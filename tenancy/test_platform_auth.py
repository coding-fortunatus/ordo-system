"""Platform admin sign in, roles, invites and audit (spec 0003, child 0003)."""
from datetime import timedelta
from io import StringIO
from unittest import mock

import pyotp
from cryptography.fernet import Fernet
from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.urls import Resolver404, resolve
from django.utils import timezone

from tenancy import admin_invites
from tenancy.auth import hash_key
from tenancy.tasks import send_email
from tenancy.models import PlatformAdmin, PlatformAuditLog, PlatformToken
from tenancy.testcases import SchoolClient, SchoolTestCase

CONSOLE = "console.localhost"
PASSWORD = "correct horse battery"


def current_code(admin, offset=0):
    secret = Fernet(settings.PLATFORM_TOTP_KEY).decrypt(admin.totp_secret.encode()).decode()
    totp = pyotp.TOTP(secret)
    return totp.generate_otp(totp.timecode(timezone.now()) + offset)


class PlatformTestCase(SchoolTestCase):
    def setUp(self):
        call_command("ensure_public_domains", stdout=StringIO())
        self.console = SchoolClient(HTTP_HOST=CONSOLE)

    def make_admin(self, email="owner@ordo.app", role=PlatformAdmin.Role.OWNER, enrolled=True):
        admin = PlatformAdmin(email=email, name=email.split("@")[0], role=role)
        admin.set_password(PASSWORD)
        admin.save()
        if enrolled:
            self.enrol(admin)
        return admin

    def enrol(self, admin):
        from tenancy import totp

        totp.new_secret(admin)
        admin.totp_confirmed_at = timezone.now()
        admin.save()

    def login(self, email="owner@ordo.app", password=PASSWORD, **extra):
        return self.console.post(
            "/api/platform/auth/login/", {"email": email, "password": password}, format="json", **extra
        )

    def sign_in(self, admin):
        challenge = self.login(admin.email).data["challenge"]
        admin.refresh_from_db()
        response = self.console.post(
            "/api/platform/auth/totp/",
            {"challenge": challenge, "code": current_code(admin)},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        return SchoolClient(HTTP_HOST=CONSOLE, HTTP_AUTHORIZATION=f"Bearer {response.data['token']}")


class LoginTests(PlatformTestCase):
    def test_new_admin_enrols_then_later_gets_totp_step(self):
        admin = self.make_admin(enrolled=False)
        response = self.login()
        self.assertEqual(response.data["step"], "enroll")
        self.assertTrue(response.data["otpauth_uri"].startswith("otpauth://totp/Ordo:"))
        admin.refresh_from_db()
        response = self.console.post(
            "/api/platform/auth/enroll/",
            {"challenge": response.data["challenge"], "code": current_code(admin)},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["admin"]["email"], admin.email)
        token = PlatformToken.objects.get(key_hash=hash_key(response.data["token"]))
        self.assertAlmostEqual(
            token.expires_at, timezone.now() + timedelta(hours=12), delta=timedelta(minutes=1)
        )

        self.assertEqual(self.login().data["step"], "totp")

    def test_code_cannot_be_replayed(self):
        admin = self.make_admin()
        challenge = self.login().data["challenge"]
        code = current_code(admin)
        first = self.console.post(
            "/api/platform/auth/totp/", {"challenge": challenge, "code": code}, format="json"
        )
        second = self.console.post(
            "/api/platform/auth/totp/", {"challenge": challenge, "code": code}, format="json"
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)

    def test_wrong_password_and_unknown_email_look_the_same(self):
        self.make_admin()
        wrong = self.login(password="nope")
        unknown = self.login(email="ghost@ordo.app")
        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(wrong.data, unknown.data)
        self.assertEqual(PlatformAuditLog.objects.filter(action="login_failed").count(), 2)

    def test_tampered_or_expired_challenge_is_410(self):
        admin = self.make_admin()
        response = self.console.post(
            "/api/platform/auth/totp/", {"challenge": "x", "code": "123456"}, format="json"
        )
        self.assertEqual(response.status_code, 410)
        challenge = self.login().data["challenge"]
        later = timezone.now() + timedelta(minutes=6)
        with mock.patch("time.time", return_value=later.timestamp()):
            response = self.console.post(
                "/api/platform/auth/totp/",
                {"challenge": challenge, "code": current_code(admin)},
                format="json",
            )
        self.assertEqual(response.status_code, 410)

    def test_expired_token_is_401(self):
        admin = self.make_admin()
        client = self.sign_in(admin)
        self.assertEqual(client.get("/api/platform/me/").status_code, 200)
        PlatformToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(client.get("/api/platform/me/").status_code, 401)

    def test_logout_ends_the_session(self):
        client = self.sign_in(self.make_admin())
        self.assertEqual(client.post("/api/platform/auth/logout/").status_code, 204)
        self.assertEqual(client.get("/api/platform/me/").status_code, 401)

    def test_no_token_is_401(self):
        self.assertEqual(self.console.get("/api/platform/me/").status_code, 401)

    def test_platform_api_is_not_routed_on_a_school_host(self):
        # Checked on the urlconf itself: rendering Django 5.0's 404 page breaks
        # under Python 3.14 (CI and production run 3.11).
        with self.assertRaises(Resolver404):
            resolve("/api/platform/me/", urlconf=settings.ROOT_URLCONF)
        resolve("/api/platform/me/", urlconf=settings.PUBLIC_SCHEMA_URLCONF)


class LoginTimingTests(PlatformTestCase):
    """Security audit F14: every failing path does the same hashing work,
    so timing doesn't tell a pending invite or deactivated admin apart."""

    def hashes_on_failure(self, email):
        with mock.patch("tenancy.views.make_password") as dummy:
            response = self.login(email, password="wrong-password-123")
        self.assertEqual(response.status_code, 400)
        return dummy.called

    def test_unknown_inactive_and_invited_emails_all_hash(self):
        inactive = self.make_admin("inactive@ordo.app")
        inactive.is_active = False
        inactive.save()
        invited = PlatformAdmin(email="invited@ordo.app", name="invited", role=PlatformAdmin.Role.SUPPORT)
        invited.set_unusable_password()
        invited.save()

        self.assertTrue(self.hashes_on_failure("nobody@ordo.app"))
        self.assertTrue(self.hashes_on_failure("inactive@ordo.app"))
        self.assertTrue(self.hashes_on_failure("invited@ordo.app"))


class LockoutTests(PlatformTestCase):
    def test_five_failures_lock_the_email_until_an_owner_unlocks(self):
        locked = self.make_admin("support@ordo.app", role=PlatformAdmin.Role.SUPPORT)
        owner = self.sign_in(self.make_admin())
        for _ in range(5):
            self.assertEqual(self.login(locked.email, "nope").status_code, 400)
        self.assertEqual(self.login(locked.email).status_code, 429)

        self.assertEqual(owner.post(f"/api/platform/admins/{locked.pk}/unlock/").status_code, 204)
        self.assertEqual(self.login(locked.email).status_code, 200)

    def test_wrong_codes_count_too(self):
        admin = self.make_admin()
        challenge = self.login().data["challenge"]
        for _ in range(5):
            self.console.post(
                "/api/platform/auth/totp/", {"challenge": challenge, "code": "000000"}, format="json"
            )
        response = self.console.post(
            "/api/platform/auth/totp/",
            {"challenge": challenge, "code": current_code(admin)},
            format="json",
        )
        self.assertEqual(response.status_code, 429)

    def test_twenty_failures_from_one_ip_lock_it(self):
        for n in range(20):
            self.login(f"spray{n}@ordo.app", "nope", HTTP_X_FORWARDED_FOR="203.0.113.9")
        self.make_admin()
        self.assertEqual(self.login(HTTP_X_FORWARDED_FOR="203.0.113.9").status_code, 429)
        self.assertEqual(self.login(HTTP_X_FORWARDED_FOR="203.0.113.10").status_code, 200)


class RoleTests(PlatformTestCase):
    def test_support_cannot_manage_admins(self):
        support = self.sign_in(self.make_admin("support@ordo.app", role=PlatformAdmin.Role.SUPPORT))
        self.assertEqual(support.get("/api/platform/admins/").status_code, 403)
        self.assertEqual(support.get("/api/platform/audit/").status_code, 200)

    def test_last_active_owner_cannot_be_demoted_or_deactivated(self):
        me = self.make_admin()
        other = self.make_admin("other@ordo.app")
        client = self.sign_in(me)
        self.assertEqual(
            client.patch(f"/api/platform/admins/{other.pk}/", {"is_active": False}, format="json").status_code,
            200,
        )
        # Only `me` is left, and you can't demote yourself either way.
        self.assertEqual(
            client.patch(f"/api/platform/admins/{me.pk}/", {"role": "support"}, format="json").status_code,
            409,
        )

    def test_reset_2fa_signs_the_admin_out(self):
        owner = self.sign_in(self.make_admin())
        other = self.make_admin("other@ordo.app")
        other_client = self.sign_in(other)
        self.assertEqual(owner.post(f"/api/platform/admins/{other.pk}/reset-2fa/").status_code, 204)
        self.assertEqual(other_client.get("/api/platform/me/").status_code, 401)
        self.assertEqual(self.login(other.email).data["step"], "enroll")


class InviteTests(PlatformTestCase):
    def invite(self, owner):
        run_inline = lambda *args: send_email.apply(args=args)
        with mock.patch.object(send_email, "delay", side_effect=run_inline), \
                self.captureOnCommitCallbacks(execute=True):
            response = owner.post(
                "/api/platform/admins/",
                {"email": "New@Ordo.app", "name": "New", "role": "support"},
                format="json",
            )
        self.assertEqual(response.status_code, 201, response.data)
        return PlatformAdmin.objects.get(email="new@ordo.app")

    def test_invite_link_sets_a_password_once(self):
        owner = self.sign_in(self.make_admin())
        invited = self.invite(owner)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("http", mail.outbox[0].body)
        token = admin_invites.make_token(invited)
        self.assertIn(token, mail.outbox[0].body.replace("%3A", ":"))

        weak = self.console.post("/api/platform/admins/accept/", {"token": token, "password": "123"}, format="json")
        self.assertEqual(weak.status_code, 400)
        self.assertIn("password", weak.data["errors"])
        ok = self.console.post(
            "/api/platform/admins/accept/", {"token": token, "password": PASSWORD}, format="json"
        )
        self.assertEqual(ok.status_code, 204)
        again = self.console.post(
            "/api/platform/admins/accept/", {"token": token, "password": PASSWORD}, format="json"
        )
        self.assertEqual(again.status_code, 410)
        self.assertEqual(self.login("new@ordo.app").data["step"], "enroll")
        self.assertEqual(
            owner.post(f"/api/platform/admins/{invited.pk}/resend-invite/").status_code, 409
        )

    def test_link_expires_after_48_hours(self):
        invited = self.invite(self.sign_in(self.make_admin()))
        token = admin_invites.make_token(invited)
        later = timezone.now() + timedelta(hours=49)
        with mock.patch("time.time", return_value=later.timestamp()):
            response = self.console.post(
                "/api/platform/admins/accept/", {"token": token, "password": PASSWORD}, format="json"
            )
        self.assertEqual(response.status_code, 410)

    def test_resend_voids_the_old_link(self):
        owner = self.sign_in(self.make_admin())
        invited = self.invite(owner)
        old = admin_invites.make_token(invited)
        self.assertEqual(owner.post(f"/api/platform/admins/{invited.pk}/resend-invite/").status_code, 202)
        response = self.console.post(
            "/api/platform/admins/accept/", {"token": old, "password": PASSWORD}, format="json"
        )
        self.assertEqual(response.status_code, 410)

    def test_email_taken(self):
        owner = self.sign_in(self.make_admin())
        response = owner.post(
            "/api/platform/admins/", {"email": "OWNER@ordo.app", "name": "X", "role": "owner"}, format="json"
        )
        self.assertEqual(response.status_code, 400)


class AuditTests(PlatformTestCase):
    def test_writes_are_audited_with_admin_and_ip(self):
        me = self.make_admin()
        other = self.make_admin("other@ordo.app")
        client = self.sign_in(me)
        client.patch(
            f"/api/platform/admins/{other.pk}/",
            {"name": "Renamed"},
            format="json",
            HTTP_X_FORWARDED_FOR="198.51.100.1, 203.0.113.5",
        )
        row = PlatformAuditLog.objects.get(action="admin_updated")
        self.assertEqual(row.admin, me)
        self.assertEqual(row.ip, "203.0.113.5")
        self.assertEqual(row.detail["changes"], {"name": ["other", "Renamed"]})

        listed = client.get("/api/platform/audit/", {"action": "admin_updated"})
        self.assertEqual(listed.data["count"], 1)


class CreatePlatformAdminCommandTests(PlatformTestCase):
    def test_creates_an_owner_and_resets_2fa(self):
        with mock.patch("getpass.getpass", return_value=PASSWORD):
            call_command("create_platform_admin", email="Boss@Ordo.app", name="Boss", stdout=StringIO())
        admin = PlatformAdmin.objects.get(email="boss@ordo.app")
        self.assertTrue(admin.is_owner)
        self.assertTrue(admin.check_password(PASSWORD))

        self.enrol(admin)
        call_command("create_platform_admin", email="boss@ordo.app", reset_2fa=True, stdout=StringIO())
        admin.refresh_from_db()
        self.assertIsNone(admin.totp_confirmed_at)
