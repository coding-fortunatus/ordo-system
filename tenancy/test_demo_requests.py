"""Demo requests: the marketing site's form and the console's Requests page."""
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command

from tenancy.demo_requests import PER_IP_PER_HOUR
from tenancy.models import DemoRequest, PlatformAdmin, PlatformAuditLog
from tenancy.observability import counters
from tenancy.tasks import send_email
from tenancy.test_school_lifecycle import SchoolConsoleTestCase, run_inline
from tenancy.testcases import SchoolClient

URL = "/api/public/demo-requests/"


class DemoRequestTestCase(SchoolConsoleTestCase):
    def setUp(self):
        super().setUp()
        call_command("ensure_public_domains", stdout=StringIO())
        self.api = SchoolClient(HTTP_HOST="api.localhost")
        # Fresh throttle counters for every test.
        patcher = mock.patch.object(counters, "_client", counters.MemoryRedis())
        patcher.start()
        self.addCleanup(patcher.stop)

    def payload(self, **overrides):
        return {
            "kind": "demo",
            "name": "  Ada Obi ",
            "email": "Ada@Unilag.edu.ng",
            "phone": "+234 800 000 0000",
            "institution": "University of Lagos",
            "job_title": "Exams officer",
            "student_count": "10k_30k",
            "message": "We'd like to see the timetable and seating.",
            **overrides,
        }

    def submit(self, ip="203.0.113.7", **overrides):
        with run_inline(send_email), self.captureOnCommitCallbacks(execute=True):
            return self.api.post(
                URL, self.payload(**overrides), format="json", HTTP_X_FORWARDED_FOR=ip
            )


class PublicFormTests(DemoRequestTestCase):
    def test_a_request_is_stored_and_the_owners_are_emailed(self):
        response = self.submit()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data, {"ok": True})
        demo = DemoRequest.objects.get()
        self.assertEqual(demo.name, "Ada Obi")
        self.assertEqual(demo.email, "ada@unilag.edu.ng")
        self.assertEqual(demo.status, DemoRequest.Status.NEW)
        self.assertEqual(demo.ip, "203.0.113.7")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.owner_admin.email])
        self.assertIn("University of Lagos", mail.outbox[0].subject)
        self.assertIn(f"/console/requests?id={demo.pk}", mail.outbox[0].body)

    def test_setup_help_requests_are_kept_apart(self):
        self.submit(kind="setup")
        self.assertEqual(DemoRequest.objects.get().kind, DemoRequest.Kind.SETUP)
        self.assertIn("Setup help", mail.outbox[0].subject)

    def test_name_email_and_institution_are_required(self):
        response = self.submit(name="  ", email="not-an-email", institution="")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.data["errors"]), {"name", "email", "institution"})
        self.assertFalse(DemoRequest.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_blank_name_is_refused(self):
        response = self.submit(name="   ")
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.data["errors"])

    def test_an_unknown_size_band_is_refused(self):
        response = self.submit(student_count="lots")
        self.assertEqual(response.status_code, 400)
        self.assertIn("student_count", response.data["errors"])

    def test_the_honeypot_is_accepted_and_thrown_away(self):
        response = self.submit(website="http://spam.example")
        self.assertEqual(response.status_code, 201)
        self.assertFalse(DemoRequest.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_one_address_is_capped_per_hour(self):
        for _ in range(PER_IP_PER_HOUR):
            self.assertEqual(self.submit().status_code, 201)
        response = self.submit()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data["code"], "rate_limited")
        self.assertEqual(self.submit(ip="198.51.100.2").status_code, 201)

    def test_a_refused_address_does_not_spend_the_shared_budget(self):
        # Security audit F11: one IP hammering past its own cap must not
        # lock every other visitor out for the hour.
        from tenancy.demo_requests import TOTAL_PER_HOUR

        for _ in range(PER_IP_PER_HOUR + TOTAL_PER_HOUR + 5):
            self.submit()
        self.assertEqual(self.submit(ip="203.0.113.200").status_code, 201)

    def test_the_status_and_notes_cannot_be_set_from_outside(self):
        self.submit(status="converted", notes="vip")
        demo = DemoRequest.objects.get()
        self.assertEqual(demo.status, DemoRequest.Status.NEW)
        self.assertEqual(demo.notes, "")

    def test_school_hosts_do_not_serve_the_form(self):
        self.assertEqual(SchoolClient().post(URL, self.payload(), format="json").status_code, 404)


class ConsoleTests(DemoRequestTestCase):
    def setUp(self):
        super().setUp()
        self.submit()
        self.submit(kind="setup", institution="Lagos State University", email="b@lasu.edu.ng")
        self.demo = DemoRequest.objects.get(institution="University of Lagos")

    def test_the_list_is_newest_first_and_filters(self):
        rows = self.owner.get("/api/platform/demo-requests/").data["results"]
        self.assertEqual([r["institution"] for r in rows], ["Lagos State University", "University of Lagos"])
        rows = self.owner.get("/api/platform/demo-requests/?kind=setup").data["results"]
        self.assertEqual([r["institution"] for r in rows], ["Lagos State University"])
        rows = self.owner.get("/api/platform/demo-requests/?q=unilag").data["results"]
        self.assertEqual([r["id"] for r in rows], [self.demo.pk])

    def test_the_summary_counts_new_requests(self):
        self.assertEqual(self.owner.get("/api/platform/demo-requests/summary/").data, {"new": 2})

    def test_support_admins_can_triage_and_it_is_audited(self):
        support = self.make_admin("help@ordo.app", role=PlatformAdmin.Role.SUPPORT)
        client = self.sign_in(support)
        response = client.patch(
            f"/api/platform/demo-requests/{self.demo.pk}/",
            {"status": "scheduled", "notes": "Call on Tuesday", "assigned_to_id": support.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "scheduled")
        self.assertEqual(response.data["assigned_to"], {"id": support.pk, "name": support.name})
        entry = PlatformAuditLog.objects.filter(action="demo_request_updated").get()
        self.assertEqual(entry.admin, support)
        self.assertEqual(entry.detail["status"], ["new", "scheduled"])
        rows = self.owner.get("/api/platform/demo-requests/?status=open").data["results"]
        self.assertEqual(len(rows), 2)
        rows = self.owner.get("/api/platform/demo-requests/?status=new").data["results"]
        self.assertEqual(len(rows), 1)

    def test_what_the_requester_wrote_cannot_be_edited(self):
        self.owner.patch(
            f"/api/platform/demo-requests/{self.demo.pk}/", {"email": "x@y.z"}, format="json"
        )
        self.demo.refresh_from_db()
        self.assertEqual(self.demo.email, "ada@unilag.edu.ng")

    def test_a_request_can_be_linked_to_its_school(self):
        response = self.owner.patch(
            f"/api/platform/demo-requests/{self.demo.pk}/",
            {"status": "converted", "school_id": self.tenant.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["school"]["slug"], "test")

    def test_signed_out_callers_are_refused(self):
        self.assertEqual(self.console.get("/api/platform/demo-requests/").status_code, 401)
