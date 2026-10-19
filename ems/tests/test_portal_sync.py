"""Portal sync, build slices 1 to 11 (spec 0005): the connection and its
endpoints, the test call, syncing departments, faculties, classes,
courses and students, the first sync's preview and approval, deleting
what the portal stops sending, paging, CSV, the combined mode, and what
the portal owns while connected, and disconnecting."""
import csv
import io
import json
import socket
from datetime import date, timedelta
from unittest import mock

import httpx
from cryptography.fernet import Fernet
from django.test import override_settings
from django.utils import timezone

from ems import tasks
from ems.models import (
    AuditLog,
    BackgroundJob,
    Class,
    Course,
    Department,
    Faculty,
    GenerationConstraints,
    PortalConnection,
    PortalEndpoint,
    SeatArrangement,
    Student,
    SyncIssue,
    SyncRun,
    SystemSettings,
    User,
)
from ems.portal_sync import fetch, runs
from ems.tests.helpers import make_class, make_course, make_department, make_hall
from ems.tests.test_plan_limits import PASSWORD, PlanMixin
from tenancy.testcases import SchoolTestCase

KEY = Fernet.generate_key().decode()
SECRET = "sk_live_portal_secret_9f3a"
URL = "https://portal.example.edu/api/students"
MAPPING = {
    "external_id": "id",
    "matric_no": "matric",
    "first_name": "first",
    "last_name": "last",
    "email": "email",
    "department_ref": "dept",
    "class_ref": "level",
}


def public_dns(*ips):
    """``socket.getaddrinfo`` answering ``ips`` for every host."""
    ips = ips or ("93.184.216.34",)

    def resolve(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    return mock.patch("ems.portal_sync.fetch.socket.getaddrinfo", side_effect=resolve)


def portal(handler):
    """Route the fetcher's HTTP client to ``handler(request) -> Response``."""
    def client():
        return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)

    return mock.patch("ems.portal_sync.fetch._client", side_effect=client)


def serve(payload, status=200, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        body = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
        return httpx.Response(status, content=body)

    return handler


def row(n, dept="Accountancy", level="ND I", **extra):
    return {
        "id": f"P{n}",
        "matric": f"M{n:04}",
        "first": f"Ada{n}",
        "last": "Obi",
        "email": f"a{n}@example.com",
        "dept": dept,
        "level": level,
        **extra,
    }


@override_settings(PORTAL_SECRET_KEY=KEY)
class PortalSyncTestCase(PlanMixin, SchoolTestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="sa@test.localhost",
            password=PASSWORD,
            role=User.Role.SUPER_ADMIN,
            is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        no_sleep = mock.patch("ems.portal_sync.fetch._sleep")
        self.sleeps = no_sleep.start()
        self.addCleanup(no_sleep.stop)

    def connect(self, **data):
        body = {
            "mode": "per_type",
            "auth_type": "api_key_header",
            "header_name": "X-API-Key",
            "secret": SECRET,
            **data,
        }
        return self.client.put("/api/portal-sync/connection/", body, format="json")

    def add_endpoint(self, **data):
        return self.client.post(
            "/api/portal-sync/endpoints/",
            {"kind": "students", "url": URL, "rows_path": "data", **data},
            format="json",
        )

    def ready(self, mapping=MAPPING):
        self.assertEqual(self.connect().status_code, 200)
        endpoint = self.add_endpoint().data
        response = self.client.patch(
            f"/api/portal-sync/endpoints/{endpoint['id']}/",
            {"field_mapping": mapping},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        return endpoint["id"]

    def run_sync(self, handler, *, preview=False, path="/api/portal-sync/runs/", data=None):
        """POST ``path`` (with ``data``) and run the queued task inline
        against ``handler``.
        Unless ``preview``, the connection is approved first, so the run
        applies. Returns the finished run."""
        if not preview:
            # .update(): saving would bump updated_at, a config change.
            PortalConnection.objects.update(first_sync_approved_at=timezone.now())
        with mock.patch("ems.tasks.portal_sync_task.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(path, data, format="json")
        self.assertEqual(response.status_code, 202, response.data)
        job_id, run_id = delay.call_args.args
        with public_dns(), portal(handler):
            runs.execute(run_id, job_id)
        return SyncRun.objects.get(pk=run_id)

    def sync(self, rows, *, preview=False):
        """Start a sync and run its task inline. Returns the finished run."""
        return self.run_sync(serve({"data": rows}), preview=preview)


class ConnectionTests(PortalSyncTestCase):
    def test_the_secret_is_encrypted_and_never_returned(self):
        response = self.connect()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("secret", response.data)
        self.assertTrue(response.data["has_secret"])
        self.assertEqual(response.data["secret_last4"], "9f3a")
        stored = PortalConnection.objects.get()
        self.assertNotIn(SECRET, stored.secret)
        self.assertEqual(Fernet(KEY).decrypt(stored.secret.encode()).decode(), SECRET)
        self.assertNotIn(SECRET, json.dumps(self.client.get("/api/portal-sync/connection/").data))

    def test_one_connection_per_school_and_the_secret_is_optional_on_update(self):
        self.connect()

        response = self.client.put(
            "/api/portal-sync/connection/", {"header_name": "X-Key"}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(PortalConnection.objects.count(), 1)
        self.assertEqual(response.data["header_name"], "X-Key")
        self.assertEqual(response.data["secret_last4"], "9f3a")

    def test_a_new_connection_needs_a_secret_and_a_header_name(self):
        no_secret = self.connect(secret="")
        no_header = self.connect(header_name="")

        self.assertEqual(no_secret.status_code, 422)
        self.assertIn("secret", no_secret.data["errors"])
        self.assertEqual(no_header.status_code, 422)
        self.assertIn("header_name", no_header.data["errors"])
        self.assertFalse(PortalConnection.objects.exists())

    def test_bearer_needs_no_header_name(self):
        response = self.connect(auth_type="bearer", header_name="X-Ignored")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["header_name"], "")

    def test_no_connection_is_404(self):
        self.assertEqual(self.client.get("/api/portal-sync/connection/").status_code, 404)


class AccessTests(PortalSyncTestCase):
    def test_other_roles_are_refused(self):
        officer = User.objects.create_user(
            email="fo@test.localhost", password=PASSWORD, role=User.Role.FACULTY_OFFICER
        )
        self.client.force_authenticate(officer)

        for path in ("connection/", "fields/", "runs/"):
            self.assertEqual(
                self.client.get(f"/api/portal-sync/{path}").status_code, 403, path
            )
        self.assertEqual(self.client.post("/api/portal-sync/runs/").status_code, 403)

    def test_a_data_officer_may_manage_it(self):
        officer = User.objects.create_user(
            email="do@test.localhost", password=PASSWORD, role=User.Role.DATA_OFFICER
        )
        self.client.force_authenticate(officer)

        self.assertEqual(self.connect().status_code, 200)

    def test_a_plan_without_the_feature_gets_feature_disabled(self):
        self.set_school(features_override=["exports"])

        for path in ("connection/", "fields/", "runs/"):
            response = self.client.get(f"/api/portal-sync/{path}")
            self.assertEqual(response.status_code, 403, path)
            self.assertEqual(response.data["code"], "feature_disabled", path)


class EndpointTests(PortalSyncTestCase):
    def setUp(self):
        super().setUp()
        self.connect()

    def test_mapping_save_is_refused_while_a_required_field_is_unmapped(self):
        endpoint = self.add_endpoint().data

        response = self.client.patch(
            f"/api/portal-sync/endpoints/{endpoint['id']}/",
            {"field_mapping": {"matric_no": "matric"}},
            format="json",
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("first_name", response.data["errors"]["field_mapping"][0])

    def test_an_unknown_ordo_field_is_refused(self):
        endpoint = self.add_endpoint().data

        response = self.client.patch(
            f"/api/portal-sync/endpoints/{endpoint['id']}/",
            {"field_mapping": {**MAPPING, "shoe_size": "shoe"}},
            format="json",
        )

        self.assertEqual(response.status_code, 422)

    def test_one_endpoint_per_kind(self):
        self.add_endpoint()

        response = self.add_endpoint()

        self.assertEqual(response.status_code, 422)
        self.assertIn("kind", response.data["errors"])

    def test_plain_http_is_refused_outside_debug(self):
        with override_settings(DEBUG=False):
            response = self.add_endpoint(url="http://portal.example.edu/students")

        self.assertEqual(response.status_code, 422)
        self.assertIn("url", response.data["errors"])

    def test_fields_lists_required_flags(self):
        response = self.client.get("/api/portal-sync/fields/")

        students = {f["key"]: f["required"] for f in response.data["students"]}
        self.assertTrue(students["matric_no"])
        self.assertFalse(students["external_id"])


class EndpointTestCallTests(PortalSyncTestCase):
    def setUp(self):
        super().setUp()
        self.connect()
        self.endpoint = self.add_endpoint().data["id"]

    def call(self, handler, dns=None):
        with dns or public_dns(), portal(handler):
            return self.client.post(f"/api/portal-sync/endpoints/{self.endpoint}/test/")

    def test_returns_columns_and_at_most_20_rows_and_stores_nothing(self):
        seen = []
        rows = [row(n) for n in range(25)]
        rows[3]["extra"] = 7

        response = self.call(serve({"data": rows}, seen=seen))

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data["columns"],
            ["id", "matric", "first", "last", "email", "dept", "level", "extra"],
        )
        self.assertEqual(len(response.data["sample_rows"]), 20)
        self.assertEqual(response.data["sample_rows"][3]["extra"], "7")
        self.assertEqual(response.data["row_count_on_page"], 25)
        self.assertEqual(Student.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)
        request = seen[0]
        self.assertEqual(request.headers["X-API-Key"], SECRET)
        self.assertEqual(request.headers["Host"], "portal.example.edu")

    def test_bearer_auth_header(self):
        self.client.put(
            "/api/portal-sync/connection/", {"auth_type": "bearer"}, format="json"
        )
        seen = []

        self.call(serve({"data": []}, seen=seen))

        self.assertEqual(seen[0].headers["Authorization"], f"Bearer {SECRET}")

    def assertFails(self, response, reason):
        self.assertEqual(response.status_code, 422, response.data)
        self.assertEqual(response.data["code"], "fetch_failed")
        self.assertEqual(response.data["reason"], reason)

    def test_http_error(self):
        self.assertFails(self.call(serve({}, status=401)), "http_status")

    def test_not_json(self):
        self.assertFails(self.call(serve(b"<html>")), "not_parseable")

    def test_rows_path_not_a_list(self):
        self.assertFails(self.call(serve({"data": {"x": 1}})), "rows_path_not_list")

    def test_timeout(self):
        def handler(request):
            raise httpx.ReadTimeout("slow", request=request)

        self.assertFails(self.call(handler), "timeout")

    def test_private_and_loopback_addresses_are_blocked(self):
        for ip in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "::1"):
            with self.subTest(ip=ip):
                called = []
                response = self.call(serve([], seen=called), dns=public_dns(ip))
                self.assertFails(response, "address_blocked")
                self.assertEqual(called, [])

    def test_a_host_with_any_private_address_is_blocked(self):
        response = self.call(serve([]), dns=public_dns("93.184.216.34", "10.0.0.5"))

        self.assertFails(response, "address_blocked")

    def test_a_redirect_is_not_followed(self):
        def handler(request):
            return httpx.Response(302, headers={"Location": "http://10.0.0.5/"})

        self.assertFails(self.call(handler), "http_status")

    def test_errors_name_the_host_only(self):
        PortalEndpoint.objects.filter(pk=self.endpoint).update(
            url="https://portal.example.edu/api/students?token=topsecret"
        )

        response = self.call(serve({}, status=500))

        self.assertNotIn("topsecret", response.data["detail"])
        self.assertNotIn(SECRET, response.data["detail"])
        self.assertIn("portal.example.edu", response.data["detail"])


class FetchCapTests(PortalSyncTestCase):
    def setUp(self):
        super().setUp()
        self.connect()
        self.endpoint = PortalEndpoint.objects.get(pk=self.add_endpoint().data["id"])
        self.connection = PortalConnection.objects.get()

    def test_5xx_is_retried_three_times_with_backoff(self):
        calls = []

        with public_dns(), portal(serve({}, status=503, seen=calls)):
            with self.assertRaises(fetch.FetchError) as caught:
                fetch.fetch_rows(self.endpoint, self.connection)

        self.assertEqual(caught.exception.reason, "http_status")
        self.assertEqual(len(calls), 4)
        self.assertEqual([c.args[0] for c in self.sleeps.call_args_list], [1, 4, 16])

    def test_a_retry_that_succeeds(self):
        answers = iter([httpx.Response(502), httpx.Response(200, json={"data": [row(1)]})])

        with public_dns(), portal(lambda request: next(answers)):
            rows = fetch.fetch_rows(self.endpoint, self.connection)

        self.assertEqual(len(rows), 1)

    def test_the_byte_budget_is_shared_and_enforced(self):
        budget = fetch.Budget(remaining=100)

        with public_dns(), portal(serve({"data": [row(n) for n in range(10)]})):
            with self.assertRaises(fetch.FetchError) as caught:
                fetch.fetch_rows(self.endpoint, self.connection, budget=budget)

        self.assertEqual(caught.exception.reason, "too_large")

    def test_too_many_rows(self):
        with mock.patch.object(fetch, "MAX_ROWS", 2):
            with public_dns(), portal(serve({"data": [row(n) for n in range(3)]})):
                with self.assertRaises(fetch.FetchError) as caught:
                    fetch.fetch_rows(self.endpoint, self.connection)

        self.assertEqual(caught.exception.reason, "too_many_rows")


class StudentSyncTests(PortalSyncTestCase):
    def setUp(self):
        super().setUp()
        self.dept = make_department("ac")
        self.dept.name = "Accountancy"
        self.dept.save()
        self.nd1 = make_class(0, self.dept, name="ND I")
        self.nd2 = make_class(0, self.dept, name="ND II")
        self.ready()

    def test_creates_students_and_refreshes_class_size(self):
        run = self.sync([row(1), row(2), row(3, level="nd ii")])

        self.assertEqual(run.status, "succeeded")
        self.assertEqual(run.counts["students"]["created"], 3)
        self.assertEqual(run.counts["students"]["type_status"], "applied")
        self.assertEqual(Student.objects.get(external_id="P3").level, self.nd2)
        self.nd1.refresh_from_db()
        self.assertEqual(self.nd1.size, 2)
        self.assertEqual(run.job.status, "success")
        self.assertEqual(run.job.result_data, {"run_id": run.pk, "status": "succeeded"})

    def test_links_an_existing_student_by_matric_no(self):
        existing = Student.objects.create(
            matric_no="M0001", first_name="Old", last_name="Name", email="",
            department=self.dept, level=self.nd1,
        )

        run = self.sync([row(1)])

        self.assertEqual(run.counts["students"]["linked"], 1)
        self.assertEqual(run.counts["students"]["created"], 0)
        existing.refresh_from_db()
        self.assertEqual(existing.external_id, "P1")
        self.assertEqual(existing.first_name, "Ada1")

    def test_a_second_sync_updates_by_portal_id(self):
        self.sync([row(1), row(2)])

        run = self.sync([row(1, first="Renamed", matric="M9999"), row(2)])

        self.assertEqual(run.counts["students"]["updated"], 1)
        student = Student.objects.get(external_id="P1")
        self.assertEqual((student.first_name, student.matric_no), ("Renamed", "M9999"))
        self.assertEqual(Student.objects.count(), 2)

    def test_an_unchanged_row_is_not_counted(self):
        self.sync([row(1)])

        run = self.sync([row(1)])

        self.assertEqual(
            {k: run.counts["students"][k] for k in ("created", "linked", "updated")},
            {"created": 0, "linked": 0, "updated": 0},
        )

    def test_a_student_moved_between_classes_updates_both_sizes(self):
        self.sync([row(1), row(2)])

        self.sync([row(1, level="ND II"), row(2)])

        self.nd1.refresh_from_db()
        self.nd2.refresh_from_db()
        self.assertEqual((self.nd1.size, self.nd2.size), (1, 1))

    def test_bad_rows_are_skipped_and_reported_by_reference_only(self):
        for slug in ("tw1", "tw2"):
            Department.objects.create(name="Twin", slug=slug)
        make_class(0, self.dept, name="HND I")
        make_class(0, self.dept, name="HND I")

        run = self.sync([
            row(1, matric=""),
            row(2, dept="Twin"),
            row(3, level="HND I"),
            row(4),
            row(5, id="DUP"),
            row(6, id="DUP"),
        ])

        self.assertEqual(run.status, "partial")
        self.assertEqual(run.counts["students"]["created"], 1)
        self.assertEqual(run.counts["students"]["skipped"], 5)
        issues = SyncIssue.objects.filter(run=run, kind="bad_row")
        self.assertEqual(
            sorted(i.row_ref for i in issues), ["DUP", "P1", "P2", "P3"]
        )
        for issue in issues:
            self.assertNotIn("Ada", issue.message)
            self.assertNotIn("@example.com", issue.message)

    def test_a_department_and_class_ordo_lacks_are_created(self):
        run = self.sync([row(1, dept="Computer Science", level="ND I")])

        self.assertEqual(run.status, "succeeded")
        dept = Department.objects.get(name="Computer Science")
        self.assertEqual((dept.slug, dept.external_id), ("CS", "Computer Science"))
        cls = Class.objects.get(department=dept)
        self.assertEqual((cls.name, cls.external_id, cls.size), ("ND I", "Computer Science/ND I", 1))
        self.assertEqual(run.counts["departments"]["created"], 1)
        self.assertEqual(run.counts["classes"]["created"], 1)

        again = self.sync([row(1, dept="Computer Science", level="ND I")])

        self.assertEqual(Department.objects.filter(name="Computer Science").count(), 1)
        self.assertEqual(Class.objects.filter(department=dept).count(), 1)
        self.assertNotIn("departments", again.counts)

    def test_held_students_create_no_departments(self):
        self.set_school(max_students_override=0)

        self.sync([row(1, dept="Computer Science")])

        self.assertFalse(Department.objects.filter(name="Computer Science").exists())

    def test_unmapped_portal_id_uses_the_matric_no(self):
        PortalEndpoint.objects.update(
            field_mapping={k: v for k, v in MAPPING.items() if k != "external_id"}
        )

        self.sync([row(1)])

        self.assertEqual(Student.objects.get().external_id, "M0001")

    def test_ambiguous_matric_no_is_a_bad_row(self):
        for _ in range(2):
            Student.objects.create(
                matric_no="M0001", first_name="A", last_name="B", email="",
                department=self.dept, level=self.nd1,
            )

        run = self.sync([row(1)])

        issue = SyncIssue.objects.get(run=run)
        self.assertIn("Ambiguous match", issue.message)
        self.assertEqual(Student.objects.filter(external_id__isnull=False).count(), 0)

    def test_a_failed_fetch_applies_nothing(self):
        with mock.patch("ems.tasks.portal_sync_task.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post("/api/portal-sync/runs/")
        job_id, run_id = delay.call_args.args

        with public_dns(), portal(serve({}, status=500)):
            runs.execute(run_id, job_id)

        run = SyncRun.objects.get(pk=run_id)
        self.assertEqual(run.status, "failed")
        self.assertIn("HTTP 500", run.error)
        self.assertEqual(Student.objects.count(), 0)
        self.assertEqual(BackgroundJob.objects.get(job_id=job_id).status, "failed")
        self.assertEqual(SyncIssue.objects.get(run=run).kind, "fetch_error")

    def test_the_plan_limit_holds_students(self):
        Student.objects.create(
            matric_no="X1", first_name="A", last_name="B", email="",
            department=self.dept, level=self.nd1,
        )
        self.set_school(max_students_override=2)

        run = self.sync([row(1), row(2)])

        self.assertEqual(run.status, "held")
        self.assertEqual(run.counts["students"]["type_status"], "held")
        self.assertEqual(Student.objects.count(), 1)
        issue = SyncIssue.objects.get(run=run)
        self.assertEqual(issue.kind, "plan_limit")
        self.assertIn("3", issue.message)

    def test_hand_added_students_are_never_touched(self):
        mine = Student.objects.create(
            matric_no="HAND1", first_name="Hand", last_name="Made", email="",
            department=self.dept, level=self.nd1,
        )

        self.sync([row(1)])

        mine.refresh_from_db()
        self.assertIsNone(mine.external_id)
        self.assertEqual(mine.first_name, "Hand")


class RunStartTests(PortalSyncTestCase):
    def test_not_ready_without_a_mapping(self):
        self.connect()
        self.add_endpoint()

        response = self.client.post("/api/portal-sync/runs/")

        self.assertEqual(response.status_code, 422)
        self.assertIn("matric_no", response.data["detail"])

    def test_a_second_start_while_running_is_409(self):
        self.ready()
        with mock.patch("ems.tasks.portal_sync_task.delay"):
            self.assertEqual(self.client.post("/api/portal-sync/runs/").status_code, 202)

            response = self.client.post("/api/portal-sync/runs/")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(SyncRun.objects.count(), 1)

    def test_a_run_whose_job_was_killed_does_not_block(self):
        self.ready()
        job = BackgroundJob.objects.create(
            job_id="dead", job_type="portal_sync", status="failed", created_by=self.admin
        )
        SyncRun.objects.create(
            connection=PortalConnection.objects.get(), trigger="manual", job=job
        )

        with mock.patch("ems.tasks.portal_sync_task.delay"):
            response = self.client.post("/api/portal-sync/runs/")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(SyncRun.objects.filter(status="failed").count(), 1)

    def test_locked_while_a_timetable_exists(self):
        self.ready()
        SystemSettings.objects.create(has_timetable=True)

        response = self.client.post("/api/portal-sync/runs/")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "uploads_locked")

    def test_run_list_and_detail(self):
        self.ready()
        make_class(0, make_department("zz"))
        run = self.sync([])

        listing = self.client.get("/api/portal-sync/runs/").data
        detail = self.client.get(f"/api/portal-sync/runs/{run.pk}/").data
        issues = self.client.get(f"/api/portal-sync/runs/{run.pk}/issues/?kind=bad_row").data

        self.assertEqual(listing["results"][0]["id"], run.pk)
        self.assertEqual(detail["status"], "succeeded")
        self.assertEqual(detail["job_id"], run.job.job_id)
        self.assertEqual(issues["count"], 0)


class PreviewTests(PortalSyncTestCase):
    """The first sync is a preview that applies nothing (AC-4)."""

    def setUp(self):
        super().setUp()
        self.endpoint_id = self.ready()
        self.dept = make_department("ac")
        self.cls = make_class(0, self.dept, name="ND I")

    def approve(self, run, handler=None):
        rows = [row(1, dept=self.dept.name), row(2, dept=self.dept.name)]
        return self.run_sync(
            handler or serve({"data": rows}),
            preview=True,
            path=f"/api/portal-sync/runs/{run.pk}/approve/",
        )

    def test_a_preview_counts_with_examples_and_applies_nothing(self):
        Student.objects.create(
            matric_no="M0002", first_name="Old", last_name="Name",
            department=self.dept, level=self.cls,
        )
        rows = [row(1, dept=self.dept.name), row(2, dept=self.dept.name), row(3, dept="New Dept")]
        rows[2]["matric"] = ""

        run = self.sync(rows, preview=True)

        self.assertTrue(run.is_preview)
        self.assertEqual(run.status, "awaiting_approval")
        counts = run.counts["students"]
        self.assertEqual((counts["created"], counts["linked"], counts["skipped"]), (1, 1, 1))
        self.assertEqual(counts["examples"]["created"], ["M0001"])
        self.assertEqual(counts["examples"]["linked"], ["M0002"])
        self.assertEqual(Student.objects.count(), 1)
        self.assertIsNone(Student.objects.get().external_id)
        self.assertEqual(SyncIssue.objects.filter(run=run, kind="bad_row").count(), 1)
        self.assertIsNone(PortalConnection.objects.get().first_sync_approved_at)

    def test_examples_stop_at_50(self):
        rows = [row(n, dept=self.dept.name) for n in range(1, 61)]

        run = self.sync(rows, preview=True)

        self.assertEqual(run.counts["students"]["created"], 60)
        self.assertEqual(len(run.counts["students"]["examples"]["created"]), 50)

    def test_approving_applies_and_later_syncs_are_real(self):
        preview = self.sync([row(1, dept=self.dept.name)], preview=True)

        real = self.approve(preview)

        preview.refresh_from_db()
        self.assertEqual(preview.status, "approved")
        self.assertEqual((real.trigger, real.is_preview, real.status), ("approval", False, "succeeded"))
        self.assertNotIn("examples", real.counts["students"])
        self.assertEqual(Student.objects.count(), 2)
        self.assertIsNotNone(PortalConnection.objects.get().first_sync_approved_at)
        later = self.run_sync(serve({"data": []}), preview=True)
        self.assertFalse(later.is_preview)

    def test_only_a_waiting_preview_can_be_approved(self):
        preview = self.sync([], preview=True)
        self.approve(preview)

        response = self.client.post(f"/api/portal-sync/runs/{preview.pk}/approve/")

        self.assertEqual(response.status_code, 409)

    def test_approve_is_locked_while_a_timetable_exists(self):
        preview = self.sync([], preview=True)
        SystemSettings.objects.create(has_timetable=True)

        response = self.client.post(f"/api/portal-sync/runs/{preview.pk}/approve/")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "uploads_locked")
        preview.refresh_from_db()
        self.assertEqual(preview.status, "awaiting_approval")

    def test_a_new_preview_supersedes_the_waiting_one(self):
        first = self.sync([], preview=True)

        second = self.sync([], preview=True)

        first.refresh_from_db()
        self.assertEqual(first.status, "superseded")
        self.assertEqual(second.status, "awaiting_approval")

    def test_config_changes_reset_the_approval(self):
        url = f"/api/portal-sync/endpoints/{self.endpoint_id}/"
        changes = [
            lambda: self.client.patch(url, {"url": URL + "?v=2"}, format="json"),
            lambda: self.client.patch(url, {"rows_path": "items"}, format="json"),
            lambda: self.client.patch(
                url, {"field_mapping": {**MAPPING, "email": "mail"}}, format="json"
            ),
            lambda: self.add_endpoint(kind="classes", url=BASE + "classes"),
        ]
        # Answers under either rows path, so the rows_path change still fetches.
        answer = serve({"data": [], "items": []})
        for change in changes:
            preview = self.run_sync(answer, preview=True)
            self.approve(preview, answer)
            self.assertIsNotNone(PortalConnection.objects.get().first_sync_approved_at)

            self.assertIn(change().status_code, (200, 201))

            self.assertIsNone(PortalConnection.objects.get().first_sync_approved_at)
            PortalEndpoint.objects.filter(kind="classes").delete()

    def test_a_change_that_is_not_config_keeps_the_approval(self):
        self.approve(self.sync([], preview=True))

        self.connect(header_name="X-Key")

        self.assertIsNotNone(PortalConnection.objects.get().first_sync_approved_at)

    def test_a_config_change_supersedes_a_waiting_preview(self):
        preview = self.sync([], preview=True)

        self.client.patch(
            f"/api/portal-sync/endpoints/{self.endpoint_id}/",
            {"rows_path": "items"},
            format="json",
        )

        preview.refresh_from_db()
        self.assertEqual(preview.status, "superseded")
        response = self.client.post(f"/api/portal-sync/runs/{preview.pk}/approve/")
        self.assertEqual(response.status_code, 409)

    def test_a_config_change_while_a_preview_runs_supersedes_it(self):
        with mock.patch("ems.tasks.portal_sync_task.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post("/api/portal-sync/runs/")
        job_id, run_id = delay.call_args.args
        self.client.patch(
            f"/api/portal-sync/endpoints/{self.endpoint_id}/",
            {"field_mapping": {**MAPPING, "email": "mail"}},
            format="json",
        )

        with public_dns(), portal(serve({"data": []})):
            runs.execute(run_id, job_id)

        self.assertEqual(SyncRun.objects.get(pk=run_id).status, "superseded")

    def test_a_structure_preview_rolls_back_every_type(self):
        self.add_endpoint(kind="departments", url=BASE + "departments")
        endpoint = PortalEndpoint.objects.get(kind="departments")
        self.client.patch(
            f"/api/portal-sync/endpoints/{endpoint.pk}/",
            {"field_mapping": {"name": "name", "code": "code"}},
            format="json",
        )

        def handler(request):
            if request.url.path.endswith("departments"):
                return httpx.Response(200, json={"data": [{"name": "Computer Science", "code": "CS"}]})
            return httpx.Response(200, json={"data": [row(1, dept="Computer Science", level="ND II")]})

        run = self.run_sync(handler, preview=True)

        self.assertEqual(run.status, "awaiting_approval")
        self.assertEqual(run.counts["departments"]["examples"]["created"], ["Computer Science"])
        self.assertEqual(run.counts["classes"]["created"], 1)
        self.assertFalse(Department.objects.filter(slug="CS").exists())
        self.assertEqual(Class.objects.count(), 1)
        self.assertEqual(Student.objects.count(), 0)


BASE = "https://portal.example.edu/api/"
DEPT_MAPPING = {
    "external_id": "id",
    "name": "name",
    "code": "code",
    "faculty_ref": "faculty",
    "faculty_code": "faculty_code",
}
CLASS_MAPPING = {"external_id": "id", "name": "name", "department_ref": "dept"}
COURSE_MAPPING = {
    "external_id": "id",
    "code": "code",
    "name": "title",
    "exam_type": "type",
    "class_ref": "class",
}


class StructureSyncTests(PortalSyncTestCase):
    """Departments, faculties, classes and courses (slice 5), applied in
    that order before students."""

    def setUp(self):
        super().setUp()
        self.ready()
        self.endpoints = {}

    def endpoint(self, kind, mapping):
        response = self.add_endpoint(kind=kind, url=f"{BASE}{kind}")
        self.assertEqual(response.status_code, 201, response.data)
        self.set_mapping(kind, mapping)

    def set_mapping(self, kind, mapping):
        endpoint = PortalEndpoint.objects.get(kind=kind)
        response = self.client.patch(
            f"/api/portal-sync/endpoints/{endpoint.pk}/",
            {"field_mapping": mapping},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)

    def sync_all(self, students=(), *, preview=False, **rows_by_kind):
        """One run where each endpoint answers its own rows."""
        payloads = {"students": list(students), **rows_by_kind}

        def handler(request):
            kind = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, content=json.dumps({"data": payloads.get(kind, [])}))

        return self.run_sync(handler, preview=preview)

    def bad_refs(self, run):
        return sorted(i.row_ref for i in SyncIssue.objects.filter(run=run, kind="bad_row"))

    # Departments and their codes (AC-18)

    def test_a_mapped_code_links_by_code_and_later_changes_follow(self):
        dept = Department.objects.create(name="Accounting", slug="AC")
        cls = make_class(0, dept, name="ND I")
        self.endpoint("departments", DEPT_MAPPING)

        run = self.sync_all(departments=[{"id": "D1", "name": "Accountancy", "code": "ac"}])

        self.assertEqual(run.status, "succeeded")
        self.assertEqual(run.counts["departments"]["linked"], 1)
        dept.refresh_from_db()
        self.assertEqual((dept.external_id, dept.name, dept.slug), ("D1", "Accountancy", "AC"))

        run = self.sync_all(departments=[{"id": "D1", "name": "Accountancy", "code": "ACC"}])

        self.assertEqual(run.counts["departments"]["updated"], 1)
        dept.refresh_from_db()
        self.assertEqual(dept.slug, "ACC")
        self.assertEqual(Class.objects.get(pk=cls.pk).full_label, "ACC ND I")

    def test_unmapped_codes_are_derived_once_and_kept_on_rename(self):
        self.endpoint("departments", {"external_id": "id", "name": "name"})

        self.sync_all(departments=[
            {"id": "D1", "name": "Computer Science"},
            {"id": "D2", "name": "Computer Studies"},
            {"id": "D3", "name": "Accountancy"},
        ])

        slugs = dict(Department.objects.values_list("external_id", "slug"))
        self.assertEqual(slugs, {"D1": "CS", "D2": "CS2", "D3": "ACC"})

        self.sync_all(departments=[
            {"id": "D1", "name": "Computing"},
            {"id": "D2", "name": "Computer Studies"},
            {"id": "D3", "name": "Accountancy"},
        ])

        renamed = Department.objects.get(external_id="D1")
        self.assertEqual((renamed.name, renamed.slug), ("Computing", "CS"))

    def test_unusable_and_clashing_codes_are_bad_rows(self):
        self.endpoint("departments", DEPT_MAPPING)

        run = self.sync_all(departments=[
            {"id": "D1", "name": "Accountancy", "code": "AC"},
            {"id": "D2", "name": "Agric", "code": "ac"},
            {"id": "D3", "name": "Banking", "code": "A C!"},
            {"id": "D4", "name": "Civil", "code": "CE"},
        ])

        self.assertEqual(run.status, "partial")
        self.assertEqual(self.bad_refs(run), ["D1", "D2", "D3"])
        self.assertEqual(list(Department.objects.values_list("slug", flat=True)), ["CE"])

    # 'OTHER' isn't in the fetch: without this its delete holds departments.
    @override_settings(PORTAL_SYNC_MASS_DELETE_PCT=100)
    def test_a_code_another_record_keeps_is_a_bad_row(self):
        Department.objects.create(name="Agric", slug="AG", external_id="OTHER")
        Department.objects.create(name="Hand made", slug="HM")
        self.endpoint("departments", DEPT_MAPPING)

        run = self.sync_all(departments=[
            {"id": "D1", "name": "Accountancy", "code": "AG"},
            {"id": "D2", "name": "Home", "code": "hm"},
        ])

        self.assertEqual(self.bad_refs(run), ["D1"])
        self.assertFalse(Department.objects.filter(external_id="D1").exists())
        # An unlinked record holding the code is the row's natural key match.
        self.assertEqual(Department.objects.get(slug="HM").external_id, "D2")

    def test_codes_can_swap_in_one_run(self):
        self.endpoint("departments", DEPT_MAPPING)
        self.sync_all(departments=[
            {"id": "D1", "name": "One", "code": "X"},
            {"id": "D2", "name": "Two", "code": "Y"},
        ])

        run = self.sync_all(departments=[
            {"id": "D1", "name": "One", "code": "Y"},
            {"id": "D2", "name": "Two", "code": "X"},
        ])

        self.assertEqual(run.status, "succeeded")
        slugs = dict(Department.objects.values_list("external_id", "slug"))
        self.assertEqual(slugs, {"D1": "Y", "D2": "X"})

    # Faculties

    def test_faculties_are_created_from_department_rows(self):
        self.endpoint("departments", {**DEPT_MAPPING, "faculty_code": ""})

        run = self.sync_all(departments=[
            {"id": "D1", "name": "Civil", "code": "CE", "faculty": "Faculty of Engineering"},
            {"id": "D2", "name": "Mechanical", "code": "ME", "faculty": "Faculty of Engineering"},
        ])

        self.assertEqual(run.counts["faculties"]["created"], 1)
        faculty = Faculty.objects.get()
        self.assertEqual((faculty.name, faculty.slug), ("Faculty of Engineering", "ENG"))
        self.assertEqual(
            set(Department.objects.values_list("faculty_id", flat=True)), {faculty.pk}
        )

    def test_a_faculty_code_change_keeps_its_cbe_group(self):
        GenerationConstraints.objects.all().delete()
        GenerationConstraints.objects.create(cbe_faculty_groups={"ENG": 2, "SCI": 1})
        Faculty.objects.create(name="Engineering", slug="ENG")
        self.endpoint("departments", DEPT_MAPPING)
        row = {"id": "D1", "name": "Civil", "code": "CE", "faculty": "Engineering"}

        self.sync_all(departments=[{**row, "faculty_code": "ENG"}])
        run = self.sync_all(departments=[{**row, "faculty_code": "FENG"}])

        self.assertEqual(run.counts["faculties"]["updated"], 1)
        self.assertEqual(Faculty.objects.get().slug, "FENG")
        self.assertEqual(
            GenerationConstraints.objects.get().cbe_faculty_groups, {"FENG": 2, "SCI": 1}
        )

    # Classes

    def test_classes_link_by_department_and_name_and_create_departments(self):
        dept = Department.objects.create(name="Accountancy", slug="AC")
        mine = make_class(0, dept, name="ND I")
        self.endpoint("classes", CLASS_MAPPING)

        run = self.sync_all(classes=[
            {"id": "C1", "name": "nd i", "dept": "Accountancy"},
            {"id": "C2", "name": "ND II", "dept": "Accountancy"},
            {"id": "C3", "name": "ND I", "dept": "Statistics"},
        ])

        self.assertEqual(run.status, "succeeded")
        mine.refresh_from_db()
        self.assertEqual((mine.external_id, mine.name), ("C1", "nd i"))
        self.assertEqual(run.counts["classes"]["linked"], 1)
        self.assertEqual(run.counts["classes"]["created"], 2)
        self.assertEqual(Department.objects.get(name="Statistics").slug, "STA")
        self.assertEqual(Class.objects.get(external_id="C3").department.name, "Statistics")

    # Courses

    def test_courses_and_class_links(self):
        dept = Department.objects.create(name="Accountancy", slug="AC")
        nd1 = Class.objects.create(name="ND I", department=dept, external_id="C1")
        nd2 = Class.objects.create(name="ND II", department=dept, external_id="C2")
        hand = make_course("GNS101")
        nd1.courses.add(hand)
        self.endpoint("courses", COURSE_MAPPING)

        run = self.sync_all(courses=[
            {"id": "K1", "code": "ACC101", "title": "Intro", "type": "cbe", "class": "C1"},
            {"id": "K1", "code": "ACC101", "title": "Ignored", "type": "PBE", "class": "C2"},
            {"id": "K2", "code": "ACC102", "title": "Costing", "type": "", "class": "ND I"},
        ])

        self.assertEqual(run.status, "succeeded")
        k1 = Course.objects.get(external_id="K1")
        self.assertEqual((k1.name, k1.exam_type), ("Intro", "CBE"))
        self.assertEqual(Course.objects.get(external_id="K2").exam_type, "PBE")
        self.assertEqual(
            set(nd1.courses.values_list("code", flat=True)), {"GNS101", "ACC101", "ACC102"}
        )
        self.assertEqual(set(nd2.courses.values_list("code", flat=True)), {"ACC101"})

        self.sync_all(courses=[
            {"id": "K1", "code": "ACC101", "title": "Intro", "type": "CBE", "class": "C2"},
            {"id": "K2", "code": "ACC102", "title": "Costing", "type": "", "class": "C1"},
        ])

        # The portal owned link went; the course the school added stays.
        self.assertEqual(set(nd1.courses.values_list("code", flat=True)), {"GNS101", "ACC102"})

    def test_a_skipped_course_row_keeps_its_class_links(self):
        dept = Department.objects.create(name="Accountancy", slug="AC")
        nd1 = Class.objects.create(name="ND I", department=dept, external_id="C1")
        self.endpoint("courses", COURSE_MAPPING)
        self.sync_all(courses=[
            {"id": "K1", "code": "ACC101", "title": "Intro", "type": "PBE", "class": "C1"},
            {"id": "K2", "code": "ACC102", "title": "Costing", "type": "PBE", "class": "C1"},
        ])

        run = self.sync_all(courses=[
            {"id": "K1", "code": "ACC101", "title": "Intro", "type": "PBE", "class": "C1"},
            {"id": "K2", "code": "ACC102", "title": "Costing", "type": "OBE", "class": "C1"},
            {"id": "K3", "code": "ACC103", "title": "Audit", "type": "PBE", "class": "Nowhere"},
        ])

        self.assertEqual(self.bad_refs(run), ["K2", "K3"])
        self.assertEqual(set(nd1.courses.values_list("code", flat=True)), {"ACC101", "ACC102"})

    def test_a_course_links_an_unlinked_course_by_code(self):
        course = make_course("ACC101")
        self.endpoint("courses", {k: v for k, v in COURSE_MAPPING.items() if k != "exam_type"})

        run = self.sync_all(courses=[{"id": "K1", "code": "ACC101", "title": "Intro", "class": ""}])

        self.assertEqual(run.counts["courses"]["linked"], 1)
        course.refresh_from_db()
        self.assertEqual((course.external_id, course.name), ("K1", "Intro"))

    # Order

    def test_every_kind_applies_in_order_in_one_run(self):
        self.endpoint("departments", DEPT_MAPPING)
        self.endpoint("classes", CLASS_MAPPING)
        self.endpoint("courses", COURSE_MAPPING)

        run = self.sync_all(
            departments=[{"id": "D1", "name": "Accountancy", "code": "AC", "faculty": "Mgt", "faculty_code": "MGT"}],
            classes=[{"id": "C1", "name": "ND I", "dept": "D1"}],
            courses=[{"id": "K1", "code": "ACC101", "title": "Intro", "type": "PBE", "class": "C1"}],
            students=[row(1, dept="D1", level="C1")],
        )

        self.assertEqual(run.status, "succeeded", list(SyncIssue.objects.filter(run=run).values()))
        self.assertEqual(
            set(run.counts), {"faculties", "departments", "classes", "courses", "students"}
        )
        student = Student.objects.select_related("level__department__faculty").get()
        self.assertEqual(student.level.external_id, "C1")
        self.assertEqual(student.level.department.faculty.slug, "MGT")
        self.assertEqual(list(student.level.courses.values_list("code", flat=True)), ["ACC101"])


class DeleteTests(PortalSyncTestCase):
    """Records the portal stops sending (slice 7): deletes, the reference
    guard and its resolution, the mass delete hold and its confirm, and the
    plan limit counting planned deletes."""

    endpoint = StructureSyncTests.endpoint
    set_mapping = StructureSyncTests.set_mapping
    sync_all = StructureSyncTests.sync_all
    bad_refs = StructureSyncTests.bad_refs

    def setUp(self):
        super().setUp()
        self.dept = make_department("ac")
        self.dept.name = "Accountancy"
        self.dept.save()
        self.nd1 = make_class(0, self.dept, name="ND I")
        self.ready()
        # Ten portal owned students: dropping one is 10%, not over the hold.
        self.sync([row(n) for n in range(1, 11)])

    def seat(self, matric_no):
        student = Student.objects.get(matric_no=matric_no)
        SeatArrangement.objects.create(
            period="AM", date=date(2026, 11, 2), student=student,
            hall=make_hall(f"H{student.pk}", 2, 2), course=make_course(f"K{student.pk}"),
            cls=student.level,
        )

    def held_issue(self, run):
        return SyncIssue.objects.get(run=run, kind="delete_held_referenced")

    def resolve(self, issue, resolution):
        return self.client.post(
            f"/api/portal-sync/issues/{issue.pk}/resolve/",
            {"resolution": resolution},
            format="json",
        )

    def confirm(self, run, data_types, *, handler=None, preview=False):
        return self.run_sync(
            handler or serve({"data": []}),
            preview=preview,
            path=f"/api/portal-sync/runs/{run.pk}/confirm-mass-delete/",
            data={"data_types": data_types},
        )

    # Deletes and the reference guard (AC-10)

    def test_a_student_the_portal_drops_is_deleted(self):
        run = self.sync([row(n) for n in range(2, 11)])

        self.assertEqual(run.status, "succeeded")
        self.assertEqual(run.counts["students"]["deleted"], 1)
        self.assertFalse(Student.objects.filter(matric_no="M0001").exists())
        self.nd1.refresh_from_db()
        self.assertEqual(self.nd1.size, 9)

    def test_hand_added_students_are_never_deleted(self):
        Student.objects.create(
            matric_no="HAND1", first_name="Hand", last_name="Made", email="",
            department=self.dept, level=self.nd1,
        )

        self.sync([row(n) for n in range(1, 11)])

        self.assertTrue(Student.objects.filter(matric_no="HAND1").exists())

    def test_a_bad_rows_student_is_not_deleted(self):
        run = self.sync([row(1, first="")] + [row(n) for n in range(2, 11)])

        self.assertEqual(run.status, "partial")
        self.assertEqual(run.counts["students"]["deleted"], 0)
        self.assertTrue(Student.objects.filter(external_id="P1").exists())

    def test_a_student_with_seats_is_held_then_kept(self):
        self.seat("M0001")

        run = self.sync([row(n) for n in range(2, 11)])

        self.assertEqual(run.status, "partial")
        self.assertEqual(run.counts["students"]["held"], 1)
        issue = self.held_issue(run)
        self.assertEqual(issue.row_ref, "M0001")
        self.assertIn("exam seats", issue.message)

        response = self.resolve(issue, "keep")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["resolution"], "keep")
        self.assertEqual(response.data["resolved_by_email"], self.admin.email)
        self.assertIsNone(Student.objects.get(matric_no="M0001").external_id)
        # Ordo owned now: the next sync leaves it alone.
        run = self.sync([row(n) for n in range(2, 11)])
        self.assertEqual(run.status, "succeeded")
        self.assertTrue(Student.objects.filter(matric_no="M0001").exists())
        self.assertEqual(self.resolve(issue, "delete").status_code, 409)

    def test_resolving_delete_removes_the_record_and_its_seats(self):
        self.seat("M0001")
        run = self.sync([row(n) for n in range(2, 11)])

        response = self.resolve(self.held_issue(run), "delete")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(Student.objects.filter(matric_no="M0001").exists())
        self.assertFalse(SeatArrangement.objects.exists())
        self.nd1.refresh_from_db()
        self.assertEqual(self.nd1.size, 9)

    def test_resolving_resolves_every_open_issue_about_the_record(self):
        self.seat("M0001")
        first = self.held_issue(self.sync([row(n) for n in range(2, 11)]))
        second = self.held_issue(self.sync([row(n) for n in range(2, 11)]))

        self.resolve(second, "keep")

        first.refresh_from_db()
        self.assertEqual(first.resolution, "keep")

    def test_resolve_refusals(self):
        self.seat("M0001")
        run = self.sync([row(1, first="")] + [row(n) for n in range(2, 11)])
        bad = SyncIssue.objects.get(run=run, kind="bad_row")
        self.assertEqual(self.resolve(bad, "keep").status_code, 422)

        run = self.sync([row(n) for n in range(2, 11)])
        issue = self.held_issue(run)
        self.assertEqual(self.resolve(issue, "maybe").status_code, 422)
        SystemSettings.objects.create(has_timetable=True)
        response = self.resolve(issue, "delete")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "uploads_locked")
        # Keeping changes no exam data, so the lock allows it.
        self.assertEqual(self.resolve(issue, "keep").status_code, 200)

    def test_a_previews_held_delete_cant_be_resolved(self):
        self.seat("M0001")
        PortalConnection.objects.update(first_sync_approved_at=None)

        run = self.sync([row(n) for n in range(2, 11)], preview=True)

        self.assertEqual(run.status, "awaiting_approval")
        self.assertEqual(self.resolve(self.held_issue(run), "delete").status_code, 409)
        self.assertEqual(Student.objects.get(matric_no="M0001").external_id, "P1")

    def test_a_preview_counts_deletes_and_applies_none(self):
        PortalConnection.objects.update(first_sync_approved_at=None)

        run = self.sync([row(n) for n in range(2, 11)], preview=True)

        self.assertEqual(run.counts["students"]["deleted"], 1)
        self.assertEqual(run.counts["students"]["examples"]["deleted"], ["M0001"])
        self.assertTrue(Student.objects.filter(matric_no="M0001").exists())

    @override_settings(PORTAL_SYNC_MASS_DELETE_PCT=100)
    def test_deletes_run_children_first(self):
        self.endpoint("departments", DEPT_MAPPING)
        self.endpoint("classes", CLASS_MAPPING)
        students = [row(n) for n in range(1, 11)]
        self.sync_all(
            students,
            departments=[
                {"id": "D1", "name": "Accountancy", "code": "AC"},
                {"id": "D2", "name": "Humanities", "code": "HUM"},
            ],
            classes=[
                {"id": "C1", "name": "ND I", "dept": "D1"},
                {"id": "C2", "name": "HND I", "dept": "D2"},
                {"id": "C3", "name": "ND II", "dept": "D1"},
            ],
        )
        Student.objects.filter(matric_no="M0010").update(level=Class.objects.get(external_id="C3"))

        # M0010's row moves it back to ND I before ND II's delete is checked,
        # and D2 loses its only class before its own delete is checked.
        run = self.sync_all(
            students,
            departments=[{"id": "D1", "name": "Accountancy", "code": "AC"}],
            classes=[{"id": "C1", "name": "ND I", "dept": "D1"}],
        )

        self.assertEqual(run.status, "succeeded")
        self.assertEqual(run.counts["classes"]["deleted"], 2)
        self.assertEqual(run.counts["departments"]["deleted"], 1)
        self.assertEqual(list(Class.objects.values_list("external_id", flat=True)), ["C1"])
        self.assertFalse(Department.objects.filter(external_id="D2").exists())

    @override_settings(PORTAL_SYNC_MASS_DELETE_PCT=100)
    def test_a_department_with_staff_or_classes_that_stay_is_held(self):
        self.endpoint("departments", DEPT_MAPPING)
        self.sync_all(
            [row(n) for n in range(1, 11)],
            departments=[
                {"id": "D1", "name": "Accountancy", "code": "AC"},
                {"id": "D2", "name": "Humanities", "code": "HUM"},
                {"id": "D3", "name": "Drama", "code": "DRA"},
            ],
        )
        User.objects.create_user(
            email="hod@test.localhost", password=PASSWORD,
            department=Department.objects.get(external_id="D2"),
        )
        make_class(0, Department.objects.get(external_id="D3"), name="Hand made")

        run = self.sync_all(
            [row(n) for n in range(1, 11)],
            departments=[{"id": "D1", "name": "Accountancy", "code": "AC"}],
        )

        self.assertEqual(run.status, "partial")
        messages = {
            i.row_ref: i.message
            for i in SyncIssue.objects.filter(run=run, kind="delete_held_referenced")
        }
        self.assertIn("staff accounts", messages["Humanities"])
        self.assertIn("classes", messages["Drama"])
        self.assertEqual(Department.objects.filter(external_id__in=["D2", "D3"]).count(), 2)

    @override_settings(PORTAL_SYNC_MASS_DELETE_PCT=100)
    def test_a_class_with_students_that_stay_is_held(self):
        self.endpoint("classes", CLASS_MAPPING)
        self.sync_all(
            [row(n) for n in range(1, 11)],
            classes=[
                {"id": "C1", "name": "ND I", "dept": "Accountancy"},
                {"id": "C2", "name": "ND II", "dept": "Accountancy"},
            ],
        )

        run = self.sync_all(
            [row(n) for n in range(1, 10)] + [row(10, level="ND II")],
            classes=[{"id": "C1", "name": "ND I", "dept": "Accountancy"}],
        )

        issue = self.held_issue(run)
        self.assertEqual((issue.data_type, issue.row_ref), ("classes", "ND II"))
        self.assertIn("still has students", issue.message)
        self.assertTrue(Class.objects.filter(external_id="C2").exists())

    # The mass delete hold (AC-11)

    def test_an_empty_student_list_is_held_and_confirm_deletes(self):
        self.endpoint("departments", DEPT_MAPPING)

        run = self.sync_all([], departments=[{"id": "D1", "name": "Accountancy", "code": "AC"}])

        self.assertEqual(run.status, "held")
        self.assertEqual(run.counts["students"]["type_status"], "held")
        self.assertEqual(run.counts["students"]["held"], 10)
        self.assertEqual(run.counts["departments"]["linked"], 1)
        issue = SyncIssue.objects.get(run=run, kind="mass_delete_held")
        self.assertEqual(issue.data_type, "students")
        self.assertIn("10 of 10", issue.message)
        self.assertEqual(Student.objects.count(), 10)

        payloads = {"departments": [{"id": "D1", "name": "Accountancy", "code": "AC"}]}
        confirmed = self.confirm(
            run,
            ["students"],
            handler=lambda request: httpx.Response(
                200,
                content=json.dumps({"data": payloads.get(request.url.path.rsplit("/", 1)[-1], [])}),
            ),
        )

        self.assertEqual(confirmed.trigger, "confirm")
        self.assertEqual(confirmed.allow_mass_delete, ["students"])
        self.assertEqual(confirmed.status, "succeeded")
        self.assertEqual(confirmed.counts["students"]["deleted"], 10)
        self.assertEqual(Student.objects.count(), 0)

    def test_confirm_takes_only_held_types_from_the_latest_run(self):
        run = self.sync([])
        url = f"/api/portal-sync/runs/{run.pk}/confirm-mass-delete/"

        response = self.client.post(url, {"data_types": ["courses"]}, format="json")
        self.assertEqual(response.status_code, 422)
        response = self.client.post(url, {"data_types": []}, format="json")
        self.assertEqual(response.status_code, 422)

        self.sync([row(n) for n in range(1, 11)])
        response = self.client.post(url, {"data_types": ["students"]}, format="json")
        self.assertEqual(response.status_code, 409)

    def test_confirm_is_locked_while_a_timetable_exists(self):
        run = self.sync([])
        SystemSettings.objects.create(has_timetable=True)

        response = self.client.post(
            f"/api/portal-sync/runs/{run.pk}/confirm-mass-delete/",
            {"data_types": ["students"]},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "uploads_locked")

    def test_a_confirmed_preview_carries_its_allowance_into_the_approval(self):
        PortalConnection.objects.update(first_sync_approved_at=None)
        held = self.sync([], preview=True)
        self.assertEqual(held.counts["students"]["type_status"], "held")

        preview = self.confirm(held, ["students"], preview=True)
        self.assertEqual(preview.status, "awaiting_approval")
        self.assertEqual(preview.counts["students"]["deleted"], 10)
        self.assertEqual(Student.objects.count(), 10)

        applied = self.run_sync(
            serve({"data": []}),
            preview=True,
            path=f"/api/portal-sync/runs/{preview.pk}/approve/",
        )
        self.assertEqual(applied.allow_mass_delete, ["students"])
        self.assertEqual(Student.objects.count(), 0)

    def test_rows_naming_a_held_types_missing_records_are_bad_rows(self):
        self.endpoint("classes", CLASS_MAPPING)
        self.sync_all(
            [row(n) for n in range(1, 11)],
            classes=[
                {"id": "C1", "name": "ND I", "dept": "Accountancy"},
                {"id": "C2", "name": "ND II", "dept": "Accountancy"},
            ],
        )

        run = self.sync_all(
            [row(n) for n in range(1, 11)] + [row(11, level="ND II"), row(12, level="HND I")],
            classes=[{"id": "C1", "name": "ND I", "dept": "Accountancy"}],
        )

        self.assertEqual(run.status, "held")
        self.assertEqual(run.counts["classes"]["type_status"], "held")
        self.assertEqual(run.counts["students"]["type_status"], "applied")
        self.assertEqual(self.bad_refs(run), ["P11", "P12"])
        self.assertTrue(Class.objects.filter(external_id="C2").exists())
        self.assertFalse(Class.objects.filter(name="HND I").exists())

    # The plan limit (AC-12)

    def test_the_plan_limit_counts_planned_deletes(self):
        self.set_school(max_students_override=10)

        run = self.sync([row(n) for n in range(2, 12)])

        self.assertEqual(run.status, "succeeded")
        self.assertEqual(Student.objects.count(), 10)

    def test_a_held_delete_does_not_count_toward_the_plan_limit(self):
        self.set_school(max_students_override=10)
        self.seat("M0001")

        run = self.sync([row(n) for n in range(2, 12)])

        self.assertEqual(run.status, "held")
        issue = SyncIssue.objects.get(run=run, kind="plan_limit")
        self.assertIn("11", issue.message)
        self.assertTrue(Student.objects.filter(matric_no="M0001", external_id="P1").exists())


# Slice 8: paging, CSV and the combined mode --------------------------------


def paged(pages, param="page", seen=None):
    """A portal answering ``pages[n - 1]`` for ``?<param>=n``, and an empty
    list past the last page."""
    def handler(request):
        if seen is not None:
            seen.append(request)
        n = int(request.url.params[param])
        rows = pages[n - 1] if n <= len(pages) else []
        return httpx.Response(200, json={"data": rows})

    return handler


def linked(pages, seen=None):
    """A portal whose page ``n`` names page ``n + 1`` at ``meta.next``."""
    def handler(request):
        if seen is not None:
            seen.append(request)
        n = int(request.url.params.get("p", "1"))
        nxt = f"{URL}?p={n + 1}" if n < len(pages) else None
        return httpx.Response(200, json={"data": pages[n - 1], "meta": {"next": nxt}})

    return handler


def to_csv(rows):
    """``rows`` as a UTF-8 CSV with a byte order mark, as Excel saves it."""
    out = io.StringIO()
    columns = list(dict.fromkeys(k for r in rows for k in r))
    writer = csv.DictWriter(out, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return ("\ufeff" + out.getvalue()).encode("utf-8")


class PagingTests(PortalSyncTestCase):
    """``page_param`` and ``next_link`` paging, and a failure on any page
    failing the whole run (AC-6)."""

    def setUp(self):
        super().setUp()
        self.endpoint_id = self.ready()

    def set_paging(self, **data):
        response = self.client.patch(
            f"/api/portal-sync/endpoints/{self.endpoint_id}/", data, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)

    def test_page_param_reads_every_page_until_an_empty_one(self):
        self.set_paging(pagination="page_param", page_param="page")
        seen = []
        pages = [[row(1), row(2)], [row(3), row(4)], [row(5)]]

        run = self.run_sync(paged(pages, seen=seen))

        self.assertEqual(run.status, "succeeded")
        self.assertEqual(Student.objects.count(), 5)
        self.assertEqual([r.url.params["page"] for r in seen], ["1", "2", "3", "4"])

    def test_page_param_keeps_the_urls_own_query(self):
        self.client.patch(
            f"/api/portal-sync/endpoints/{self.endpoint_id}/",
            {"url": f"{URL}?session=2026", "pagination": "page_param", "page_param": "pg"},
            format="json",
        )
        seen = []

        self.run_sync(paged([[row(1)]], param="pg", seen=seen))

        self.assertEqual(seen[0].url.params["session"], "2026")
        self.assertEqual(seen[0].url.params["pg"], "1")

    def test_the_test_call_fetches_the_first_page_only(self):
        self.set_paging(pagination="page_param", page_param="page")
        seen = []

        with public_dns(), portal(paged([[row(1)], [row(2)]], seen=seen)):
            response = self.client.post(f"/api/portal-sync/endpoints/{self.endpoint_id}/test/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["row_count_on_page"], 1)
        self.assertEqual(len(seen), 1)

    def test_a_page_failing_after_retries_fails_the_run_and_applies_nothing(self):
        self.sync([row(1), row(2)])
        self.set_paging(pagination="page_param", page_param="page")
        pages = {"1": [row(1, first="Renamed")], "2": [row(3)], "4": [row(4)], "5": [row(5)]}

        def handler(request):
            n = request.url.params["page"]
            if n == "3":
                return httpx.Response(500)
            return httpx.Response(200, json={"data": pages.get(n, [])})

        run = self.run_sync(handler)

        self.assertEqual(run.status, "failed")
        self.assertIn("HTTP 500", run.error)
        self.assertEqual(
            sorted(Student.objects.values_list("first_name", flat=True)), ["Ada1", "Ada2"]
        )
        self.assertFalse(Student.objects.filter(matric_no="M0003").exists())

    def test_too_many_pages_fails_the_run(self):
        self.set_paging(pagination="page_param", page_param="page")

        with mock.patch.object(fetch, "MAX_PAGES", 2):
            run = self.run_sync(paged([[row(1)], [row(2)], [row(3)]]))

        self.assertEqual(run.status, "failed")
        self.assertEqual(Student.objects.count(), 0)

    def test_next_link_follows_until_empty(self):
        self.set_paging(pagination="next_link", next_path="meta.next")
        seen = []

        run = self.run_sync(linked([[row(1)], [row(2)], [row(3)]], seen=seen))

        self.assertEqual(run.status, "succeeded")
        self.assertEqual(Student.objects.count(), 3)
        self.assertEqual(len(seen), 3)

    def test_a_relative_next_link_stays_on_the_endpoints_host(self):
        self.set_paging(pagination="next_link", next_path="next")
        seen = []

        def handler(request):
            seen.append(request)
            if "cursor" in request.url.params:
                return httpx.Response(200, json={"data": [row(2)], "next": ""})
            return httpx.Response(200, json={"data": [row(1)], "next": "/api/students?cursor=b"})

        run = self.run_sync(handler)

        self.assertEqual(run.status, "succeeded")
        self.assertEqual(seen[1].headers["host"], "portal.example.edu")
        self.assertEqual(Student.objects.count(), 2)

    def test_a_next_link_to_another_host_is_blocked(self):
        self.set_paging(pagination="next_link", next_path="next")

        run = self.run_sync(serve({"data": [row(1)], "next": "https://evil.example.com/x"}))

        self.assertEqual(run.status, "failed")
        self.assertIn("leaves the portal", run.error)
        self.assertEqual(Student.objects.count(), 0)
        issue = SyncIssue.objects.get(run=run)
        self.assertEqual(issue.kind, "fetch_error")

    def test_a_next_link_resolving_to_a_private_address_is_blocked(self):
        self.set_paging(pagination="next_link", next_path="next")
        answers = iter(["93.184.216.34", "10.0.0.5"])

        def resolve(host, port, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), port))]

        endpoint = PortalEndpoint.objects.get()
        with mock.patch("ems.portal_sync.fetch.socket.getaddrinfo", side_effect=resolve):
            with portal(serve({"data": [row(1)], "next": f"{URL}?p=2"})):
                with self.assertRaises(fetch.FetchError) as caught:
                    fetch.fetch_rows(endpoint, endpoint.connection)

        self.assertEqual(caught.exception.reason, "address_blocked")

    def test_a_next_link_loop_fails(self):
        self.set_paging(pagination="next_link", next_path="next")

        run = self.run_sync(serve({"data": [row(1)], "next": URL}))

        self.assertEqual(run.status, "failed")
        self.assertIn("loop", run.error)

    def test_paging_needs_its_parameter(self):
        for data, field in (
            ({"pagination": "page_param", "page_param": ""}, "page_param"),
            ({"pagination": "next_link", "next_path": ""}, "next_path"),
        ):
            response = self.client.patch(
                f"/api/portal-sync/endpoints/{self.endpoint_id}/", data, format="json"
            )
            self.assertEqual(response.status_code, 422)
            self.assertIn(field, response.data["errors"])


class CsvTests(PortalSyncTestCase):
    """A CSV endpoint: its header row is the columns (AC-2)."""

    def setUp(self):
        super().setUp()
        self.connect()
        response = self.add_endpoint(format="csv", pagination="page_param", page_param="p")
        self.assertEqual(response.status_code, 201, response.data)
        self.endpoint = response.data

    def test_csv_forces_no_paging_and_no_rows_path(self):
        self.assertEqual(self.endpoint["pagination"], "none")
        self.assertEqual(self.endpoint["rows_path"], "")

    def test_the_test_call_reads_the_header_and_rows(self):
        body = to_csv([row(1), row(2, email="")])

        with public_dns(), portal(serve(body)):
            response = self.client.post(f"/api/portal-sync/endpoints/{self.endpoint['id']}/test/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data["columns"], ["id", "matric", "first", "last", "email", "dept", "level"]
        )
        self.assertEqual(response.data["row_count_on_page"], 2)
        self.assertEqual(response.data["sample_rows"][1]["email"], "")

    def test_a_body_that_isnt_utf8_csv_is_refused(self):
        with public_dns(), portal(serve(b"\xff\xfe\x00garbage")):
            response = self.client.post(f"/api/portal-sync/endpoints/{self.endpoint['id']}/test/")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data["reason"], "not_parseable")

    def test_a_csv_sync_keeps_leading_zeros_and_skips_blank_lines(self):
        self.client.patch(
            f"/api/portal-sync/endpoints/{self.endpoint['id']}/",
            {"field_mapping": MAPPING},
            format="json",
        )
        body = to_csv([row(1, matric="007/ND/21"), row(2, matric="0042")]) + b",,,,,,\r\n"

        run = self.run_sync(serve(body))

        self.assertEqual(run.status, "succeeded", list(SyncIssue.objects.values()))
        self.assertEqual(
            sorted(Student.objects.values_list("matric_no", flat=True)), ["0042", "007/ND/21"]
        )


COMBINED_MAPPING = {
    **MAPPING,
    "department_code": "dept_code",
    "faculty_ref": "faculty",
    "faculty_code": "faculty_code",
    "course_codes": "courses",
}


def crow(n, dept="Accountancy", level="ND I", courses="ACC101;ACC102", **extra):
    return row(n, dept=dept, level=level, courses=courses, **extra)


class CombinedTests(PortalSyncTestCase):
    """One endpoint for everything: departments, faculties, classes and
    courses come from the student rows (spec 0005, *Ordo fields per kind*)."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.connect(mode="combined").status_code, 200)

    def combined(self, mapping=COMBINED_MAPPING, **data):
        response = self.add_endpoint(kind="combined", **data)
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.patch(
            f"/api/portal-sync/endpoints/{response.data['id']}/",
            {"field_mapping": mapping},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)

    def test_a_combined_connection_takes_only_the_combined_endpoint(self):
        self.assertEqual(self.add_endpoint(kind="students").status_code, 422)
        self.combined()
        self.assertEqual(self.add_endpoint(kind="combined").status_code, 422)

    def test_a_combined_csv_creates_everything_and_links_class_courses(self):
        self.combined(format="csv")
        rows = [
            crow(1, dept_code="ac", faculty="Management", faculty_code="mgt"),
            crow(2, courses="ACC103"),
            crow(3, dept="Computer Science", level="ND I", courses='["COM101", "ACC101"]',
                 faculty="Science"),
        ]

        run = self.run_sync(serve(to_csv(rows)))

        self.assertEqual(run.status, "succeeded", list(SyncIssue.objects.values()))
        acc = Department.objects.get(name="Accountancy")
        self.assertEqual((acc.slug, acc.external_id, acc.faculty.slug), ("AC", "AC", "MGT"))
        cs = Department.objects.get(name="Computer Science")
        self.assertEqual((cs.slug, cs.faculty.name, cs.faculty.slug), ("CS", "Science", "SCI"))
        nd1 = Class.objects.get(department=acc)
        self.assertEqual(nd1.external_id, "Accountancy/ND I")
        self.assertEqual(
            sorted(nd1.courses.values_list("code", flat=True)), ["ACC101", "ACC102", "ACC103"]
        )
        self.assertEqual(
            sorted(Class.objects.get(department=cs).courses.values_list("code", flat=True)),
            ["ACC101", "COM101"],
        )
        course = Course.objects.get(code="COM101")
        self.assertEqual((course.name, course.exam_type, course.external_id), ("COM101", "PBE", "COM101"))
        self.assertEqual(nd1.size, 2)
        self.assertEqual(run.counts["courses"]["created"], 4)
        self.assertEqual(run.counts["departments"]["created"], 2)
        self.assertEqual(run.counts["students"]["created"], 3)

    def test_existing_records_link_and_keep_their_names(self):
        dept = make_department("ac")
        dept.name = "Accountancy"
        dept.save()
        cls = make_class(0, dept, name="ND I")
        course = Course.objects.create(code="ACC101", name="Intro to Accounting", exam_type="CBE")
        self.combined()

        run = self.sync([crow(1, courses="ACC101")])

        self.assertEqual(run.status, "succeeded", list(SyncIssue.objects.values()))
        dept.refresh_from_db()
        cls.refresh_from_db()
        course.refresh_from_db()
        self.assertEqual(dept.external_id, "Accountancy")
        self.assertEqual(cls.external_id, "Accountancy/ND I")
        self.assertEqual(
            (course.name, course.exam_type, course.external_id),
            ("Intro to Accounting", "CBE", "ACC101"),
        )
        self.assertEqual(run.counts["departments"]["linked"], 1)
        self.assertEqual(run.counts["classes"]["linked"], 1)
        self.assertEqual(run.counts["courses"]["linked"], 1)
        self.assertEqual(Student.objects.get().level, cls)

    def test_a_ref_by_portal_id_keeps_the_records_name(self):
        dept = Department.objects.create(name="Accountancy", slug="AC", external_id="D1")
        make_class(0, dept, name="ND I")
        Class.objects.update(external_id="C1")
        self.combined()

        run = self.sync([crow(1, dept="D1", level="C1")])

        self.assertEqual(run.status, "succeeded", list(SyncIssue.objects.values()))
        self.assertEqual(Department.objects.get().name, "Accountancy")
        self.assertEqual(Class.objects.get().name, "ND I")
        self.assertEqual(Student.objects.get().department, dept)

    def test_an_unusable_code_skips_its_row_and_creates_nothing_from_it(self):
        self.combined()

        run = self.sync([
            crow(1, dept="Accountancy", dept_code="A C!"),
            crow(2, dept="Computer Science", faculty="Science", faculty_code="S!"),
            crow(3, dept="Mass Comm", dept_code="MC"),
        ])

        self.assertEqual(run.status, "partial")
        bad = SyncIssue.objects.filter(run=run, kind="bad_row")
        self.assertEqual(sorted(i.row_ref for i in bad), ["P1", "P2"])
        self.assertEqual(list(Department.objects.values_list("slug", flat=True)), ["MC"])
        self.assertEqual(Student.objects.get().matric_no, "M0003")

    def test_a_department_code_clash_makes_its_students_bad_rows(self):
        Department.objects.create(name="Agric", slug="AC", external_id="AG")
        self.combined()

        run = self.sync([crow(1, dept="Accountancy", dept_code="AC")])

        self.assertEqual(run.status, "partial")
        refs = sorted(
            (i.data_type, i.row_ref) for i in SyncIssue.objects.filter(run=run, kind="bad_row")
        )
        self.assertEqual(refs, [("departments", "AC"), ("students", "P1")])
        self.assertFalse(Student.objects.exists())

    def test_the_first_row_with_a_code_sets_it(self):
        self.combined()

        self.sync([crow(1), crow(2, dept_code="ACT"), crow(3, dept_code="XYZ")])

        self.assertEqual(Department.objects.get().slug, "ACT")

    def test_course_links_follow_what_each_class_lists(self):
        self.combined()
        nd3 = crow(3, level="ND III", courses="ACC102")
        self.sync([crow(1, courses="ACC101,ACC102"), crow(2, level="ND II", courses="ACC102"), nd3])
        nd1, nd2, _ = Class.objects.order_by("name")

        # ND I drops ACC102; ND II lists nothing, so it keeps its links.
        run = self.sync([crow(1, courses="ACC101"), crow(2, level="ND II", courses=""), nd3])

        self.assertEqual(run.status, "succeeded", list(SyncIssue.objects.filter(run=run).values()))
        self.assertEqual(list(nd1.courses.values_list("code", flat=True)), ["ACC101"])
        self.assertEqual(list(nd2.courses.values_list("code", flat=True)), ["ACC102"])

    def test_a_course_no_row_lists_any_more_is_deleted(self):
        self.combined()
        codes = ",".join(f"K{n}" for n in range(10))
        self.sync([crow(1, courses=codes)])

        run = self.sync([crow(1, courses=codes.replace(",K9", ""))])

        self.assertEqual(run.status, "succeeded")
        self.assertEqual(run.counts["courses"]["deleted"], 1)
        self.assertFalse(Course.objects.filter(code="K9").exists())

    def test_a_department_no_student_names_any_more_is_deleted(self):
        self.combined()
        rows = [crow(n, dept=f"Dept {n}", courses="") for n in range(10)]
        self.sync(rows)
        self.assertEqual(Department.objects.count(), 10)

        run = self.sync(rows[:9])

        self.assertEqual(run.status, "succeeded", list(SyncIssue.objects.filter(run=run).values()))
        for data_type in ("students", "classes", "departments"):
            self.assertEqual(run.counts[data_type]["deleted"], 1, data_type)
        self.assertFalse(Department.objects.filter(name="Dept 9").exists())

    def test_without_course_codes_mapped_courses_are_never_deleted(self):
        mapping = {k: v for k, v in COMBINED_MAPPING.items() if k != "course_codes"}
        self.combined(mapping=mapping)
        Course.objects.create(code="ACC101", name="Intro", external_id="ACC101")

        run = self.sync([crow(1)])

        self.assertEqual(run.status, "succeeded")
        self.assertTrue(Course.objects.filter(code="ACC101").exists())
        self.assertNotIn("courses", run.counts)

    def test_a_combined_preview_applies_nothing(self):
        self.combined()

        run = self.sync([crow(1), crow(2, dept="Computer Science")], preview=True)

        self.assertEqual(run.status, "awaiting_approval")
        self.assertEqual(run.counts["departments"]["created"], 2)
        self.assertEqual(run.counts["courses"]["created"], 2)
        self.assertEqual(sorted(run.counts["students"]["examples"]["created"]), ["M0001", "M0002"])
        for model in (Department, Class, Course, Student, Faculty):
            self.assertFalse(model.objects.exists(), model)

    def test_held_students_still_apply_the_other_types(self):
        self.set_school(max_students_override=1)
        self.combined()

        run = self.sync([crow(1), crow(2)])

        self.assertEqual(run.status, "held")
        self.assertEqual(run.counts["students"]["type_status"], "held")
        self.assertEqual(Department.objects.count(), 1)
        self.assertEqual(Class.objects.get().courses.count(), 2)
        self.assertFalse(Student.objects.exists())


class PortalManagedTests(PortalSyncTestCase):
    """Synced records are read only where the portal owns them, and the
    sync follows the upload lock (slice 9, AC-8, AC-13, AC-18)."""

    endpoint = StructureSyncTests.endpoint
    set_mapping = StructureSyncTests.set_mapping
    sync_all = StructureSyncTests.sync_all

    def setUp(self):
        super().setUp()
        self.ready()

    def assertManaged(self, response):
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.data["code"], "portal_managed")

    def synced_student(self):
        self.assertEqual(self.sync([row(1)]).status, "succeeded")
        return Student.objects.get(external_id="P1")

    # Students

    def test_a_synced_student_is_read_only_and_cant_be_deleted(self):
        student = self.synced_student()

        response = self.client.patch(
            f"/api/students/{student.pk}/", {"first_name": "Changed"}, format="json"
        )
        self.assertManaged(response)
        response = self.client.patch(
            f"/api/students/{student.pk}/", {"class_id": make_class(0).pk}, format="json"
        )
        self.assertManaged(response)
        self.assertManaged(self.client.delete(f"/api/students/{student.pk}/"))
        student.refresh_from_db()
        self.assertEqual(student.first_name, "Ada1")

    def test_sending_portal_fields_unchanged_is_fine(self):
        student = self.synced_student()
        body = {
            "first_name": " Ada1 ",
            "last_name": "Obi",
            "matric_no": "m0001",
            "email": "a1@example.com",
            "phone": "",
            "class_id": student.level_id,
        }
        response = self.client.put(f"/api/students/{student.pk}/", body, format="json")
        self.assertEqual(response.status_code, 200, response.data)

    def test_the_api_shows_what_the_portal_owns(self):
        student = self.synced_student()
        data = self.client.get(f"/api/students/{student.pk}/").data
        self.assertTrue(data["from_portal"])
        self.assertIn("first_name", data["portal_locked"])
        self.assertIn("class_id", data["portal_locked"])

        # A students only connection writes no department name or code.
        dept = self.client.get(f"/api/departments/{student.department.slug}/").data
        self.assertTrue(dept["from_portal"])
        self.assertEqual(dept["portal_locked"], [])

    def test_hand_made_records_stay_editable_but_none_can_be_added(self):
        dept = make_department()
        cls = make_class(0, dept)
        hand = Student.objects.create(
            matric_no="H1", first_name="Hand", last_name="Made", department=dept, level=cls
        )
        response = self.client.patch(
            f"/api/students/{hand.pk}/", {"first_name": "Edited"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["from_portal"])
        self.assertEqual(response.data["portal_locked"], [])

        response = self.client.post(
            "/api/students/",
            {"first_name": "New", "last_name": "One", "matric_no": "N1", "class_id": cls.pk},
            format="json",
        )
        self.assertManaged(response)
        self.assertEqual(self.client.delete(f"/api/students/{hand.pk}/").status_code, 204)

    def test_without_a_connection_nothing_is_portal_managed(self):
        student = self.synced_student()
        PortalConnection.objects.all().delete()
        response = self.client.patch(
            f"/api/students/{student.pk}/", {"first_name": "Changed"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["from_portal"])

    # Uploads

    def test_csv_uploads_of_synced_types_are_refused(self):
        student = self.synced_student()
        csv_file = lambda: io.BytesIO(b"Matric,First\n")  # noqa: E731
        for path in (
            f"/api/uploads/class-students/{student.level_id}/",
            "/api/uploads/departments/",
            f"/api/uploads/classes/{student.department.slug}/",
        ):
            response = self.client.post(path, {"file": csv_file()}, format="multipart")
            self.assertManaged(response)
        response = self.client.post(
            "/api/uploads/bulk/",
            {"file": csv_file(), "upload_type": "departments"},
            format="multipart",
        )
        self.assertManaged(response)

        # Courses aren't synced by a students endpoint; halls never are.
        for path in ("/api/uploads/courses/", "/api/uploads/halls/"):
            response = self.client.post(path, {"file": csv_file()}, format="multipart")
            self.assertNotEqual(response.status_code, 403, response.data)

    def test_the_upload_lock_message_wins_over_portal_managed(self):
        self.synced_student()
        SystemSettings.objects.create(has_timetable=True)
        response = self.client.post(
            "/api/uploads/departments/", {"file": io.BytesIO(b"x")}, format="multipart"
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotEqual(response.data.get("code"), "portal_managed")

    # Classes and courses

    def test_ordo_owned_class_fields_stay_editable(self):
        self.endpoint("classes", CLASS_MAPPING)
        self.endpoint("courses", COURSE_MAPPING)
        run = self.sync_all(
            departments=[],
            classes=[{"id": "C1", "name": "ND I", "dept": "Accountancy"}],
            courses=[{"id": "K1", "code": "ACC101", "title": "Intro", "class": "C1"}],
        )
        self.assertEqual(run.status, "succeeded", run.counts)
        cls = Class.objects.get(external_id="C1")
        course = Course.objects.get(external_id="K1")

        response = self.client.patch(
            f"/api/classes/{cls.pk}/", {"is_active": False, "visa_code": "X1"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertManaged(
            self.client.patch(f"/api/classes/{cls.pk}/", {"name": "ND II"}, format="json")
        )
        # The portal lists this class's courses.
        self.assertManaged(
            self.client.post(f"/api/classes/{cls.pk}/courses/", {"code": "ACC102"}, format="json")
        )
        self.assertManaged(self.client.delete(f"/api/classes/{cls.pk}/courses/{course.pk}/"))
        self.assertManaged(
            self.client.patch(f"/api/courses/{course.pk}/", {"name": "Renamed"}, format="json")
        )
        self.assertManaged(
            self.client.patch(
                f"/api/classes/{cls.pk}/courses/{course.pk}/", {"code": "ACC9"}, format="json"
            )
        )
        response = self.client.patch(
            f"/api/courses/{course.pk}/", {"exam_type": "PBE"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)

    def test_a_mapped_exam_type_is_portal_owned(self):
        mapping = {**COURSE_MAPPING}
        self.endpoint("courses", mapping)
        self.sync_all(courses=[{"id": "K1", "code": "ACC101", "title": "Intro", "type": "CBE"}])
        course = Course.objects.get(external_id="K1")
        self.assertManaged(
            self.client.patch(f"/api/courses/{course.pk}/", {"exam_type": "PBE"}, format="json")
        )
        del mapping["exam_type"]
        self.set_mapping("courses", mapping)
        response = self.client.patch(
            f"/api/courses/{course.pk}/", {"exam_type": "PBE"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)

    # Department and faculty codes (AC-18)

    def test_a_mapped_code_is_read_only_and_a_derived_one_is_editable(self):
        self.endpoint("departments", DEPT_MAPPING)
        self.sync_all(
            departments=[
                {"id": "D1", "name": "Accountancy", "code": "AC", "faculty": "Business"},
                {"id": "D2", "name": "Computer Science", "code": "", "faculty": "Business"},
            ]
        )
        mapped = Department.objects.get(external_id="D1")
        derived = Department.objects.get(external_id="D2")
        self.assertEqual(derived.slug, "CS")

        self.assertManaged(
            self.client.patch("/api/departments/AC/", {"slug": "ACC"}, format="json")
        )
        self.assertManaged(
            self.client.patch("/api/departments/AC/", {"name": "Accounting"}, format="json")
        )
        # This row sent no code, but the column is mapped, so the portal owns
        # this kind's codes (spec 0005, *Value sourcing*, read only check).
        self.assertManaged(
            self.client.patch("/api/departments/CS/", {"slug": "CSC"}, format="json")
        )

        mapping = {k: v for k, v in DEPT_MAPPING.items() if k != "code"}
        self.set_mapping("departments", mapping)
        response = self.client.patch("/api/departments/CS/", {"slug": "CSC"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        data = self.client.get(f"/api/departments/{mapped.slug}/").data
        self.assertEqual(data["portal_locked"], ["faculty", "name"])

    def test_a_faculty_code_and_its_departments_follow_the_portal(self):
        self.endpoint("departments", DEPT_MAPPING)
        self.sync_all(
            departments=[
                {"id": "D1", "name": "Accountancy", "code": "AC", "faculty": "Business",
                 "faculty_code": "BUS"},
            ]
        )
        faculty = Faculty.objects.get(slug="BUS")
        other = Faculty.objects.create(name="Science", slug="SCI")
        dept = Department.objects.get(external_id="D1")

        self.assertManaged(
            self.client.patch("/api/faculties/BUS/", {"slug": "BIZ"}, format="json")
        )
        self.assertManaged(
            self.client.patch(
                "/api/faculties/SCI/", {"department_ids": [dept.pk]}, format="json"
            )
        )
        self.assertManaged(self.client.delete("/api/faculties/BUS/"))
        response = self.client.patch(
            f"/api/faculties/{other.slug}/", {"name": "Sciences"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertManaged(
            self.client.post("/api/faculties/", {"name": "Arts", "slug": "ART"}, format="json")
        )
        self.assertEqual(faculty.departments.get(), dept)

    # Upload lock (AC-13)

    def test_sync_follows_the_upload_lock(self):
        self.assertFalse(runs.timetable_exists())
        SystemSettings.objects.create(has_timetable=True)
        self.assertTrue(runs.timetable_exists())


# Slice 10: pruning, audit, error capture.


class PruneTests(PortalSyncTestCase):
    def test_runs_older_than_90_days_are_pruned_with_their_issues(self):
        self.ready()
        conn = PortalConnection.objects.get()
        old = SyncRun.objects.create(connection=conn, trigger="manual", status="partial")
        SyncIssue.objects.create(run=old, data_type="students", kind="bad_row", message="x")
        stuck = SyncRun.objects.create(connection=conn, trigger="manual", status="running")
        recent = SyncRun.objects.create(connection=conn, trigger="manual", status="succeeded")
        now = timezone.now()
        SyncRun.objects.filter(pk__in=[old.pk, stuck.pk]).update(started_at=now - timedelta(days=91))
        SyncRun.objects.filter(pk=recent.pk).update(started_at=now - timedelta(days=89))

        tasks.prune_sync_runs()

        self.assertEqual(
            set(SyncRun.objects.values_list("pk", flat=True)), {stuck.pk, recent.pk}
        )
        self.assertFalse(SyncIssue.objects.exists())


class AuditTests(PortalSyncTestCase):
    """Connection changes, the secret, approve, confirm and resolve each
    write a named ``AuditLog`` row, never with the secret (AC-16)."""

    seat = DeleteTests.seat
    held_issue = DeleteTests.held_issue
    resolve = DeleteTests.resolve
    confirm = DeleteTests.confirm

    def rows(self, **filters):
        return AuditLog.objects.filter(object_type__startswith="portal", **filters)

    def assert_no_secret(self):
        dump = json.dumps(list(AuditLog.objects.values("action", "metadata", "path")))
        self.assertNotIn(SECRET, dump)
        self.assertNotIn("NEW_SECRET", dump)

    def test_connection_create_change_and_secret(self):
        self.connect()
        self.client.put("/api/portal-sync/connection/", {"header_name": "X-Key"}, format="json")
        self.client.put("/api/portal-sync/connection/", {"secret": "sk_NEW_SECRET_123"}, format="json")
        self.client.put(
            "/api/portal-sync/connection/",
            {"auth_type": "bearer", "secret": "sk_NEW_SECRET_456"},
            format="json",
        )

        actions = list(self.rows().order_by("id").values_list("action", "metadata"))
        self.assertEqual(
            actions,
            [
                ("Created portal connection", {}),
                ("Updated portal connection", {"changed": ["header_name"], "secret_changed": False}),
                ("Changed portal connection secret", {"changed": [], "secret_changed": True}),
                (
                    "Updated portal connection and changed its secret",
                    {"changed": ["auth_type", "header_name"], "secret_changed": True},
                ),
            ],
        )
        self.assertEqual(self.rows().first().object_type, "portal connection")
        self.assert_no_secret()

    def test_endpoint_changes_are_named(self):
        endpoint_id = self.ready()
        self.client.delete(f"/api/portal-sync/endpoints/{endpoint_id}/")

        actions = list(self.rows(object_type="portal endpoint").order_by("id").values_list("action", flat=True))
        self.assertEqual(
            actions,
            [
                "Added portal endpoint (students)",
                "Updated portal endpoint (students)",
                "Removed portal endpoint (students)",
            ],
        )
        updated = self.rows(action="Updated portal endpoint (students)").get()
        self.assertEqual(updated.metadata, {"changed": ["field_mapping"]})

    def test_approve_confirm_and_resolve(self):
        dept = make_department("ac")
        dept.name = "Accountancy"
        dept.save()
        make_class(0, dept, name="ND I")
        self.ready()
        preview = self.sync([row(n) for n in range(1, 11)], preview=True)
        approval = self.run_sync(
            serve({"data": [row(n) for n in range(1, 11)]}),
            preview=True,
            path=f"/api/portal-sync/runs/{preview.pk}/approve/",
        )
        self.seat("M0001")
        held = self.held_issue(self.sync([row(n) for n in range(2, 11)]))
        self.resolve(held, "keep")
        mass = self.sync([])
        confirmed = self.confirm(mass, ["students"])

        approve = self.rows(action=f"Approved portal sync preview #{preview.pk}").get()
        self.assertEqual(approve.metadata, {"new_run_id": approval.pk})
        resolve = self.rows(object_type="portal sync issue").get()
        self.assertEqual(resolve.action, "Resolved held students delete as keep")
        self.assertEqual(resolve.object_id, str(held.pk))
        self.assertNotIn("M0001", json.dumps(resolve.metadata))
        confirm = self.rows(action=f"Confirmed portal sync mass delete for run #{mass.pk}").get()
        self.assertEqual(
            confirm.metadata, {"data_types": ["students"], "new_run_id": confirmed.pk}
        )
        self.assertEqual(confirm.status_code, 202)


class CaptureTests(PortalSyncTestCase):
    """A failed run is recorded through spec 0004: the portal failing is
    ``expected``, a crash is an error; neither carries row data (AC-16)."""

    def setUp(self):
        super().setUp()
        self.dept = make_department("ac")
        self.dept.name = "Accountancy"
        self.dept.save()
        make_class(0, self.dept, name="ND I")
        self.ready()

    def test_a_portal_failure_is_captured_as_expected(self):
        with mock.patch("ems.portal_sync.runs.job_failed") as captured:
            run = self.run_sync(serve("down", status=503))

        self.assertEqual(run.status, "failed")
        job, exc = captured.call_args.args
        self.assertEqual(job.pk, run.job.pk)
        self.assertIsInstance(exc, fetch.FetchError)
        self.assertTrue(captured.call_args.kwargs["expected"])
        self.assertNotIn(SECRET, run.error + json.dumps(run.job.result_data))

    def test_a_crash_is_captured_as_an_error_without_row_data(self):
        boom = RuntimeError("could not save")
        apply = (("students", mock.Mock(side_effect=boom)),)
        with mock.patch("ems.portal_sync.runs.job_failed") as captured, mock.patch(
            "ems.portal_sync.runs._APPLY", apply
        ):
            run = self.sync([row(1)])

        self.assertEqual(run.status, "failed")
        self.assertIs(captured.call_args.args[1], boom)
        self.assertFalse(captured.call_args.kwargs["expected"])
        self.assertEqual(run.job.result_data, {"run_id": run.pk, "status": "failed"})
        self.assertNotIn("M0001", run.error + json.dumps(run.job.result_data))

    def test_a_clean_run_captures_nothing(self):
        with mock.patch("ems.portal_sync.runs.job_failed") as captured:
            self.sync([row(1)])

        captured.assert_not_called()


class DisconnectTests(PortalSyncTestCase):
    """Deleting the connection hands every synced record back to Ordo,
    removes the endpoints and keeps the run history (slice 11, AC-17)."""

    seat = DeleteTests.seat
    held_issue = DeleteTests.held_issue
    resolve = DeleteTests.resolve

    def setUp(self):
        super().setUp()
        self.dept = make_department("ac")
        self.dept.name = "Accountancy"
        self.dept.save()
        self.nd1 = make_class(0, self.dept, name="ND I")
        self.ready()

    def disconnect(self):
        return self.client.delete("/api/portal-sync/connection/")

    def test_disconnect_releases_every_synced_record_and_keeps_runs(self):
        run = self.sync([row(n) for n in range(1, 4)])
        # Students only syncs link no structure; mark some as if they did.
        Faculty.objects.filter(pk=self.dept.faculty_id).update(external_id="F1")
        Department.objects.filter(pk=self.dept.pk).update(external_id="D1")
        Class.objects.filter(pk=self.nd1.pk).update(external_id="C1")
        make_course("ACC101")
        Course.objects.update(external_id="K1")

        response = self.disconnect()

        self.assertEqual(response.status_code, 204)
        for model in (Faculty, Department, Class, Course, Student):
            self.assertFalse(model.objects.filter(external_id__isnull=False).exists(), model)
        self.assertEqual(Student.objects.count(), 3)
        self.assertFalse(PortalConnection.objects.exists())
        self.assertFalse(PortalEndpoint.objects.exists())
        run.refresh_from_db()
        self.assertIsNone(run.connection_id)
        self.assertEqual(run.status, "succeeded")
        self.assertEqual(self.client.get("/api/portal-sync/runs/").data["count"], 1)
        self.assertEqual(self.client.get("/api/portal-sync/connection/").status_code, 404)

    def test_released_records_are_editable_again(self):
        self.sync([row(1)])
        student = Student.objects.get(external_id="P1")

        self.disconnect()

        response = self.client.patch(
            f"/api/students/{student.pk}/", {"first_name": "Changed"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        data = self.client.get(f"/api/students/{student.pk}/").data
        self.assertFalse(data["from_portal"])

    def test_a_reconnect_links_released_records_again_by_natural_key(self):
        self.sync([row(1)])
        self.disconnect()

        self.ready()
        run = self.sync([row(1)])

        self.assertEqual(run.counts["students"]["linked"], 1)
        self.assertEqual(Student.objects.count(), 1)

    def test_disconnect_is_refused_while_a_sync_runs(self):
        self.sync([row(1)])
        SyncRun.objects.create(
            connection=PortalConnection.objects.get(),
            trigger="manual",
            is_preview=False,
            status=SyncRun.Status.RUNNING,
            started_by=self.admin,
        )

        response = self.disconnect()

        self.assertEqual(response.status_code, 409)
        self.assertTrue(PortalConnection.objects.exists())
        self.assertTrue(Student.objects.filter(external_id="P1").exists())

    def test_disconnect_without_a_connection_is_404(self):
        self.disconnect()

        self.assertEqual(self.disconnect().status_code, 404)

    def test_other_roles_and_plans_without_the_feature_are_refused(self):
        officer = User.objects.create_user(
            email="fo@test.localhost", password=PASSWORD, role=User.Role.FACULTY_OFFICER
        )
        self.client.force_authenticate(officer)
        self.assertEqual(self.disconnect().status_code, 403)

        self.client.force_authenticate(self.admin)
        self.set_school(features_override=["exports"])
        response = self.disconnect()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "feature_disabled")
        self.assertTrue(PortalConnection.objects.exists())

    def test_a_waiting_preview_is_superseded(self):
        preview = self.sync([row(1)], preview=True)
        self.assertEqual(preview.status, "awaiting_approval")

        self.disconnect()

        preview.refresh_from_db()
        self.assertEqual(preview.status, "superseded")
        self.ready()
        response = self.client.post(f"/api/portal-sync/runs/{preview.pk}/approve/")
        self.assertEqual(response.status_code, 409)

    def test_a_held_delete_cant_be_resolved_after_disconnect(self):
        self.sync([row(n) for n in range(1, 11)])
        self.seat("M0001")
        issue = self.held_issue(self.sync([row(n) for n in range(2, 11)]))

        self.disconnect()

        self.assertEqual(self.resolve(issue, "delete").status_code, 409)
        self.assertTrue(Student.objects.filter(matric_no="M0001").exists())

    def test_a_mass_delete_from_before_a_reconnect_cant_be_confirmed(self):
        self.sync([row(n) for n in range(1, 11)])
        mass = self.sync([])
        self.assertEqual(mass.status, "held")

        self.disconnect()
        self.ready()

        response = self.client.post(
            f"/api/portal-sync/runs/{mass.pk}/confirm-mass-delete/",
            {"data_types": ["students"]},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Student.objects.count(), 10)

    def test_disconnect_is_audited_with_counts_only(self):
        self.sync([row(n) for n in range(1, 3)])
        connection_id = PortalConnection.objects.get().pk

        self.disconnect()

        entry = AuditLog.objects.get(action="Deleted portal connection")
        self.assertEqual(entry.object_type, "portal connection")
        self.assertEqual(entry.object_id, str(connection_id))
        self.assertEqual(entry.metadata["released"]["students"], 2)
        self.assertNotIn(SECRET, json.dumps(entry.metadata))
        self.assertNotIn("M0001", json.dumps(entry.metadata))
