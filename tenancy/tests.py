"""Schools are kept apart by Postgres schemas, picked from the Host header
(spec 0003, child 0001)."""
import asyncio
import datetime as dt
import shutil
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.http import Http404
from django.test import RequestFactory

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import DEFAULT_DB_ALIAS, connection, connections
from django.test import override_settings
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework.authtoken.models import Token
from tenant_schemas_celery.task import TenantTask, headers_with_schema

from core.asgi import application
from core.celery import app as celery_app
from core.urls import school_media
from ems.cloudinary_storage import CloudinaryImageStorage
from ems.notifications import job_group, notify, user_group

from ems.models import AuditLog, BackgroundJob, Notification, SystemSettings, User
from tenancy.features import KEYS
from tenancy.management.commands.migrate_platform import LOCK_KEY
from tenancy.migration_executor import LiveSchoolsExecutor
from tenancy.models import Domain, Plan, School, validate_feature_keys
from tenancy.slugs import RESERVED, SLUG_RE, schema_name_for
from tenancy.testcases import SchoolClient, SchoolTestCase, SocketTestMixin

OTHER_HOST = "other.localhost"


class TwoSchoolsTestCase(SchoolTestCase):
    """The test school plus a second one, ``other``, made once per run."""

    @classmethod
    def setUpClass(cls):
        connection.set_schema_to_public()
        other = School.objects.filter(slug="other").first()
        if other is None:
            other = School.objects.create(
                schema_name="school_other", slug="other", name="Other School",
                plan=Plan.objects.get(code="platform"), status=School.Status.ACTIVE,
                first_admin_name="Other Admin", first_admin_email="admin@other.localhost",
            )
            Domain.objects.create(domain=OTHER_HOST, tenant=other, is_primary=True)
            other.create_schema(check_if_exists=True, verbosity=0)
        cls.other = other
        super().setUpClass()

    def tearDown(self):
        # A request to another host leaves the connection on that school.
        connection.set_tenant(self.tenant)
        super().tearDown()

    def make_user(self, email, password="pw-123456"):
        return User.objects.create_user(
            email=email, first_name="Ada", last_name="L", password=password
        )


class IsolationTests(TwoSchoolsTestCase):
    def test_each_school_sees_only_its_own_rows(self):
        # covers: AC-1
        self.make_user("a@x.com")
        SystemSettings.objects.create(institution_name="Test School")
        with schema_context("school_other"):
            self.make_user("a@x.com")
            SystemSettings.objects.create(institution_name="Other School")
            self.assertEqual(User.objects.count(), 1)
            self.assertEqual(
                list(SystemSettings.objects.values_list("institution_name", flat=True)),
                ["Other School"],
            )

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(
            list(SystemSettings.objects.values_list("institution_name", flat=True)),
            ["Test School"],
        )

    def test_public_schema_holds_no_school_tables(self):
        # covers: AC-1
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name LIKE 'ems\\_%%'",
                [get_public_schema_name()],
            )
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_a_token_only_works_on_its_own_school(self):
        # covers: AC-2
        user = self.make_user("a@x.com")
        token = Token.objects.get(user=user).key

        mine = SchoolClient(HTTP_AUTHORIZATION=f"Token {token}")
        self.assertEqual(mine.get("/api/auth/me/").status_code, 200)

        theirs = SchoolClient(HTTP_HOST=OTHER_HOST, HTTP_AUTHORIZATION=f"Token {token}")
        self.assertEqual(theirs.get("/api/auth/me/").status_code, 401)

    def test_login_reads_the_hosts_school(self):
        # covers: AC-1
        self.make_user("a@x.com", password="pw-test-123")
        body = {"email": "a@x.com", "password": "pw-test-123"}

        self.assertEqual(
            SchoolClient().post("/api/auth/login/", body, format="json").status_code, 200
        )
        self.assertEqual(
            SchoolClient(HTTP_HOST=OTHER_HOST)
            .post("/api/auth/login/", body, format="json").status_code,
            400,
        )


class HostResolutionTests(TwoSchoolsTestCase):
    def test_an_unknown_school_is_a_generic_404(self):
        # covers: AC-7
        response = SchoolClient(HTTP_HOST="typo.localhost").get("/api/auth/me/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "School not found."})

        page = SchoolClient(HTTP_HOST="typo.localhost").get("/timetable")
        self.assertEqual(page.status_code, 404)
        self.assertEqual(page.content, b"School not found.")

    def test_a_purged_school_looks_like_an_unknown_one(self):
        # covers: AC-7
        School.objects.filter(pk=self.other.pk).update(status=School.Status.PURGED)
        response = SchoolClient(HTTP_HOST=OTHER_HOST).get("/api/auth/me/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "School not found."})

    def test_hosts_match_without_case_or_port(self):
        user = self.make_user("a@x.com")
        token = Token.objects.get(user=user).key
        client = SchoolClient(HTTP_HOST="TEST.localhost:3000", HTTP_AUTHORIZATION=f"Token {token}")
        self.assertEqual(client.get("/api/auth/me/").status_code, 200)

    def test_a_host_outside_allowed_hosts_is_a_400(self):
        response = SchoolClient(HTTP_HOST="test.evil.example").get("/api/auth/me/")
        self.assertEqual(response.status_code, 400)

    def test_healthz_answers_for_any_host(self):
        response = SchoolClient(HTTP_HOST="10.0.0.7:8000").get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")

    def test_public_hosts_serve_only_the_public_urlconf(self):
        call_command("ensure_public_domains", stdout=StringIO())
        api = SchoolClient(HTTP_HOST="api.localhost")
        self.assertEqual(api.get("/api/public/features/").status_code, 200)
        self.assertEqual(api.get("/api/auth/me/").status_code, 404)
        self.assertEqual(
            SchoolClient(HTTP_HOST="console.localhost").get("/api/auth/me/").status_code, 404
        )

    def test_the_old_admin_host_no_longer_serves_the_console(self):
        call_command("ensure_public_domains", stdout=StringIO())
        response = SchoolClient(HTTP_HOST="admin.localhost").get("/api/platform/me/")
        self.assertEqual(response.status_code, 404)


class TimezoneTests(TwoSchoolsTestCase):
    def test_a_request_runs_in_the_schools_zone_then_resets(self):
        # covers: AC-6
        School.objects.filter(pk=self.tenant.pk).update(timezone="Asia/Tokyo")
        user = self.make_user("a@x.com")
        token = Token.objects.get(user=user).key

        data = SchoolClient(HTTP_AUTHORIZATION=f"Token {token}").get("/api/auth/me/").json()

        self.assertTrue(data["created_at"].endswith("+09:00"), data["created_at"])
        self.assertEqual(timezone.get_current_timezone_name(), "UTC")


@override_settings(PLATFORM_BASE_DOMAIN="staging.ordo.app")
class PublicDomainsTests(SchoolTestCase):
    def test_public_domains_follow_the_base_domain(self):
        public = School.objects.get(schema_name=get_public_schema_name())
        Domain.objects.create(domain="old.example", tenant=public)

        call_command("ensure_public_domains", stdout=StringIO())
        call_command("ensure_public_domains", stdout=StringIO())

        self.assertEqual(
            dict(Domain.objects.filter(tenant=public).values_list("domain", "is_primary")),
            {"console.staging.ordo.app": True, "api.staging.ordo.app": False},
        )

    @override_settings(PLATFORM_CONSOLE_SUBDOMAIN="ops-7f3k")
    def test_the_console_subdomain_is_configurable(self):
        call_command("ensure_public_domains", stdout=StringIO())
        public = School.objects.get(schema_name=get_public_schema_name())
        self.assertEqual(
            set(Domain.objects.filter(tenant=public).values_list("domain", flat=True)),
            {"ops-7f3k.staging.ordo.app", "api.staging.ordo.app"},
        )


class HealthzTests(SchoolTestCase):
    def test_healthz_reports_unavailable_when_the_database_is_down(self):
        with mock.patch(
            "tenancy.middleware.connection.cursor", side_effect=Exception("db down")
        ):
            response = SchoolClient(HTTP_HOST="10.0.0.7").get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"unavailable")


class AuditOnPublicHostsTests(TwoSchoolsTestCase):
    def test_a_sign_in_attempt_on_a_public_host_writes_no_audit_row(self):
        call_command("ensure_public_domains", stdout=StringIO())
        with mock.patch.object(AuditLog.objects, "create") as create:
            response = SchoolClient(HTTP_HOST="api.localhost").post(
                "/api/auth/login/", {"email": "a@x.com", "password": "x"}, format="json"
            )
        self.assertEqual(response.status_code, 404)
        create.assert_not_called()

    def test_a_sign_in_attempt_is_audited_in_the_hosts_school_only(self):
        # covers: AC-1
        SchoolClient(HTTP_HOST=OTHER_HOST).post(
            "/api/auth/login/", {"email": "a@x.com", "password": "x"}, format="json"
        )
        connection.set_tenant(self.tenant)

        self.assertFalse(AuditLog.objects.exists())
        with schema_context("school_other"):
            self.assertEqual(
                list(AuditLog.objects.values_list("action", flat=True)),
                ["Failed sign-in attempt"],
            )


class SlugTests(SchoolTestCase):
    def test_slugs_follow_the_format_rule(self):
        for slug in ("abc", "a1b", "uni-lagos", "a" * 30):
            with self.subTest(slug=slug):
                self.assertTrue(SLUG_RE.match(slug))

    def test_slugs_outside_the_format_rule_are_refused(self):
        for slug in ("ab", "a" * 31, "-abc", "abc-", "Abc", "a_b", "a.b", "", "ünî"):
            with self.subTest(slug=slug):
                self.assertIsNone(SLUG_RE.match(slug))

    def test_the_schema_name_swaps_hyphens_for_underscores(self):
        self.assertEqual(schema_name_for("uni-lagos"), "school_uni_lagos")
        self.assertEqual(schema_name_for("acme"), "school_acme")

    def test_platform_hostnames_are_reserved(self):
        for slug in ("admin", "api", "www", "test", "demo", "localhost", "staging"):
            with self.subTest(slug=slug):
                self.assertIn(slug, RESERVED)


class FeatureKeyTests(SchoolTestCase):
    def test_known_keys_and_null_are_accepted(self):
        validate_feature_keys(None)
        validate_feature_keys([])
        validate_feature_keys(sorted(KEYS))

    def test_an_unknown_key_is_refused_by_name(self):
        with self.assertRaisesMessage(ValidationError, "Unknown feature keys: sso."):
            validate_feature_keys(["exports", "sso"])

    def test_seat_allocation_without_distribution_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "Seat allocation needs Distribution"):
            validate_feature_keys(["seat_allocation"])
        validate_feature_keys(["distribution", "seat_allocation"])

    def test_anything_but_a_list_of_strings_is_refused(self):
        for value in ("exports", {"exports": True}, [1]):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    validate_feature_keys(value)


class SeedTests(SchoolTestCase):
    def test_the_platform_plan_is_internal_and_unlimited(self):
        plan = Plan.objects.get(code="platform")
        self.assertFalse(plan.is_active)
        self.assertIsNone(plan.max_students)
        self.assertIsNone(plan.max_staff_users)
        self.assertEqual(set(plan.features), KEYS)

    def test_the_public_schema_is_an_active_school_on_the_platform_plan(self):
        public = School.objects.get(schema_name=get_public_schema_name())
        self.assertEqual(public.status, School.Status.ACTIVE)
        self.assertEqual(public.plan.code, "platform")


@override_settings(PLATFORM_BASE_DOMAIN="class-level.example")
class SchoolTestCaseTests(SchoolTestCase):
    """The base keeps TestCase's class hooks that the tenant cases skip."""

    @classmethod
    def setUpTestData(cls):
        cls.settings_row = SystemSettings.objects.create(institution_name="From setUpTestData")

    def test_class_setup_runs_inside_the_test_school(self):
        self.assertEqual(connection.schema_name, "school_test")
        self.assertTrue(SystemSettings.objects.filter(pk=self.settings_row.pk).exists())

    def test_class_level_settings_overrides_apply(self):
        from django.conf import settings

        self.assertEqual(settings.PLATFORM_BASE_DOMAIN, "class-level.example")

    def test_the_default_client_calls_the_test_school(self):
        user = User.objects.create_user(
            email="a@x.com", first_name="Ada", last_name="L", password="pw-123456"
        )
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 200)


class BehindTheProxyTests(SchoolTestCase):
    def test_the_client_ip_is_the_hop_our_proxy_added(self):
        from django.test import RequestFactory

        from tenancy.audit import client_ip

        factory = RequestFactory()
        forged = factory.get("/", HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.9")
        self.assertEqual(client_ip(forged), "203.0.113.9")
        self.assertEqual(client_ip(factory.get("/", REMOTE_ADDR="10.0.0.2")), "10.0.0.2")

    def test_absolute_urls_follow_the_forwarded_proto(self):
        request = SchoolClient(HTTP_X_FORWARDED_PROTO="https").get("/api/system/branding/")
        self.assertTrue(request.wsgi_request.is_secure())

    def test_a_forwarded_host_never_picks_the_school(self):
        # covers: AC-18
        call_command("ensure_public_domains", stdout=StringIO())
        response = SchoolClient(HTTP_X_FORWARDED_HOST="api.localhost").get(
            "/api/public/features/"
        )
        # Still the test school's urlconf, where public/ is kept for old links.
        self.assertEqual(response.wsgi_request.tenant.schema_name, "school_test")


# What ``record_school`` saw on its last run.
seen = {}


@celery_app.task(name="tenancy.tests.record_school")
def record_school(user_id):
    seen["schema"] = connection.schema_name
    seen["zone"] = timezone.get_current_timezone_name()
    if user_id is None:
        raise RuntimeError("no one to tell")
    notify([user_id], title="From a task")


class CeleryTests(TwoSchoolsTestCase):
    def setUp(self):
        super().setUp()
        seen.clear()

    def test_every_ems_task_is_tenant_aware(self):
        # covers: AC-3
        import ems.tasks  # noqa: F401  (workers import it by autodiscovery)

        ems_tasks = {n: t for n, t in celery_app.tasks.items() if n.startswith("ems.")}
        self.assertTrue(ems_tasks)
        for name, task in ems_tasks.items():
            with self.subTest(task=name):
                self.assertIsInstance(task, TenantTask)

    def test_a_task_runs_in_the_school_it_was_queued_from(self):
        # covers: AC-3
        user = self.make_user("a@x.com")
        headers = headers_with_schema(None)

        # The worker starts on the public schema; the header carries the school.
        with schema_context(get_public_schema_name()):
            record_school.apply(args=[user.pk], headers=headers)

        self.assertEqual(seen["schema"], "school_test")
        self.assertEqual(Notification.objects.filter(recipient=user).count(), 1)
        with schema_context("school_other"):
            self.assertFalse(Notification.objects.exists())

    def test_a_task_runs_in_its_schools_timezone_and_leaves_none_behind(self):
        # covers: AC-6
        School.objects.filter(pk=self.other.pk).update(timezone="Asia/Tokyo")
        with schema_context("school_other"):
            user = self.make_user("b@x.com")
            record_school.apply(args=[user.pk])

        self.assertEqual(seen["schema"], "school_other")
        self.assertEqual(seen["zone"], "Asia/Tokyo")
        self.assertEqual(timezone.get_current_timezone_name(), "UTC")

    def test_an_inline_task_gives_the_outer_task_its_zone_back(self):
        # covers: AC-6
        from celery.signals import task_postrun, task_prerun

        School.objects.filter(pk=self.tenant.pk).update(timezone="Asia/Tokyo")
        connection.set_tenant(School.objects.get(pk=self.tenant.pk))
        user = self.make_user("c@x.com")
        task_prerun.send(sender=record_school, task=record_school)
        try:
            record_school.apply(args=[user.pk])
            self.assertEqual(timezone.get_current_timezone_name(), "Asia/Tokyo")
        finally:
            task_postrun.send(sender=record_school, task=record_school)
        self.assertEqual(timezone.get_current_timezone_name(), "UTC")

    def test_a_failing_task_still_leaves_no_zone_behind(self):
        # covers: AC-6
        School.objects.filter(pk=self.other.pk).update(timezone="Asia/Tokyo")
        with schema_context("school_other"):
            result = record_school.apply(args=[None])

        self.assertTrue(result.failed())
        self.assertEqual(seen["zone"], "Asia/Tokyo")
        self.assertEqual(timezone.get_current_timezone_name(), "UTC")

    def test_a_task_queued_from_the_public_schema_runs_there_in_utc(self):
        # covers: AC-3
        with schema_context(get_public_schema_name()):
            record_school.apply(args=[None])

        self.assertEqual(seen["schema"], get_public_schema_name())
        self.assertEqual(seen["zone"], "UTC")

    def test_workers_take_one_job_at_a_time_and_store_no_results(self):
        # covers: AC-30
        self.assertEqual(celery_app.conf.worker_prefetch_multiplier, 1)
        self.assertTrue(celery_app.conf.task_acks_late)
        self.assertTrue(celery_app.conf.task_ignore_result)
        self.assertEqual(celery_app.conf.timezone, "UTC")


class SocketTests(SocketTestMixin, TwoSchoolsTestCase):
    def connect(self, path, host="test.localhost", origin=None):
        origin = origin or f"http://{host}:3000"
        ws = WebsocketCommunicator(
            application, path,
            headers=[(b"host", host.encode()), (b"origin", origin.encode())],
        )
        return ws

    def run_async(self, scenario):
        async_to_sync(scenario)()

    def token_for(self, user):
        return Token.objects.get(user=user).key

    def test_an_unknown_school_is_refused(self):
        # covers: AC-4
        async def scenario():
            ws = self.connect("/ws/notifications/?token=x", host="typo.localhost")
            connected, code = await ws.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4404)

        self.run_async(scenario)

    def test_a_purged_school_is_refused(self):
        # covers: AC-4
        School.objects.filter(pk=self.other.pk).update(status=School.Status.PURGED)

        async def scenario():
            ws = self.connect("/ws/notifications/?token=x", host=OTHER_HOST)
            connected, code = await ws.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4404)

        self.run_async(scenario)

    def test_a_suspended_school_is_refused(self):
        # covers: AC-24
        School.objects.filter(pk=self.other.pk).update(status=School.Status.SUSPENDED)

        async def scenario():
            ws = self.connect("/ws/notifications/?token=x", host=OTHER_HOST)
            connected, code = await ws.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4403)

        self.run_async(scenario)

    def assert_refused(self, path, code, **kwargs):
        async def scenario():
            ws = self.connect(path, **kwargs)
            connected, close_code = await ws.connect()
            self.assertFalse(connected)
            self.assertEqual(close_code, code)

        self.run_async(scenario)

    def test_a_public_host_has_no_sockets(self):
        # covers: AC-4
        call_command("ensure_public_domains", stdout=StringIO())
        self.assert_refused("/ws/notifications/?token=x", 4404, host="api.localhost")

    def test_the_host_is_matched_without_its_port_or_case(self):
        # covers: AC-4
        token = self.token_for(self.make_user("a@x.com"))

        async def scenario():
            ws = self.connect(
                f"/ws/notifications/?token={token}", host="TEST.localhost:8000",
                origin="http://test.localhost:3000",
            )
            connected, _ = await ws.connect()
            self.assertTrue(connected)
            self.assertEqual(await ws.receive_json_from(), {"type": "unread", "count": 0})
            await ws.disconnect()

        self.run_async(scenario)

    def test_a_socket_without_a_token_is_refused(self):
        # covers: AC-4
        self.assert_refused("/ws/notifications/", 4401)

    def test_a_job_from_another_school_is_not_found(self):
        # covers: AC-4
        token = self.token_for(self.make_user("a@x.com"))
        with schema_context("school_other"):
            BackgroundJob.objects.create(
                job_id="theirs", job_type="timetable",
                created_by=self.make_user("b@x.com"),
            )
        self.assert_refused(f"/ws/jobs/theirs/?token={token}", 4404)

    def test_someone_elses_job_is_forbidden(self):
        owner = self.make_user("a@x.com")
        BackgroundJob.objects.create(job_id="j2", job_type="timetable", created_by=owner)
        token = self.token_for(self.make_user("b@x.com"))
        self.assert_refused(f"/ws/jobs/j2/?token={token}", 4403)

    def test_a_socket_with_no_origin_is_refused(self):
        # covers: AC-4
        token = self.token_for(self.make_user("a@x.com"))

        async def scenario():
            ws = WebsocketCommunicator(
                application, f"/ws/notifications/?token={token}",
                headers=[(b"host", b"test.localhost")],
            )
            connected, _ = await ws.connect()
            self.assertFalse(connected)

        self.run_async(scenario)

    def test_a_push_names_the_school_it_was_sent_from(self):
        # covers: AC-4
        layer = get_channel_layer()
        channel = async_to_sync(layer.new_channel)()
        with schema_context("school_other"):
            user = self.make_user("b@x.com")
            async_to_sync(layer.group_add)(user_group("school_other", user.pk), channel)
            with self.captureOnCommitCallbacks() as callbacks:
                notify([user], title="Theirs")
        # The commit lands after the connection has moved on.
        for callback in callbacks:
            callback()

        async def receive():
            return await asyncio.wait_for(layer.receive(channel), timeout=1)

        message = async_to_sync(receive)()
        self.assertEqual(message["data"]["title"], "Theirs")

    def test_a_token_only_opens_a_socket_on_its_own_school(self):
        # covers: AC-4
        token = self.token_for(self.make_user("a@x.com"))

        async def scenario():
            ws = self.connect(f"/ws/notifications/?token={token}", host=OTHER_HOST)
            connected, code = await ws.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4401)

        self.run_async(scenario)

    def test_a_notification_reaches_only_its_own_schools_socket(self):
        # covers: AC-4
        mine = User.objects.create_user(email="a@x.com", password="x", id=777)
        with schema_context("school_other"):
            theirs = User.objects.create_user(email="b@x.com", password="x", id=777)
            their_token = self.token_for(theirs)
        my_token = self.token_for(mine)

        async def scenario():
            ws_mine = self.connect(f"/ws/notifications/?token={my_token}")
            ws_theirs = self.connect(
                f"/ws/notifications/?token={their_token}", host=OTHER_HOST
            )
            self.assertTrue((await ws_mine.connect())[0])
            self.assertTrue((await ws_theirs.connect())[0])
            await ws_mine.receive_json_from()
            await ws_theirs.receive_json_from()

            await get_channel_layer().group_send(
                user_group("school_test", 777),
                {"type": "notification.new", "data": {"title": "Only A"}},
            )
            self.assertEqual(
                await ws_mine.receive_json_from(),
                {"type": "notification", "notification": {"title": "Only A"}},
            )
            self.assertTrue(await ws_theirs.receive_nothing())
            await ws_mine.disconnect()
            await ws_theirs.disconnect()

        self.run_async(scenario)

    def test_job_progress_reaches_only_its_own_schools_socket(self):
        # covers: AC-4
        mine = self.make_user("a@x.com")
        BackgroundJob.objects.create(job_id="same", job_type="timetable", created_by=mine)
        with schema_context("school_other"):
            theirs = self.make_user("b@x.com")
            BackgroundJob.objects.create(
                job_id="same", job_type="timetable", created_by=theirs
            )
            their_token = self.token_for(theirs)
        my_token = self.token_for(mine)

        async def scenario():
            ws_mine = self.connect(f"/ws/jobs/same/?token={my_token}")
            ws_theirs = self.connect(f"/ws/jobs/same/?token={their_token}", host=OTHER_HOST)
            self.assertTrue((await ws_mine.connect())[0])
            self.assertTrue((await ws_theirs.connect())[0])
            await ws_mine.receive_json_from()
            await ws_theirs.receive_json_from()

            await get_channel_layer().group_send(
                job_group("school_test", "same"),
                {"type": "job.progress", "data": {"progress": 50}},
            )
            self.assertEqual(await ws_mine.receive_json_from(), {"progress": 50})
            self.assertTrue(await ws_theirs.receive_nothing())
            await ws_mine.disconnect()
            await ws_theirs.disconnect()

        self.run_async(scenario)

    def test_sync_senders_name_the_current_school(self):
        # covers: AC-4
        user = self.make_user("a@x.com")
        layer = get_channel_layer()
        channel = async_to_sync(layer.new_channel)()
        async_to_sync(layer.group_add)(job_group("school_test", "j1"), channel)

        BackgroundJob.objects.create(job_id="j1", job_type="timetable", created_by=user)

        message = async_to_sync(layer.receive)(channel)
        self.assertEqual(message["data"]["job_id"], "j1")

    def test_only_school_origins_may_connect(self):
        # covers: AC-4
        token = self.token_for(self.make_user("a@x.com"))
        cases = {
            "http://test.localhost:3000": True,
            "https://other.localhost": True,
            "https://evil.example": False,
            "http://localhost.evil.example": False,
            "http://a.b.localhost": False,
        }

        async def attempt(origin):
            ws = self.connect(f"/ws/notifications/?token={token}", origin=origin)
            connected, _ = await ws.connect()
            if connected:
                await ws.disconnect()
            return connected

        for origin, allowed in cases.items():
            with self.subTest(origin=origin):
                self.assertEqual(async_to_sync(attempt)(origin), allowed)

    @override_settings(PLATFORM_BASE_DOMAIN="ordo.app", DEBUG=False)
    def test_the_origin_pattern_follows_the_base_domain(self):
        # covers: AC-4
        from tenancy.ws import SchoolOriginValidator
        from urllib.parse import urlparse

        validator = SchoolOriginValidator(None)
        self.assertTrue(validator.valid_origin(urlparse("https://unilag.ordo.app")))
        self.assertFalse(validator.valid_origin(urlparse("https://unilag.ordoXapp")))
        self.assertFalse(validator.valid_origin(urlparse("http://demo.localhost:3000")))
        self.assertFalse(validator.valid_origin(None))


class MediaTests(TwoSchoolsTestCase):
    def setUp(self):
        super().setUp()
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.enterContext(override_settings(MEDIA_ROOT=self.root))

    def media_request(self, school):
        # Called directly: the client's error page rendering is broken on
        # Django 5.0 under Python 3.14.
        request = RequestFactory().get("/media/")
        request.tenant = school
        return request

    def test_a_logo_is_stored_and_served_inside_its_own_school(self):
        # covers: AC-5
        from django.core.files.storage import default_storage

        logo_field = SystemSettings._meta.get_field("logo")
        self.enterContext(mock.patch.object(logo_field, "storage", default_storage))
        row = SystemSettings.objects.create(institution_name="Test School")
        row.logo.save("logo.png", ContentFile(b"png"), save=True)

        self.assertTrue((self.root / "school_test" / row.logo.name).is_file())
        self.assertEqual(SchoolClient().get(f"/media/{row.logo.name}").status_code, 200)
        with self.assertRaises(Http404):
            school_media(self.media_request(self.other), row.logo.name)

    def test_media_cannot_escape_the_schools_folder(self):
        # covers: AC-5
        (self.root / "school_other").mkdir()
        (self.root / "school_other" / "secret.txt").write_text("theirs")

        with self.assertRaises((Http404, SuspiciousFileOperation)):
            school_media(self.media_request(self.tenant), "../school_other/secret.txt")

    def test_one_storage_follows_the_current_school(self):
        # covers: AC-5
        from ems.cloudinary_storage import SchoolFileSystemStorage

        storage = SchoolFileSystemStorage()
        mine = storage.path("branding/a.png")
        with schema_context("school_other"):
            theirs = storage.path("branding/a.png")

        self.assertEqual(mine, str(self.root / "school_test" / "branding" / "a.png"))
        self.assertEqual(theirs, str(self.root / "school_other" / "branding" / "a.png"))

    def test_the_public_hosts_have_no_media_route(self):
        # covers: AC-5
        from django.urls import Resolver404, resolve

        with self.assertRaises(Resolver404):
            resolve("/media/branding/logo.png", urlconf="core.urls_public")

    def test_a_name_already_prefixed_is_not_prefixed_again(self):
        # covers: AC-5
        def upload(content, public_id, **kwargs):
            return {"public_id": public_id, "format": "png"}

        storage = CloudinaryImageStorage()
        with mock.patch("cloudinary.uploader.upload", side_effect=upload):
            name = storage._save("school_test/branding/logo.png", ContentFile(b"png"))

        self.assertEqual(name, "school_test/branding/logo.png")

    def test_cloudinary_ids_start_with_the_schema(self):
        # covers: AC-5
        def upload(content, public_id, **kwargs):
            return {"public_id": public_id, "format": "png"}

        storage = CloudinaryImageStorage()
        with mock.patch("cloudinary.uploader.upload", side_effect=upload):
            mine = storage.save("branding/logo.png", ContentFile(b"png"))
            with schema_context("school_other"):
                theirs = storage.save("branding/logo.png", ContentFile(b"png"))

        self.assertTrue(mine.startswith("school_test/branding/logo_"))
        self.assertTrue(theirs.startswith("school_other/branding/logo_"))


class LocalTimeExportTests(SchoolTestCase):
    def test_exported_times_are_in_the_schools_zone(self):
        # covers: AC-6
        admin = User.objects.create_superuser(email="a@x.com", password="x")
        officer = User.objects.create_user(email="b@x.com", password="x")
        User.objects.filter(pk=officer.pk).update(
            created_at=dt.datetime(2026, 1, 1, 20, 0, tzinfo=dt.timezone.utc)
        )
        client = SchoolClient()
        client.force_authenticate(admin)

        response = client.get("/api/users/export/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("2026-01-02 05:00:00", response.content.decode())

    def setUp(self):
        super().setUp()
        School.objects.filter(pk=self.tenant.pk).update(timezone="Asia/Tokyo")

    def test_the_audit_export_uses_the_schools_zone(self):
        # covers: AC-6
        admin = User.objects.create_superuser(email="a@x.com", password="x")
        log = AuditLog.objects.create(action="Signed in", user=admin)
        AuditLog.objects.filter(pk=log.pk).update(
            created_at=dt.datetime(2026, 1, 1, 20, 0, tzinfo=dt.timezone.utc)
        )
        client = SchoolClient()
        client.force_authenticate(admin)

        response = client.get("/api/audit-logs/export/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("2026-01-02 05:00:00", response.content.decode())

    def test_the_broadsheet_is_stamped_in_the_schools_zone(self):
        # covers: AC-6
        from ems.broadsheet import TimetableBroadSheet

        frozen = dt.datetime(2026, 1, 1, 20, 0, tzinfo=dt.timezone.utc)
        sheet = TimetableBroadSheet()
        with mock.patch("django.utils.timezone.now", return_value=frozen):
            with timezone.override("Asia/Tokyo"):
                sheet.create_header_section("1ST", "2025/2026")

        stamps = [c.value for row in sheet.ws.iter_rows() for c in row
                  if isinstance(c.value, str) and c.value.startswith("Generated on")]
        self.assertEqual(stamps, ["Generated on: January 02, 2026 at 05:00 AM"])


def migrated_schemas():
    """Patch the per schema migration and record which schemas it ran on."""
    ran = []
    patcher = mock.patch(
        "django_tenants.migration_executors.standard.run_migrations",
        side_effect=lambda args, options, codename, schema_name, **kw: ran.append(schema_name),
    )
    return patcher, ran


class LiveSchoolsExecutorTests(SchoolTestCase):
    def setUp(self):
        super().setUp()
        connection.set_schema_to_public()
        plan = Plan.objects.get(code="platform")
        for status in School.Status:
            slug = f"st-{status.value.replace('_', '-')}"
            School.objects.create(
                schema_name=schema_name_for(slug), slug=slug, name=slug, plan=plan,
                status=status, first_admin_name="A", first_admin_email="a@x.com",
            )

    def tearDown(self):
        connection.set_tenant(self.tenant)
        super().tearDown()

    def test_tenant_migrations_skip_schools_that_are_not_live(self):
        # covers: AC-8
        patcher, ran = migrated_schemas()
        with patcher:
            call_command("migrate_schemas", tenant=True, interactive=False, verbosity=0)

        self.assertIn("school_test", ran)
        self.assertTrue(
            {"school_st_active", "school_st_suspended", "school_st_pending_deletion"} <= set(ran)
        )
        for skipped in ("school_st_provisioning", "school_st_failed", "school_st_purged"):
            self.assertNotIn(skipped, ran)

    def test_an_explicit_schema_migrates_even_while_provisioning(self):
        # Provisioning's create_schema passes --schema for a school not yet live.
        patcher, ran = migrated_schemas()
        executor = LiveSchoolsExecutor([], {"schema_name": "school_st_provisioning"})
        with patcher:
            executor.run_migrations(tenants=["school_st_provisioning"])

        self.assertEqual(ran, ["school_st_provisioning"])


def migrate_lock_is_held():
    """True when another session holds the deploy's migrate lock."""
    probe = connections.create_connection(DEFAULT_DB_ALIAS)
    try:
        with probe.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(hashtext(%s))", [LOCK_KEY])
            got = cursor.fetchone()[0]
            if got:
                cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", [LOCK_KEY])
        return not got
    finally:
        probe.close()


class MigratePlatformTests(SchoolTestCase):
    def run_steps(self, steps, fail_on=None):
        def step(name, **options):
            steps.append((name, "tenant" if options.get("tenant") else
                          "shared" if options.get("shared") else None, migrate_lock_is_held()))
            if (name, options.get("tenant")) == fail_on:
                raise RuntimeError("migration 0042 failed")

        with mock.patch(
            "tenancy.management.commands.migrate_platform.call_command", side_effect=step
        ):
            call_command("migrate_platform", stdout=StringIO())

    def test_shared_then_tenant_then_domains_all_under_the_lock(self):
        # covers: AC-8
        steps = []
        self.run_steps(steps)

        self.assertEqual(steps, [
            ("migrate_schemas", "shared", True),
            ("migrate_schemas", "tenant", True),
            ("ensure_public_domains", None, True),
        ])
        self.assertFalse(migrate_lock_is_held())

    def test_a_failing_school_migration_stops_the_run_and_frees_the_lock(self):
        # covers: AC-8
        steps = []
        with self.assertRaisesMessage(RuntimeError, "migration 0042 failed"):
            self.run_steps(steps, fail_on=("migrate_schemas", True))

        self.assertEqual([name for name, *_ in steps], ["migrate_schemas", "migrate_schemas"])
        self.assertFalse(migrate_lock_is_held())

