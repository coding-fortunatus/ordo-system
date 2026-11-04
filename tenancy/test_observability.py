"""Per school error observability (spec 0004): capture, privacy, grouping,
sampling, health status, alerts, the console API, ingest and retention."""
import json
import logging
from datetime import timedelta
from unittest import mock

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.core import mail
from django.db import IntegrityError, connection, transaction
from django.http import JsonResponse
from django.test import override_settings
from django.urls import Resolver404, include, path, resolve
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework.authtoken.models import Token

from core.asgi import application
from ems.models import BackgroundJob, User
from ems.readiness import NotReady
from tenancy.models import (
    ErrorEvent,
    ErrorGroup,
    HealthBucket,
    Plan,
    PlatformAdmin,
    PlatformAuditLog,
    School,
    SchoolHealth,
)
from tenancy.observability import alerts, capture, counters, health, scrub, signals
from tenancy.observability.tasks import observe_health, prune_observability, purge_school
from tenancy.tasks import send_email
from tenancy.test_alerts import AlertTestCase
from tenancy.test_school_lifecycle import run_inline
from tenancy.test_platform_auth import CONSOLE
from tenancy.testcases import SCHOOL_TEST_HOST, SchoolClient, SocketTestMixin, SchoolTestCase


def boom(request):
    return 1 / 0


def rollback(request):
    with transaction.atomic():
        Plan.objects.create(code="rolled-back", name="Rolled back")
        raise ValueError("written, then rolled back")


def ok(request):
    return JsonResponse({"ok": True})


def bad(request):
    return JsonResponse({"detail": "No."}, status=400)


urlpatterns = [
    path("api/boom/", boom),
    path("api/rollback/", rollback),
    path("api/ok/", ok),
    path("api/bad/", bad),
    path("", include("core.urls")),
]


class ObservabilityMixin:
    def setUp(self):
        super().setUp()
        self.redis = counters.MemoryRedis()
        counters.set_client(self.redis)

    def tearDown(self):
        counters.set_client(None)
        super().tearDown()

    def hour_counts(self, school_id):
        key = counters.hour_key(counters.school_key(school_id), timezone.now())
        return {k: int(v) for k, v in self.redis.hgetall(key).items()}

    def officer(self, email="officer@example.com", role=User.Role.SUPER_ADMIN):
        user = User.objects.create_user(email=email, password="x", role=role, is_staff=True)
        return user, Token.objects.get_or_create(user=user)[0].key


def quiet_admin_emails(test):
    """Django's own 500 mail handler: off, as it has no ADMINS to mail here.
    (Django 5.0 can't render its report on Python 3.14; prod runs 3.11.)"""
    test.enterContext(mock.patch("django.utils.log.AdminEmailHandler.emit"))


@override_settings(ROOT_URLCONF=__name__)
class RequestCaptureTests(ObservabilityMixin, SchoolTestCase):
    def setUp(self):
        super().setUp()
        quiet_admin_emails(self)
        self.client.raise_request_exception = False

    def test_a_500_is_grouped_with_its_request_id_and_counted_again(self):
        # covers: AC-1, AC-11
        response = self.client.get("/api/boom/")
        self.assertEqual(response.status_code, 500)
        request_id = response["X-Request-ID"]
        self.assertRegex(request_id, r"^[0-9a-f]{32}$")
        group = ErrorGroup.objects.get()
        self.assertEqual((group.school_id, group.count, group.source), (self.tenant.pk, 1, "api"))
        self.assertEqual(group.exc_type, "builtins.ZeroDivisionError")
        event = group.events.get()
        self.assertEqual(event.context["request_id"], request_id)
        self.assertEqual(event.context["route"], "api/boom/")
        self.assertEqual(event.context["method"], "GET")
        self.assertTrue(event.stack[-1]["file"].startswith("tenancy/"))

        self.client.get("/api/boom/")
        group.refresh_from_db()
        self.assertEqual(group.count, 2)

    def test_the_same_bug_in_another_school_is_another_group(self):
        # covers: AC-11
        with schema_context(get_public_schema_name()):
            other = School.objects.create(
                schema_name="school_other", slug="other", name="Other", plan=Plan.objects.get(code="platform"),
                first_admin_name="O", first_admin_email="o@o.test",
            )
        self.client.get("/api/boom/")
        with mock.patch.object(capture, "current_school_id", return_value=other.pk):
            self.client.get("/api/boom/")
        groups = ErrorGroup.objects.order_by("school_id")
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].fingerprint, groups[1].fingerprint)

    def test_a_valid_incoming_request_id_is_kept(self):
        # covers: AC-1
        rid = "a" * 32
        response = self.client.get("/api/ok/", HTTP_X_REQUEST_ID=rid)
        self.assertEqual(response["X-Request-ID"], rid)
        response = self.client.get("/api/ok/", HTTP_X_REQUEST_ID="not-a-request-id")
        self.assertNotEqual(response["X-Request-ID"], "not-a-request-id")

    def test_every_api_response_counts_and_4xx_count_as_client_errors(self):
        # covers: AC-7
        self.client.get("/api/ok/")
        self.client.get("/api/bad/")
        self.assertEqual(self.hour_counts(self.tenant.pk), {"requests": 2, "client_errors": 1})

    def test_an_error_in_the_public_schema_lands_in_platform(self):
        # covers: AC-1, AC-2
        connection.set_schema_to_public()
        try:
            1 / 0
        except ZeroDivisionError as exc:
            capture.capture_exception(exc, "task")
        connection.set_tenant(self.tenant)
        self.assertIsNone(ErrorGroup.objects.get().school_id)

    def test_capture_failure_leaves_the_response_alone(self):
        # covers: AC-9
        with mock.patch.object(capture, "record", side_effect=RuntimeError("db down")), \
                self.assertLogs("ordo.observability", "ERROR"):
            self.assertEqual(self.client.get("/api/boom/").status_code, 500)
        self.assertEqual(self.client.get("/api/ok/").status_code, 200)

    def test_redis_down_still_stores_the_event_and_responses_are_normal(self):
        # covers: AC-9, AC-10
        broken = mock.Mock()
        broken.pipeline.side_effect = ConnectionError("redis down")
        counters.set_client(broken)
        with self.assertLogs("ordo.observability", "WARNING"):
            self.assertEqual(self.client.get("/api/ok/").status_code, 200)
            counters.set_client(broken)
            self.assertEqual(self.client.get("/api/boom/").status_code, 500)
        self.assertEqual(ErrorEvent.objects.count(), 1)

    @override_settings(OBSERVABILITY_ENABLED=False)
    def test_the_master_switch_turns_everything_off(self):
        # covers: AC-9
        self.assertEqual(self.client.get("/api/boom/").status_code, 500)
        self.assertFalse(ErrorGroup.objects.exists())
        self.assertEqual(self.hour_counts(self.tenant.pk), {})


@override_settings(ROOT_URLCONF=__name__, OBSERVABILITY_DB_ALIAS="observability")
class RollbackTests(ObservabilityMixin, SchoolTestCase):
    """Through the real ``observability`` connection, which commits on its own."""

    databases = {"default", "observability"}

    def setUp(self):
        super().setUp()
        quiet_admin_emails(self)

    def tearDown(self):
        ErrorEvent.objects.using("observability").all().delete()
        ErrorGroup.objects.using("observability").all().delete()
        super().tearDown()

    def test_an_error_inside_a_rolled_back_transaction_is_still_stored(self):
        # covers: AC-9
        self.client.raise_request_exception = False
        self.assertEqual(self.client.get("/api/rollback/").status_code, 500)
        self.assertFalse(Plan.objects.filter(code="rolled-back").exists())
        self.assertEqual(ErrorEvent.objects.using("observability").count(), 1)


class PrivacyTests(ObservabilityMixin, SchoolTestCase):
    def test_an_integrity_error_keeps_neither_matric_number_nor_name(self):
        # covers: AC-8
        message = (
            'duplicate key value violates unique constraint "ems_student_matric_no_key"\n'
            "DETAIL:  Key (matric_no)=(190403021) already exists.\n"
            'Student "Adaeze Okafor" could not be saved'
        )
        try:
            raise IntegrityError(message)
        except IntegrityError as exc:
            capture.capture_exception(exc, "api")
        event = ErrorEvent.objects.get()
        for text in (event.message, event.group.title):
            self.assertNotIn("190403021", text)
            self.assertNotIn("Adaeze", text)
            self.assertNotIn("Okafor", text)

    def test_the_scrubber_masks_contacts_numbers_and_tokens(self):
        # covers: AC-8
        text = scrub.scrub(
            "ada@school.edu.ng called +234 803 123 4567 with key 9f8e7d6c5b4a39281706aa and id 123456 "
            "Key (email)=(x@y.z) 'secret'"
        )
        for leaked in ("ada@school", "803 123", "9f8e7d6c5b4a", "123456", "x@y.z", "secret"):
            self.assertNotIn(leaked, text)
        self.assertEqual(len(scrub.scrub("x" * 5000)), 1000)

    def test_context_holds_only_the_allowlist(self):
        # covers: AC-8
        context = capture.build_context(method="GET", body="secret", query="q=1", user_id=4)
        self.assertEqual(context, {"method": "GET", "user_id": 4})


class JobCaptureTests(ObservabilityMixin, SchoolTestCase):
    def job(self, **fields):
        user = User.objects.create_user(email=f"u{BackgroundJob.objects.count()}@x.test", password="x")
        return BackgroundJob.objects.create(
            job_id=f"job-{BackgroundJob.objects.count()}", job_type="allocation", created_by=user, **fields
        )

    def not_ready(self):
        exc = NotReady.__new__(NotReady)
        Exception.__init__(exc, "These active classes have no students uploaded: 100L.")
        exc.report = {}
        return exc

    def fail(self, job, exc):
        job.status = "failed"
        job.error_message = str(exc)
        capture.job_failed(job, exc)
        job.save()

    def test_a_not_ready_job_is_expected_and_leaves_the_school_healthy(self):
        # covers: AC-3, AC-12
        job = self.job()
        self.fail(job, self.not_ready())
        group = ErrorGroup.objects.get()
        self.assertEqual((group.source, group.kind), ("job", "expected"))
        self.assertEqual(group.events.get().context["job_id"], job.job_id)
        health.observe()
        self.assertEqual(SchoolHealth.objects.get(school=self.tenant).status, "healthy")
        self.assertEqual(self.hour_counts(self.tenant.pk), {"expected_job_failures": 1})

    def test_a_crashing_job_is_an_error_with_its_stack_once(self):
        # covers: AC-3, AC-4
        job = self.job()
        try:
            {}["missing"]
        except KeyError as exc:
            self.fail(job, exc)
            signals.task_failed(sender=mock.Mock(name="t"), exception=exc)
        group = ErrorGroup.objects.get()
        self.assertEqual((group.kind, group.count, group.culprit), ("error", 1, "job:allocation"))
        self.assertTrue(group.events.get().stack)

    def test_a_failed_job_no_site_reported_is_caught_by_the_safety_net(self):
        # covers: AC-3
        job = self.job()
        job.status = "failed"
        job.error_message = "Something new went wrong"
        job.save()
        job.save()  # Saving it again is not a second failure.
        group = ErrorGroup.objects.get()
        self.assertEqual((group.exc_type, group.count, group.events.get().stack), ("JobFailed", 1, []))

    def test_stale_jobs_failed_in_bulk_are_recorded(self):
        # covers: AC-3
        from ems.api.views.jobs import fail_stale_jobs

        job = self.job(status="running")
        BackgroundJob.objects.filter(pk=job.pk).update(started_at=timezone.now() - timedelta(hours=2))
        self.assertEqual(fail_stale_jobs(), 1)
        self.assertEqual(ErrorGroup.objects.get().source, "job")

    def test_a_logged_then_raised_exception_in_a_task_is_one_occurrence(self):
        # covers: AC-2, AC-4
        task = mock.Mock()
        task.name = "ems.tasks.example"
        try:
            try:
                raise RuntimeError("broken")
            except RuntimeError:
                logging.getLogger("ems.tasks").exception("Example failed")
                raise
        except RuntimeError as exc:
            signals.task_failed(sender=task, exception=exc)
        group = ErrorGroup.objects.get()
        self.assertEqual((group.count, group.source, group.school_id), (1, "log", self.tenant.pk))

    def test_a_task_failure_records_its_name(self):
        # covers: AC-2
        task = mock.Mock()
        task.name = "tenancy.tasks.snapshot_usage"
        try:
            raise RuntimeError("beat broke")
        except RuntimeError as exc:
            signals.task_failed(sender=task, exception=exc)
        event = ErrorEvent.objects.get()
        self.assertEqual((event.group.source, event.context["task_name"]), ("task", task.name))

    def test_a_log_line_without_an_exception_groups_by_its_format(self):
        # covers: AC-4
        log = logging.getLogger("tenancy.example")
        log.error("Could not reach %s", "ada@school.edu.ng")
        log.error("Could not reach %s", "obi@school.edu.ng")
        group = ErrorGroup.objects.get()
        self.assertEqual((group.count, group.exc_type), (2, "LogMessage"))
        self.assertNotIn("ada@", group.events.first().message)


class SamplingTests(ObservabilityMixin, SchoolTestCase):
    def test_250_occurrences_in_an_hour_store_12_events(self):
        # covers: AC-10
        for _ in range(250):
            try:
                1 / 0
            except ZeroDivisionError as exc:
                capture.capture_exception(exc, "api")
        group = ErrorGroup.objects.get()
        self.assertEqual(group.count, 250)
        self.assertEqual(group.events.count(), 12)


class SocketTests(ObservabilityMixin, SocketTestMixin, SchoolTestCase):
    def test_a_refused_socket_counts_as_a_websocket_error(self):
        # covers: AC-6
        async def scenario():
            ws = WebsocketCommunicator(
                application,
                "/ws/notifications/?token=nope",
                headers=[
                    (b"host", SCHOOL_TEST_HOST.encode()),
                    (b"origin", f"http://{SCHOOL_TEST_HOST}:3000".encode()),
                ],
            )
            connected, _ = await ws.connect()
            self.assertFalse(connected)

        async_to_sync(scenario)()
        self.assertEqual(self.hour_counts(self.tenant.pk), {"ws_errors": 1})

    def test_an_exception_escaping_a_consumer_is_captured(self):
        # covers: AC-6
        user, token = self.officer()

        async def scenario():
            ws = WebsocketCommunicator(
                application,
                f"/ws/notifications/?token={token}",
                headers=[
                    (b"host", SCHOOL_TEST_HOST.encode()),
                    (b"origin", f"http://{SCHOOL_TEST_HOST}:3000".encode()),
                ],
            )
            await ws.connect()
            with self.assertRaises(ZeroDivisionError):
                await ws.receive_json_from()

        with mock.patch("ems.api.consumers.unread_count", side_effect=ZeroDivisionError):
            async_to_sync(scenario)()
        group = ErrorGroup.objects.get()
        self.assertEqual((group.source, group.school_id), ("ws", self.tenant.pk))


class IngestTests(ObservabilityMixin, SchoolTestCase):
    url = "/api/telemetry/errors/"

    def report(self, client=None, **overrides):
        body = {
            "name": "TypeError",
            "message": "Cannot read properties of undefined (reading 'name')",
            "stack": "TypeError: x\n    at render (https://test.localhost/assets/index-3f9a1c2b.js?v=1:1:2345)",
            "path": "/students?search=ada#top",
            "release": "abc123",
            **overrides,
        }
        return (client or self.client).post(self.url, json.dumps(body), content_type="application/json")

    def test_a_report_is_stored_as_a_browser_error(self):
        # covers: AC-5
        self.assertEqual(self.report(HTTP_USER_AGENT="Firefox").status_code, 204)
        event = ErrorEvent.objects.get()
        self.assertEqual((event.group.source, event.release), ("js", "abc123"))
        self.assertEqual(event.context["page_path"], "/students")
        self.assertEqual(event.stack[-1]["file"], "index-3f9a1c2b.js")
        self.assertNotIn("user_id", event.context)
        self.assertEqual(self.hour_counts(self.tenant.pk)["js_errors"], 1)

    def test_a_valid_school_token_attaches_the_user(self):
        # covers: AC-5
        user, token = self.officer()
        self.report(SchoolClient(HTTP_AUTHORIZATION=f"Token {token}"))
        context = ErrorEvent.objects.get().context
        self.assertEqual((context["user_id"], context["role"]), (user.pk, user.role))

    def test_the_21st_report_in_a_minute_from_one_ip_is_dropped(self):
        # covers: AC-5
        for _ in range(20):
            self.assertEqual(self.report().status_code, 204)
        self.assertEqual(self.report().status_code, 204)
        self.assertEqual(ErrorGroup.objects.get().count, 20)

    def test_an_ip_over_its_limit_does_not_silence_the_school(self):
        # Security audit F12: reports refused by the per IP limit must not
        # spend the per school budget.
        from django.conf import settings

        attacker = SchoolClient(REMOTE_ADDR="198.51.100.66")
        for _ in range(settings.OBS_JS_SCHOOL_PER_MIN + 1):
            self.report(attacker)
        self.report(SchoolClient(REMOTE_ADDR="203.0.113.200"), name="Real")
        self.assertEqual(ErrorGroup.objects.count(), 2)

    def test_too_large_and_malformed_reports_are_refused(self):
        # covers: AC-5
        self.assertEqual(self.report(message="x" * 17 * 1024).status_code, 413)
        self.assertEqual(
            self.client.post(self.url, "not json", content_type="application/json").status_code, 400
        )
        self.assertEqual(self.report(name=None).status_code, 400)
        self.assertFalse(ErrorGroup.objects.exists())

    def test_a_report_on_the_console_lands_in_platform(self):
        # covers: AC-5
        from django.core.management import call_command
        from io import StringIO

        call_command("ensure_public_domains", stdout=StringIO())
        self.assertEqual(self.report(SchoolClient(HTTP_HOST=CONSOLE)).status_code, 204)
        self.assertIsNone(ErrorGroup.objects.get().school_id)
        connection.set_tenant(self.tenant)


def make_group(school, **fields):
    now = timezone.now()
    defaults = dict(
        fingerprint=f"fp{ErrorGroup.objects.count()}", source="api", kind="error", exc_type="builtins.KeyError",
        title="KeyError: x", culprit="api/x/", first_seen=now, last_seen=now, last_opened_at=now,
    )
    return ErrorGroup.objects.create(school=school, **{**defaults, **fields})


class StatusAndAlertTests(ObservabilityMixin, AlertTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(run_inline(send_email))
        mail.outbox.clear()

    def observe(self):
        with self.captureOnCommitCallbacks(execute=True):
            observe_health()

    def sweep(self):
        with self.captureOnCommitCallbacks(execute=True):
            alerts.sweep()

    def test_20_server_errors_make_a_school_failing_and_send_one_spike_email(self):
        # covers: AC-12, AC-18
        counters.incr(self.tenant.pk, server_errors=20)
        self.observe()
        row = SchoolHealth.objects.get(school=self.tenant)
        self.assertEqual((row.status, row.server_errors), ("failing", 20))
        self.assertIn("20 server errors", row.reason)
        spikes = [m for m in self.alerts_sent() if "is failing" in m.subject]
        self.assertEqual(len(spikes), 1)

        SchoolHealth.objects.filter(school=self.tenant).update(status="healthy")
        self.observe()
        self.assertEqual(len([m for m in self.alerts_sent() if "is failing" in m.subject]), 1)

    def test_one_error_degrades_and_quiet_is_healthy(self):
        # covers: AC-12
        self.observe()
        self.assertEqual(SchoolHealth.objects.get(school=self.tenant).status, "healthy")
        counters.incr(self.tenant.pk, server_errors=1)
        self.observe()
        self.assertEqual(SchoolHealth.objects.get(school=self.tenant).status, "degraded")
        self.assertTrue(SchoolHealth.objects.filter(school=None).exists())

    def test_a_4xx_spike_is_measured_against_the_usual_rate(self):
        # covers: AC-12
        last_week = health.hour_start(timezone.now()) - timedelta(days=2)
        # With the empty previous hour observe_health writes, the usual rate is 50.
        HealthBucket.objects.create(school=self.tenant, hour=last_week, client_errors=100)
        counters.incr(self.tenant.pk, client_errors=120)
        self.observe()
        self.assertEqual(SchoolHealth.objects.get(school=self.tenant).status, "healthy")
        counters.incr(self.tenant.pk, client_errors=40)
        self.observe()
        self.assertIn("4xx", SchoolHealth.objects.get(school=self.tenant).reason)

    def test_buckets_are_set_not_added_on_a_second_run(self):
        # covers: AC-13
        counters.incr(self.tenant.pk, requests=7)
        self.observe()
        self.observe()
        bucket = HealthBucket.objects.get(school=self.tenant, hour=health.hour_start(timezone.now()))
        self.assertEqual(bucket.requests, 7)

    def test_three_new_groups_give_one_email_now_and_one_after_the_cooldown(self):
        # covers: AC-17
        make_group(self.tenant)
        self.observe()
        self.assertEqual(len([m for m in self.alerts_sent() if "new error" in m.subject]), 1)
        make_group(self.tenant)
        make_group(self.tenant)
        self.observe()
        self.assertEqual(len(self.alerts_sent()), 2)  # the first, plus the spike
        self.redis.delete(alerts.cooldown_key(counters.school_key(self.tenant.pk)))
        self.observe()
        combined = [m for m in self.alerts_sent() if "2 new errors" in m.subject]
        self.assertEqual(len(combined), 1)

    def test_a_resolved_group_that_returns_alerts_again_but_ignored_never_does(self):
        # covers: AC-11, AC-17
        connection.set_tenant(self.tenant)
        try:
            1 / 0
        except ZeroDivisionError as exc:
            capture.capture_exception(exc, "api")
        group = ErrorGroup.objects.get()
        self.sweep()
        group.refresh_from_db()
        self.assertIsNotNone(group.alerted_at)
        ErrorGroup.objects.filter(pk=group.pk).update(status="resolved", resolved_at=timezone.now())
        try:
            1 / 0
        except ZeroDivisionError as exc:
            capture.capture_exception(exc, "api")
        group.refresh_from_db()
        self.assertEqual(group.status, "open")
        self.assertIsNone(group.resolved_at)
        self.assertGreater(group.last_opened_at, group.alerted_at)
        self.redis.delete(alerts.cooldown_key(counters.school_key(self.tenant.pk)))
        self.sweep()
        self.assertEqual(len([m for m in self.alerts_sent() if "came back" in m.subject]), 1)

        ignored = make_group(self.tenant, status="ignored")
        self.redis.delete(alerts.cooldown_key(counters.school_key(self.tenant.pk)))
        self.sweep()
        ignored.refresh_from_db()
        self.assertIsNone(ignored.alerted_at)

    def test_six_schools_alerting_at_once_send_one_incident_email(self):
        # covers: AC-19
        plan = Plan.objects.get(code="platform")
        for i in range(6):
            school = School.objects.create(
                schema_name=f"school_s{i}", slug=f"s{i}", name=f"School {i}", plan=plan,
                status="active", first_admin_name="A", first_admin_email=f"a{i}@x.test",
            )
            make_group(school)
        self.sweep()
        subjects = [m.subject for m in self.alerts_sent()]
        self.assertEqual(sum("Platform wide incident" in s for s in subjects), 1)
        self.assertEqual(sum("new error" in s for s in subjects), 5)
        self.assertEqual(ErrorGroup.objects.filter(alerted_at__isnull=True).count(), 1)

    def test_nothing_is_sent_while_redis_is_down(self):
        # covers: AC-9, AC-17
        make_group(self.tenant)
        broken = mock.Mock()
        broken.get.side_effect = ConnectionError("down")
        counters.set_client(broken)
        with self.assertLogs("ordo.observability", "WARNING"):
            self.observe()
        self.assertEqual(self.alerts_sent(), [])


class ConsoleApiTests(ObservabilityMixin, AlertTestCase):
    def test_the_health_page_lists_schools_worst_first(self):
        # covers: AC-14
        now = timezone.now()
        SchoolHealth.objects.create(school=self.tenant, status="failing", reason="r", server_errors=30, computed_at=now)
        SchoolHealth.objects.create(school=None, status="healthy", reason="", computed_at=now)
        make_group(self.tenant)
        rows = self.owner.get("/api/platform/health/").data["results"]
        self.assertEqual(rows[0]["school"]["id"], self.tenant.pk)
        self.assertEqual((rows[0]["health"], rows[0]["open_groups"]), ("failing", 1))
        self.assertIsNone(rows[1]["school"])

    def test_a_school_health_has_168_zero_filled_buckets(self):
        # covers: AC-15
        data = self.owner.get(f"/api/platform/schools/{self.tenant.pk}/health/").data
        self.assertEqual(len(data["buckets"]), 168)
        self.assertIsNone(data["health"])
        self.assertEqual(len(self.owner.get("/api/platform/health/platform/").data["buckets"]), 168)

    def test_groups_filter_and_events_page_newest_first(self):
        # covers: AC-15
        group = make_group(self.tenant)
        make_group(self.tenant, status="resolved")
        make_group(None)
        base = f"/api/platform/errors/?school={self.tenant.pk}"
        self.assertEqual(self.owner.get(base).data["count"], 2)
        self.assertEqual(self.owner.get(base + "&status=open").data["count"], 1)
        self.assertEqual(self.owner.get("/api/platform/errors/?school=platform").data["count"], 1)
        self.assertEqual(self.owner.get("/api/platform/errors/").status_code, 400)
        self.assertEqual(self.owner.get(base + "&source=nope").status_code, 400)
        now = timezone.now()
        for minutes in (5, 1):
            ErrorEvent.objects.create(group=group, school=self.tenant, occurred_at=now - timedelta(minutes=minutes),
                                      message=f"{minutes}", stack=[], context={})
        events = self.owner.get(f"/api/platform/errors/{group.pk}/events/").data["results"]
        self.assertEqual([e["message"] for e in events], ["1", "5"])
        self.assertEqual(self.owner.get(f"/api/platform/errors/{group.pk}/").data["school"]["id"], self.tenant.pk)

    def test_support_can_resolve_a_group_and_it_is_audited(self):
        # covers: AC-16
        group = make_group(self.tenant)
        support = self.sign_in(self.make_admin("support@ordo.app", role=PlatformAdmin.Role.SUPPORT))
        url = f"/api/platform/errors/{group.pk}/status/"
        response = support.post(url, {"status": "resolved"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["status"], response.data["resolved_by"]), ("resolved", "support"))
        log = PlatformAuditLog.objects.get(action="error_group.resolve")
        self.assertEqual((log.school_id, log.detail), (self.tenant.pk, {"group_id": group.pk}))
        support.post(url, {"status": "resolved"}, format="json")
        self.assertEqual(PlatformAuditLog.objects.filter(action__startswith="error_group").count(), 1)
        self.assertEqual(support.post(url, {"status": "gone"}, format="json").status_code, 400)

    def test_school_tokens_and_school_hosts_get_nothing(self):
        # covers: AC-16
        connection.set_tenant(self.tenant)
        user, token = self.officer()
        school_token_on_console = SchoolClient(HTTP_HOST=CONSOLE, HTTP_AUTHORIZATION=f"Token {token}")
        self.assertEqual(school_token_on_console.get("/api/platform/errors/?school=platform").status_code, 401)
        self.assertEqual(SchoolClient(HTTP_HOST=CONSOLE).get("/api/platform/health/").status_code, 401)
        # Not routed on a school host at all, so it gets the plain not found.
        for route in ("/api/platform/errors/", "/api/platform/health/"):
            with self.assertRaises(Resolver404):
                resolve(route, urlconf="core.urls")
        connection.set_tenant(self.tenant)


class RetentionTests(ObservabilityMixin, SchoolTestCase):
    def test_old_events_and_closed_groups_go_open_groups_stay(self):
        # covers: AC-20
        now = timezone.now()
        old = now - timedelta(days=120)
        kept_open = make_group(self.tenant, first_seen=old, last_seen=old, last_opened_at=old)
        gone = make_group(self.tenant, status="resolved", first_seen=old, last_seen=old, last_opened_at=old)
        ErrorEvent.objects.create(group=kept_open, school=self.tenant, occurred_at=now - timedelta(days=31),
                                  message="", stack=[], context={})
        fresh = ErrorEvent.objects.create(group=kept_open, school=self.tenant, occurred_at=now,
                                          message="", stack=[], context={})
        HealthBucket.objects.create(school=self.tenant, hour=health.hour_start(now - timedelta(days=91)))
        prune_observability()
        self.assertTrue(ErrorGroup.objects.filter(pk=kept_open.pk).exists())
        self.assertFalse(ErrorGroup.objects.filter(pk=gone.pk).exists())
        self.assertEqual(list(ErrorEvent.objects.values_list("pk", flat=True)), [fresh.pk])
        self.assertFalse(HealthBucket.objects.exists())

    def test_purging_a_school_leaves_no_observability_rows(self):
        # covers: AC-20
        group = make_group(self.tenant)
        ErrorEvent.objects.create(group=group, school=self.tenant, occurred_at=timezone.now(),
                                  message="", stack=[], context={})
        HealthBucket.objects.create(school=self.tenant, hour=health.hour_start(timezone.now()))
        SchoolHealth.objects.create(school=self.tenant, status="healthy", computed_at=timezone.now())
        purge_school(self.tenant)
        for model in (ErrorGroup, ErrorEvent, HealthBucket, SchoolHealth):
            self.assertFalse(model.objects.filter(school=self.tenant).exists(), model.__name__)
