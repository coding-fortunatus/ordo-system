"""Ordo support sessions (spec 0003, child 0006)."""
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlparse

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.db import connection
from django.utils import timezone
from django_tenants.utils import schema_context

from core.asgi import application
from ems.models import AuditLog, Notification, User
from ems.notifications import notify
from tenancy import limits
from tenancy.models import Domain, Plan, PlatformAdmin, PlatformAuditLog, School, SupportSession
from tenancy.support import SUPPORT_SESSION_TTL, TICKET_TTL, shadow_email
from tenancy.tasks import close_expired_support_sessions
from tenancy.test_school_lifecycle import SchoolConsoleTestCase
from tenancy.testcases import SCHOOL_TEST_HOST, SchoolClient, SocketTestMixin


def later(delta):
    return mock.patch("django.utils.timezone.now", return_value=timezone.now() + delta)


class SupportTestCase(SchoolConsoleTestCase):
    def setUp(self):
        super().setUp()
        self.school = self.tenant
        with schema_context(self.school.schema_name):
            self.staff = User.objects.create_user(
                email="sa@test.localhost", password="x-123456789",
                role=User.Role.SUPER_ADMIN,
            )

    def start(self, client=None, school=None, reason="Can't publish the timetable"):
        return (client or self.owner).post(
            f"/api/platform/schools/{(school or self.school).pk}/support-sessions/",
            {"reason": reason},
            format="json",
        )

    def ticket(self, response):
        return parse_qs(urlparse(response.data["url"]).query)["ticket"][0]

    def redeem(self, ticket):
        return SchoolClient().post("/api/support/redeem/", {"ticket": ticket}, format="json")

    def enter(self, client=None):
        """Start and redeem a session. Returns a client holding the support token."""
        response = self.redeem(self.ticket(self.start(client)))
        self.assertEqual(response.status_code, 200, response.data)
        self.token = response.data["token"]
        return SchoolClient(HTTP_AUTHORIZATION=f"Token {self.token}")

    def shadow(self, admin=None):
        with schema_context(self.school.schema_name):
            return User.all_objects.get(email=shadow_email((admin or self.owner_admin).pk))

    def actions(self):
        return list(
            PlatformAuditLog.objects.filter(school=self.school)
            .order_by("id")
            .values_list("action", flat=True)
        )


class StartAndRedeemTests(SupportTestCase):
    def test_starting_returns_a_one_minute_link_on_the_school_host(self):
        # covers: AC-32
        support = self.sign_in(self.make_admin("help@ordo.app", PlatformAdmin.Role.SUPPORT))
        response = self.start(support)
        self.assertEqual(response.status_code, 201, response.data)
        url = urlparse(response.data["url"])
        self.assertEqual((url.netloc, url.path), (SCHOOL_TEST_HOST, "/support"))
        session = SupportSession.objects.get(pk=response.data["session_id"])
        self.assertNotEqual(session.ticket_hash, self.ticket(response))
        self.assertAlmostEqual(
            session.ticket_expires_at, timezone.now() + TICKET_TTL, delta=timedelta(seconds=5)
        )

    def test_a_reason_is_required(self):
        response = self.start(reason="  ")
        self.assertEqual(response.status_code, 400)
        self.assertIn("reason", response.data["errors"])

    def test_only_active_or_suspended_schools(self):
        # covers: AC-32
        School.objects.filter(pk=self.school.pk).update(status=School.Status.PENDING_DELETION)
        self.assertEqual(self.start().status_code, 409)

    def test_redeeming_gives_a_hidden_super_admin_token_for_an_hour(self):
        # covers: AC-32, AC-34
        client = self.enter()
        user = self.shadow()
        self.assertEqual(user.role, User.Role.SUPER_ADMIN)
        self.assertEqual(user.first_name, "Ordo support")
        self.assertFalse(user.has_usable_password())
        self.assertAlmostEqual(
            user.support_expires_at, timezone.now() + SUPPORT_SESSION_TTL,
            delta=timedelta(seconds=5),
        )

        me = client.get("/api/auth/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["email"], user.email)
        school = client.get("/api/school/")
        self.assertEqual(school.data["support"]["active"], True)
        self.assertEqual(school.data["support"]["admin_name"], self.owner_admin.name)

        with schema_context(self.school.schema_name):
            started = AuditLog.objects.get(object_type="support")
        self.assertEqual(started.action, "Ordo support session started: Can't publish the timetable")
        self.assertEqual(started.user_email, user.email)
        self.assertIn("support_started", self.actions())

    def test_a_used_ticket_is_gone(self):
        # covers: AC-32
        ticket = self.ticket(self.start())
        self.assertEqual(self.redeem(ticket).status_code, 200)
        again = self.redeem(ticket)
        self.assertEqual(again.status_code, 410)
        self.assertEqual(again.data["detail"], "This support link has expired.")

    def test_a_ticket_older_than_a_minute_is_gone(self):
        # covers: AC-32
        ticket = self.ticket(self.start())
        with later(TICKET_TTL + timedelta(seconds=1)):
            self.assertEqual(self.redeem(ticket).status_code, 410)

    def test_another_schools_ticket_is_gone_here(self):
        # covers: AC-32
        other = School.objects.create(
            schema_name="school_other", slug="other", name="Other",
            plan=Plan.objects.get(code="platform"), status=School.Status.ACTIVE,
            first_admin_name="O", first_admin_email="o@other.localhost",
        )
        Domain.objects.create(tenant=other, domain="other.localhost", is_primary=True)
        connection.set_tenant(self.school)
        self.assertEqual(self.redeem(self.ticket(self.start(school=other))).status_code, 410)

    def test_a_second_session_reuses_the_shadow_user_and_replaces_the_first(self):
        first = self.enter()
        second = self.enter()
        with schema_context(self.school.schema_name):
            self.assertEqual(
                User.all_objects.filter(support_expires_at__isnull=False).count(), 1
            )
        self.assertEqual(first.get("/api/auth/me/").status_code, 401)
        self.assertEqual(second.get("/api/auth/me/").status_code, 200)
        self.assertEqual(
            SupportSession.objects.filter(ended_at__isnull=True, started_at__isnull=False).count(), 1
        )


class ExpiryAndEndTests(SupportTestCase):
    def test_the_token_stops_working_when_the_hour_is_up(self):
        # covers: AC-33
        client = self.enter()
        with later(SUPPORT_SESSION_TTL + timedelta(seconds=1)):
            response = client.get("/api/auth/me/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], "support_expired")

    def test_ending_from_the_banner_revokes_at_once(self):
        # covers: AC-33, AC-34
        client = self.enter()
        self.assertEqual(client.post("/api/support/end/").status_code, 204)
        self.assertEqual(client.get("/api/auth/me/").status_code, 401)
        self.assertIsNotNone(SupportSession.objects.get().ended_at)
        self.assertEqual(self.actions()[-1], "support_ended")
        with schema_context(self.school.schema_name):
            ended = AuditLog.objects.filter(path="/api/support/end/").get()
        self.assertEqual(ended.action, "[Ordo support] Ended support session")

    def test_ending_from_the_console_revokes_at_once(self):
        # covers: AC-33
        client = self.enter()
        session = SupportSession.objects.get()
        url = f"/api/platform/support-sessions/{session.pk}/end/"
        self.assertEqual(self.owner.post(url).status_code, 204)
        self.assertEqual(client.get("/api/auth/me/").status_code, 401)
        self.assertEqual(self.owner.post(url).status_code, 409)

    def test_only_an_owner_or_the_sessions_admin_may_end_it_from_the_console(self):
        self.enter()
        session = SupportSession.objects.get()
        other = self.sign_in(self.make_admin("help@ordo.app", PlatformAdmin.Role.SUPPORT))
        url = f"/api/platform/support-sessions/{session.pk}/end/"
        self.assertEqual(other.post(url).status_code, 403)

    def test_a_school_user_cannot_end_a_support_session(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post("/api/support/end/").status_code, 403)

    def test_deactivating_an_admin_ends_their_sessions(self):
        support_admin = self.make_admin("help@ordo.app", PlatformAdmin.Role.SUPPORT)
        client = self.enter(self.sign_in(support_admin))
        response = self.owner.patch(
            f"/api/platform/admins/{support_admin.pk}/", {"is_active": False}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(client.get("/api/auth/me/").status_code, 401)
        detail = PlatformAuditLog.objects.filter(action="support_ended").get().detail
        self.assertEqual(detail["reason"], "admin_deactivated")

    def test_signing_an_admin_out_ends_their_sessions(self):
        # Security audit F13: sign-out must cut the school support token too.
        support_admin = self.make_admin("help@ordo.app", PlatformAdmin.Role.SUPPORT)
        client = self.enter(self.sign_in(support_admin))
        response = self.owner.post(f"/api/platform/admins/{support_admin.pk}/sign-out/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(client.get("/api/auth/me/").status_code, 401)
        detail = PlatformAuditLog.objects.filter(action="support_ended").get().detail
        self.assertEqual(detail["reason"], "admin_signed_out")

    def test_resetting_an_admins_2fa_ends_their_sessions(self):
        # Security audit F13.
        support_admin = self.make_admin("help@ordo.app", PlatformAdmin.Role.SUPPORT)
        client = self.enter(self.sign_in(support_admin))
        response = self.owner.post(f"/api/platform/admins/{support_admin.pk}/reset-2fa/")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(client.get("/api/auth/me/").status_code, 401)

    def test_the_sweep_records_sessions_that_ran_out(self):
        self.enter()
        with later(SUPPORT_SESSION_TTL + timedelta(minutes=1)):
            close_expired_support_sessions()
        session = SupportSession.objects.get()
        self.assertEqual(session.ended_at, session.expires_at)
        entry = PlatformAuditLog.objects.filter(action="support_ended").get()
        self.assertEqual((entry.admin, entry.detail["reason"]), (self.owner_admin, "expired"))

    def test_the_console_lists_the_schools_sessions(self):
        self.enter()
        response = self.owner.get(f"/api/platform/schools/{self.school.pk}/support-sessions/")
        self.assertEqual(response.status_code, 200)
        [row] = response.data
        self.assertEqual((row["state"], row["admin"]["id"]), ("active", self.owner_admin.pk))


class TrailTests(SupportTestCase):
    def test_every_change_made_in_support_is_tagged(self):
        # covers: AC-34
        client = self.enter()
        client.patch("/api/system/settings/", {"institution_name": "Test"}, format="json")
        with schema_context(self.school.schema_name):
            row = AuditLog.objects.filter(path="/api/system/settings/").get()
        self.assertTrue(row.action.startswith("[Ordo support] "), row.action)
        self.assertEqual(row.user_email, self.shadow().email)


class HiddenUserTests(SupportTestCase):
    def test_support_users_are_out_of_lists_caps_and_notifications(self):
        # covers: AC-35
        client = self.enter()
        shadow = self.shadow()
        with schema_context(self.school.schema_name):
            self.assertEqual(limits.staff_in_use(), 1)
            notify([shadow, self.staff], title="Hello")
            self.assertEqual(
                list(Notification.objects.values_list("recipient_id", flat=True)), [self.staff.pk]
            )
        emails = [u["email"] for u in client.get("/api/users/").data["results"]]
        self.assertNotIn(shadow.email, emails)
        self.assertIn(self.staff.email, emails)
        usage = client.get("/api/school/").data["usage"]
        self.assertEqual(usage["staff_users"], 1)


class SuspendedSchoolTests(SupportTestCase):
    def test_support_gets_into_a_suspended_school_and_nobody_else_does(self):
        # covers: AC-24, AC-32
        School.objects.filter(pk=self.school.pk).update(status=School.Status.SUSPENDED)
        client = self.enter()
        self.assertEqual(client.get("/api/auth/me/").status_code, 200)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 403)
        with later(SUPPORT_SESSION_TTL + timedelta(seconds=1)):
            self.assertEqual(client.get("/api/auth/me/").status_code, 403)


class SupportSocketTests(SocketTestMixin, SupportTestCase):
    def connect(self, token):
        return WebsocketCommunicator(
            application,
            f"/ws/notifications/?token={token}",
            headers=[
                (b"host", SCHOOL_TEST_HOST.encode()),
                (b"origin", f"http://{SCHOOL_TEST_HOST}:3000".encode()),
            ],
        )

    def test_an_expired_support_token_cannot_open_a_socket(self):
        # covers: AC-33
        self.enter()
        token = self.token
        connection.set_tenant(self.school)

        async def scenario(expect):
            ws = self.connect(token)
            connected, _ = await ws.connect()
            self.assertEqual(connected, expect)
            await ws.disconnect()

        async_to_sync(scenario)(True)
        with later(SUPPORT_SESSION_TTL + timedelta(seconds=1)):
            async_to_sync(scenario)(False)
