"""Creating schools from the console: provisioning, retry and the first
admin's invite (spec 0003, child 0004)."""
from datetime import timedelta
from smtplib import SMTPException
from unittest import mock

from django.conf import settings
from django.core import mail
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from django_tenants.utils import schema_context

from ems import invites
from ems.models import Invite, SystemSettings, User
from tenancy.models import Domain, Plan, PlatformAdmin, PlatformAuditLog, School
from tenancy.tasks import (
    STALE_PROVISIONING_ERROR,
    fail_stale_provisioning,
    provision_school,
    send_email,
)
from tenancy.test_platform_auth import PlatformTestCase


def run_inline(task):
    return mock.patch.object(task, "delay", side_effect=lambda *args: task.apply(args=args))


class SchoolConsoleTestCase(PlatformTestCase):
    def setUp(self):
        super().setUp()
        self.owner_admin = self.make_admin()
        self.owner = self.sign_in(self.owner_admin)

    def tearDown(self):
        connection.set_tenant(self.tenant)
        super().tearDown()

    def payload(self, **overrides):
        return {
            "name": "University of Lagos",
            "slug": "unilag",
            "plan": "starter",
            "first_admin_name": "Ada Obi",
            "first_admin_email": "Ada@Unilag.edu.ng",
            "contact_email": "exams@unilag.edu.ng",
            "country": "ng",
            "timezone": "Africa/Lagos",
            **overrides,
        }

    def create(self, client=None, **overrides):
        """POST a school with provisioning and email running inline."""
        with run_inline(provision_school), run_inline(send_email), \
                self.captureOnCommitCallbacks(execute=True):
            return (client or self.owner).post(
                "/api/platform/schools/", self.payload(**overrides), format="json"
            )

    def token_from_last_email(self):
        return mail.outbox[-1].body.split("/invite/")[1].split()[0]


class CreateSchoolTests(SchoolConsoleTestCase):
    def test_creating_a_school_provisions_it_and_invites_its_first_admin(self):
        # covers: AC-19, AC-20, AC-21
        response = self.create()
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data["status"], "provisioning")
        self.assertEqual(response.data["domain"], "unilag.localhost")

        school = School.objects.get(slug="unilag")
        self.assertEqual(school.status, School.Status.ACTIVE)
        self.assertTrue(school.was_ever_active)
        self.assertEqual(school.schema_name, "school_unilag")
        self.assertEqual(school.country, "NG")
        self.assertEqual(school.created_by, self.owner_admin)
        self.assertIsNotNone(school.provisioning_started_at)
        with schema_context(school.schema_name):
            settings_row = SystemSettings.objects.get()
            self.assertEqual(settings_row.institution_name, "University of Lagos")
            self.assertEqual(settings_row.contact_email, "exams@unilag.edu.ng")
            invite = Invite.objects.get()
            self.assertEqual(invite.email, "ada@unilag.edu.ng")
            self.assertEqual(invite.role, User.Role.SUPER_ADMIN)
            self.assertIsNone(invite.invited_by)

        email = mail.outbox[-1]
        self.assertEqual(email.to, ["ada@unilag.edu.ng"])
        self.assertIn("University of Lagos", email.subject)
        self.assertIn(f"{settings.PLATFORM_URL_SCHEME}://unilag.localhost/invite/", email.body)
        self.assertEqual(email.alternatives[0][1], "text/html")
        token = self.token_from_last_email()
        self.assertEqual(invites.hash_token(token), invite.token_hash)
        self.assertEqual(
            list(
                PlatformAuditLog.objects.filter(school=school)
                .order_by("id")
                .values_list("action", flat=True)
            ),
            ["school_created", "school_provisioned"],
        )

    def test_slugs_must_be_well_formed_unreserved_and_unused(self):
        # covers: AC-19
        School.objects.create(
            schema_name="school_gone", slug="gone", name="Gone",
            plan=Plan.objects.get(code="starter"), status=School.Status.PURGED,
            first_admin_name="X", first_admin_email="x@gone.test",
        )
        for slug in ("Bad_Slug", "ab", "-lead", "admin", "console", "test", "gone"):
            with self.subTest(slug=slug):
                response = self.create(slug=slug)
                self.assertEqual(response.status_code, 400)
                self.assertIn("slug", response.data["errors"])
        self.assertFalse(School.objects.filter(status=School.Status.PROVISIONING).exists())

    def test_a_custom_console_subdomain_is_reserved_too(self):
        with override_settings(PLATFORM_CONSOLE_SUBDOMAIN="ops-7f3k"):
            response = self.create(slug="ops-7f3k")
        self.assertEqual(response.status_code, 400)
        self.assertIn("slug", response.data["errors"])

    def test_an_inactive_plan_or_unknown_zone_is_a_field_error(self):
        # covers: AC-19
        response = self.create(plan="platform", timezone="Mars/Olympus")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.data["errors"]), {"plan", "timezone"})

    def test_support_cannot_create_a_school(self):
        # covers: AC-16
        support = self.sign_in(self.make_admin("support@ordo.app", PlatformAdmin.Role.SUPPORT))
        self.assertEqual(self.create(client=support).status_code, 403)

    def test_a_failure_is_kept_and_retry_rebuilds_from_scratch(self):
        # covers: AC-20
        real_create_schema = School.create_schema

        def half_then_fail(school, *args, **kwargs):
            real_create_schema(school, *args, **kwargs)
            raise RuntimeError("migration boom")

        with mock.patch.object(School, "create_schema", half_then_fail):
            self.create()
        school = School.objects.get(slug="unilag")
        self.assertEqual(school.status, School.Status.FAILED)
        self.assertEqual(school.provisioning_error, "migration boom")
        self.assertTrue(
            PlatformAuditLog.objects.filter(
                school=school, action="school_provisioning_failed"
            ).exists()
        )

        # Live, the failed attempt committed long before anyone retries. Here
        # it shares the test's transaction, so fire its deferred FK checks
        # now, or Postgres refuses to drop tables with pending trigger events.
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        with run_inline(provision_school), run_inline(send_email), \
                self.captureOnCommitCallbacks(execute=True):
            response = self.owner.post(f"/api/platform/schools/{school.pk}/retry/")
        self.assertEqual(response.status_code, 202, response.data)
        school.refresh_from_db()
        self.assertEqual(school.status, School.Status.ACTIVE)
        self.assertEqual(school.provisioning_error, "")
        with schema_context(school.schema_name):
            # Rebuilt, not resumed: one settings row and one invite.
            self.assertEqual(SystemSettings.objects.count(), 1)
            self.assertEqual(Invite.objects.count(), 1)

    def test_retry_only_works_on_a_failed_school(self):
        # covers: AC-20
        self.create()
        school = School.objects.get(slug="unilag")
        response = self.owner.post(f"/api/platform/schools/{school.pk}/retry/")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["detail"], "Cannot retry a school that is active.")

    def test_a_stuck_provisioning_is_marked_failed_after_35_minutes(self):
        # covers: AC-20
        plan = Plan.objects.get(code="starter")
        now = timezone.now()

        def school(slug, started):
            return School.objects.create(
                schema_name=f"school_{slug}", slug=slug, name=slug, plan=plan,
                first_admin_name="X", first_admin_email=f"x@{slug}.test",
                provisioning_started_at=started,
            )

        stuck = school("stuck", now - timedelta(minutes=36))
        busy = school("busy", now - timedelta(minutes=10))
        fail_stale_provisioning.apply()
        stuck.refresh_from_db()
        busy.refresh_from_db()
        self.assertEqual(stuck.status, School.Status.FAILED)
        self.assertEqual(stuck.provisioning_error, STALE_PROVISIONING_ERROR)
        self.assertEqual(busy.status, School.Status.PROVISIONING)


class SchoolListTests(SchoolConsoleTestCase):
    def test_the_list_holds_schools_only_and_filters(self):
        self.create()
        response = self.owner.get("/api/platform/schools/")
        self.assertEqual(response.status_code, 200)
        slugs = [row["slug"] for row in response.data["results"]]
        self.assertIn("unilag", slugs)
        self.assertNotIn("public", slugs)
        self.assertEqual(
            self.owner.get("/api/platform/schools/?status=failed").data["count"], 0
        )
        found = self.owner.get("/api/platform/schools/?q=lagos").data["results"]
        self.assertEqual([row["slug"] for row in found], ["unilag"])

    def test_detail_says_whether_the_first_admin_joined(self):
        self.create()
        school = School.objects.get(slug="unilag")
        data = self.owner.get(f"/api/platform/schools/{school.pk}/").data
        self.assertFalse(data["first_admin"]["joined"])
        self.assertIsNotNone(data["first_admin"]["invite_expires_at"])
        self.assertEqual(data["domains"], [{"domain": "unilag.localhost", "is_primary": True}])

    def test_plans_list_offered_plans_first(self):
        codes = [p["code"] for p in self.owner.get("/api/platform/plans/").data]
        self.assertEqual(codes[0], "starter")
        self.assertIn("platform", codes)


class ResendFirstAdminInviteTests(SchoolConsoleTestCase):
    def setUp(self):
        super().setUp()
        self.create()
        self.school = School.objects.get(slug="unilag")

    def resend(self, client=None):
        with run_inline(send_email), self.captureOnCommitCallbacks(execute=True):
            return (client or self.owner).post(
                f"/api/platform/schools/{self.school.pk}/resend-invite/"
            )

    def test_resend_replaces_the_link(self):
        # covers: AC-22
        old = self.token_from_last_email()
        support = self.sign_in(self.make_admin("support@ordo.app", PlatformAdmin.Role.SUPPORT))
        self.assertEqual(self.resend(support).status_code, 202)
        new = self.token_from_last_email()
        self.assertNotEqual(old, new)
        with schema_context(self.school.schema_name):
            with self.assertRaises(invites.InviteGone):
                invites.find(old)
            self.assertEqual(invites.find(new).email, "ada@unilag.edu.ng")
        self.assertTrue(
            PlatformAuditLog.objects.filter(school=self.school, action="school_invite_resent").exists()
        )

    def test_resend_refuses_once_the_admin_joined(self):
        with schema_context(self.school.schema_name):
            User.objects.create_user(email="ada@unilag.edu.ng", password="x-123456789")
        response = self.resend()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["detail"], "The first admin has already joined.")

    def test_the_list_says_whether_the_first_admin_joined(self):
        def joined():
            rows = self.owner.get("/api/platform/schools/").data["results"]
            return next(row["first_admin_joined"] for row in rows if row["slug"] == "unilag")

        self.assertIs(joined(), False)
        with schema_context(self.school.schema_name):
            User.objects.create_user(email="ada@unilag.edu.ng", password="x-123456789")
        self.assertIs(joined(), True)

    def test_resend_refuses_unless_the_school_is_active(self):
        School.objects.filter(pk=self.school.pk).update(status=School.Status.SUSPENDED)
        self.assertEqual(self.resend().status_code, 409)


class EmailTests(SchoolConsoleTestCase):
    def test_a_failing_send_is_retried_three_times_and_the_invite_stays_valid(self):
        # covers: AC-21
        self.create()
        token = self.token_from_last_email()
        school = School.objects.get(slug="unilag")
        with mock.patch(
            "tenancy.tasks.EmailMultiAlternatives.send", side_effect=SMTPException("down")
        ) as send, self.assertLogs("tenancy.tasks", "ERROR") as logs:
            send_email.apply(
                args=("ada@unilag.edu.ng", "Subject", "school_invite", {"url": "x"})
            )
        self.assertEqual(send.call_count, 4)
        self.assertIn("Giving up emailing 'ada@unilag.edu.ng'", logs.output[0])
        with schema_context(school.schema_name):
            self.assertEqual(invites.find(token).email, "ada@unilag.edu.ng")

    def test_the_domain_row_is_saved_with_the_school(self):
        self.create()
        school = School.objects.get(slug="unilag")
        self.assertTrue(Domain.objects.filter(tenant=school, is_primary=True).exists())
