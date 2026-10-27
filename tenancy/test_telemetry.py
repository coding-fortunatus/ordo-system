"""Metrics, logs and traces (spec 0006).

Slice 1, HTTP traces end to end: capture, the keep rule, the telemetry
Redis, the flush, partitions, the reference header and the console trace
endpoints. Slice 2, full trace coverage: SQL, Redis, outgoing calls, jobs,
WebSockets and the link from spec 0004 errors. Slice 3, logs: capture,
storage and search."""
import asyncio
import json
import logging
import time
import uuid
from datetime import timedelta
from unittest import mock

import fakeredis
import httpcore
import httpx
import redis as redis_lib
from asgiref.sync import async_to_sync
from celery import current_app, shared_task
from celery.app.trace import build_tracer
from celery.exceptions import Retry
from celery.signals import after_task_publish, before_task_publish
from channels.testing import WebsocketCommunicator
from django.core.handlers.asgi import ASGIHandler
from django.core.signals import request_finished, request_started
from django.db import close_old_connections, connection
from django.http import JsonResponse
from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, include, path, resolve
from django.utils import timezone
from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
from opentelemetry.instrumentation.utils import suppress_instrumentation
from opentelemetry.trace import SpanKind
from rest_framework.decorators import api_view

from core.asgi import application
from ems.readiness import NotReady
from tenancy.models import ErrorEvent, ObsLog, ObsSegment, ObsSpan, PlatformAdmin
from tenancy.observability import buffer, capture, context, flush, handler, jobs, otel, partitions, sampling
from tenancy.observability.exporters import SegmentExporter, segment_items
from tenancy.test_observability import ObservabilityMixin, quiet_admin_emails
from tenancy.test_platform_auth import CONSOLE, PlatformTestCase
from tenancy.testcases import SCHOOL_TEST_HOST, SchoolClient, SchoolTestCase, SocketTestMixin


def ok(request):
    return JsonResponse({"ok": True})


def bad(request):
    return JsonResponse({"detail": "No."}, status=400)


def missing(request):
    return JsonResponse({"detail": "Not found."}, status=404)


def boom(request):
    return 1 / 0


app_log = logging.getLogger("ems.test_telemetry")


def queries(request):
    """SQL with a bound value, a Redis write and an outgoing call."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT %s", ["Ada Obi"])
    fakeredis.FakeRedis().set("student:ada", "Ada Obi")
    with mock.patch.object(httpcore.ConnectionPool, "handle_request", return_value=httpcore.Response(200)):
        httpx.get("https://portal.example/students?name=Ada+Obi")
    return JsonResponse({"ok": True})


@api_view(["GET"])
def logs(request):
    app_log.debug("Too detailed to keep")
    app_log.info(
        "Sent the timetable to ada@example.com",
        extra={"student_count": 3, "detail": {"not": "kept"}, "note": "for ada@example.com"},
    )
    logging.getLogger("ordo.observability").warning("Telemetry talking about itself")
    return JsonResponse({"ok": True})


# Headers of the tasks the ``queue`` view published, as a broker would carry them.
PUBLISHED = []


def queue(request):
    """Publish ``traced_job`` the way ``apply_async`` does, without a broker."""
    task_id = uuid.uuid4().hex
    headers = {"id": task_id, "task": traced_job.name, "_schema_name": connection.schema_name}
    body = ([f"job-{task_id[:8]}", 7], {}, {})
    options = {"sender": traced_job.name, "body": body, "exchange": "", "routing_key": "celery"}
    before_task_publish.send(headers=headers, properties={}, declare=[], retry_policy=None, **options)
    after_task_publish.send(headers=headers, **options)
    PUBLISHED.append((headers, body[0]))
    return JsonResponse({"queued": task_id})


urlpatterns = [
    path("api/ok/", ok),
    path("api/bad/", bad),
    path("api/missing/", missing),
    path("api/boom/", boom),
    path("api/queries/", queries),
    path("api/logs/", logs),
    path("api/queue/", queue),
    path("", include("core.urls")),
]

job_log = logging.getLogger("ems.test_telemetry.job")


@shared_task(bind=True, name="tenancy.test_telemetry.traced_job")
def traced_job(self, job_id, user_id, outcome="ok"):
    from ems.models import BackgroundJob

    BackgroundJob.objects.filter(job_id=job_id).exists()
    job_log.info("Ran %s", job_id)
    if outcome == "fail":
        raise ValueError("generation failed")
    if outcome == "not_ready":
        raise NotReady({"empty_classes": [], "stale": [], "stage": "allocation"})
    if outcome == "retry":
        raise Retry()
    return outcome


def run_like_worker(task_name, args, headers=None):
    """Run a task as a worker does: its message headers on the request."""
    task = current_app.tasks[task_name]
    # With no headers: as beat would queue it, in the current schema.
    headers = dict(headers or {"id": uuid.uuid4().hex, "_schema_name": connection.schema_name})
    request = {
        "task": task_name, "retries": 0, "is_eager": False, "hostname": "test-worker",
        "delivery_info": {}, "headers": None, **headers,
    }
    tracer = build_tracer(task_name, task, eager=True, propagate=False, app=current_app)
    return tracer(headers["id"], list(args), {}, request)

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT_ID = "00f067aa0ba902b7"


class TracingMixin:
    """Django instrumented for the class, with a synchronous exporter into a
    fake telemetry Redis that every test starts empty."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = otel.build_provider("web", exporter=SegmentExporter(sync=True))
        otel.install(cls.provider)

    @classmethod
    def tearDownClass(cls):
        otel.uninstall()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.telemetry = fakeredis.FakeRedis(decode_responses=True)
        buffer.set_client(self.telemetry)
        buffer._take_drops()
        # The instrumentor wraps connections made after it; the test's own
        # connection (which holds its transaction) was made before.
        connection.ensure_connection()
        Psycopg2Instrumentor.instrument_connection(connection.connection, tracer_provider=self.provider)
        self.addCleanup(Psycopg2Instrumentor.uninstrument_connection, connection.connection)
        today = timezone.now().date()
        with connection.cursor() as cursor:
            partitions.ensure(cursor, partitions.TABLES, today - timedelta(days=4), days_ahead=8)

    def tearDown(self):
        buffer.set_client(None)
        super().tearDown()

    def buffered(self, signal):
        return [json.loads(v) for v in self.telemetry.lrange(buffer.buffer_key(signal), 0, -1)]


@override_settings(ROOT_URLCONF=__name__, OBS_TRACE_SAMPLE_PERCENT=100)
class CaptureTests(TracingMixin, ObservabilityMixin, SchoolTestCase):
    def setUp(self):
        super().setUp()
        quiet_admin_emails(self)
        self.client.raise_request_exception = False

    def test_a_kept_request_reaches_postgres_with_its_school_route_and_ids(self):
        # covers: AC-1, AC-5, AC-14
        response = self.client.get("/api/ok/?student=Ada+Obi")
        trace_id = response["X-Trace-ID"]
        self.assertRegex(trace_id, r"^[0-9a-f]{32}$")
        [segment] = self.buffered("segment")
        self.assertEqual(segment["trace_id"], trace_id)

        self.assertTrue(flush.flush())
        row = ObsSegment.objects.get(trace_id=trace_id)
        self.assertEqual(
            (row.school_id, row.kind, row.route, row.method, row.status_code, row.root_name),
            (self.tenant.pk, "http", "api/ok/", "GET", 200, "GET api/ok/"),
        )
        self.assertEqual(row.request_id, response["X-Request-ID"])
        self.assertFalse(row.is_error or row.is_slow)
        span = ObsSpan.objects.get(trace_id=trace_id, kind="server")
        self.assertEqual((span.kind, span.span_id, span.school_id), ("server", row.root_span_id, self.tenant.pk))
        self.assertEqual(span.attributes["http.route"], "api/ok/")
        self.assertNotIn("http.user_agent", span.attributes)
        self.assertNotIn("http.url", span.attributes)
        self.assertEqual(self.buffered("segment") + self.buffered("span"), [])
        self.assertIsNotNone(self.telemetry.get(flush.LAST_OK_KEY))

    def test_a_signed_in_school_user_is_recorded(self):
        # covers: AC-1
        user, token = self.officer()
        self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Token {token}")
        [segment] = self.buffered("segment")
        self.assertEqual(segment["user_id"], user.pk)

    @override_settings(OBS_TRACE_SAMPLE_PERCENT=0)
    def test_only_errors_and_slow_requests_are_kept_when_nothing_is_sampled(self):
        # covers: AC-3
        self.client.get("/api/ok/")
        self.client.get("/api/bad/")
        self.client.get("/api/missing/")
        self.assertEqual(self.buffered("segment"), [])
        self.client.get("/api/boom/")
        [error] = self.buffered("segment")
        self.assertEqual((error["status_code"], error["is_error"], error["is_slow"]), (500, True, False))
        [root] = [s for s in self.buffered("span") if s["parent_span_id"] is None]
        self.assertEqual(root["status"], "error")
        self.assertEqual(root["attributes"]["exception.type"], "ZeroDivisionError")
        with override_settings(OBS_SLOW_HTTP_MS=-1):
            self.client.get("/api/ok/")
        self.assertTrue(self.buffered("segment")[-1]["is_slow"])

    @override_settings(OBS_TRACE_SAMPLE_PERCENT=0)
    def test_an_inbound_traceparent_joins_the_trace_but_cannot_choose_storage(self):
        # covers: AC-3, AC-26
        response = self.client.get("/api/ok/", HTTP_TRACEPARENT=f"00-{TRACE_ID}-{PARENT_ID}-01")
        self.assertEqual(response["X-Trace-ID"], TRACE_ID)
        self.assertEqual(self.buffered("segment"), [])
        self.client.get("/api/boom/", HTTP_TRACEPARENT=f"00-{TRACE_ID}-{PARENT_ID}-00")
        [segment] = self.buffered("segment")
        self.assertEqual(segment["trace_id"], TRACE_ID)
        [root] = [s for s in self.buffered("span") if s["kind"] == "server"]
        self.assertEqual(root["parent_span_id"], PARENT_ID)

    def test_probes_files_and_telemetry_ingest_are_not_traced(self):
        # covers: AC-1
        for url in ("/healthz", "/api/telemetry/errors/"):
            self.assertNotIn("X-Trace-ID", self.client.get(url))
        self.assertEqual(self.buffered("segment"), [])

    def test_a_dead_telemetry_redis_never_fails_a_request(self):
        # covers: AC-4
        dead = redis_lib.Redis(port=1, socket_timeout=0.2, socket_connect_timeout=0.2)
        buffer.set_client(dead)
        started = time.monotonic()
        self.assertEqual(self.client.get("/api/ok/").status_code, 200)
        self.assertLess(time.monotonic() - started, 2)
        drops = {k[1:]: n for k, n in buffer.pending_drops().items()}
        self.assertEqual(set(drops), {("segment", "redis_down"), ("span", "redis_down")})
        self.assertEqual(drops[("segment", "redis_down")], 1)
        spans = drops[("span", "redis_down")]

        buffer.set_client(self.telemetry)
        buffer.push("span", [{"x": 1}])
        [key] = self.telemetry.keys("obs:dropped:*")
        self.assertEqual(self.telemetry.hgetall(key), {"segment:redis_down": "1", "span:redis_down": str(spans)})


class SamplingTests(SimpleTestCase):
    def test_every_process_samples_a_trace_the_same_way(self):
        # covers: AC-3
        ids = [f"{i:032x}" for i in range(2000)]
        with override_settings(OBS_TRACE_SAMPLE_PERCENT=10):
            first = [sampling.sampled(t) for t in ids]
            self.assertEqual(first, [sampling.sampled(t) for t in ids])
            self.assertTrue(150 < sum(first) < 250, sum(first))
        with override_settings(OBS_TRACE_SAMPLE_PERCENT=0):
            self.assertFalse(any(sampling.sampled(t) for t in ids))
        with override_settings(OBS_TRACE_SAMPLE_PERCENT=100):
            self.assertTrue(all(sampling.sampled(t) for t in ids))


class Collect:
    """An exporter that keeps what it is given."""

    def __init__(self):
        self.segments = []

    def export(self, root, spans, kind, decision, truncated):
        self.segments.append(segment_items(root, spans, kind, decision, truncated))

    def flush(self):
        return True

    def shutdown(self):
        pass


@override_settings(OBS_TRACE_SAMPLE_PERCENT=100)
class SegmentTests(SimpleTestCase):
    def setUp(self):
        self.exporter = Collect()
        self.provider = otel.build_provider("web", exporter=self.exporter)
        self.tracer = self.provider.get_tracer("test")

    def test_a_huge_segment_keeps_its_root_and_is_marked_truncated(self):
        # covers: AC-3
        with self.tracer.start_as_current_span("root", kind=SpanKind.SERVER):
            for _ in range(sampling.MAX_SPANS + 50):
                with self.tracer.start_as_current_span("child"):
                    pass
        [(segment, spans)] = self.exporter.segments
        self.assertTrue(segment["truncated"])
        self.assertEqual(len(spans), sampling.MAX_SPANS)
        self.assertEqual(spans[-1]["span_id"], segment["root_span_id"])

    def test_an_outgoing_call_keeps_its_address_without_the_query(self):
        # covers: AC-13
        with self.tracer.start_as_current_span("root", kind=SpanKind.SERVER):
            with self.tracer.start_as_current_span(
                "GET", kind=SpanKind.CLIENT,
                attributes={"http.url": "https://portal.example/students?name=Ada&token=s3cret", "net.peer.ip": "1.2.3.4"},
            ):
                pass
        [(_, spans)] = self.exporter.segments
        call = spans[0]
        self.assertEqual((call["kind"], call["attributes"]), ("http_out", {"http.url": "https://portal.example/students"}))

    def test_spans_outside_a_request_or_job_are_not_stored(self):
        # covers: AC-1
        with self.tracer.start_as_current_span("startup query"):
            with self.tracer.start_as_current_span("child"):
                pass
        self.assertEqual(self.exporter.segments, [])

    def test_a_segment_whose_root_never_ends_is_forgotten(self):
        # covers: AC-3
        clock = [0.0]
        processor = sampling.SegmentProcessor(self.exporter, clock=lambda: clock[0])
        provider = otel.TracerProvider()
        provider.add_span_processor(processor)
        provider.get_tracer("test").start_span("killed job", kind=SpanKind.CONSUMER)
        self.assertEqual(processor.held_count(), 1)
        with self.settings(CELERY_TASK_TIME_LIMIT=60):
            clock[0] = 119
            with provider.get_tracer("test").start_as_current_span("request", kind=SpanKind.SERVER):
                pass
            self.assertEqual(processor.held_count(), 1)
            clock[0] = 200
            with provider.get_tracer("test").start_as_current_span("request", kind=SpanKind.SERVER):
                pass
        self.assertEqual(processor.held_count(), 0)


class BufferTests(SimpleTestCase):
    def setUp(self):
        self.telemetry = fakeredis.FakeRedis(decode_responses=True)
        buffer.set_client(self.telemetry)
        buffer._take_drops()

    def tearDown(self):
        buffer.set_client(None)

    @override_settings(OBS_BUFFER_MAX=2)
    def test_a_full_buffer_drops_new_items_and_counts_them(self):
        # covers: AC-4
        buffer.push("span", [{"n": 1}])
        buffer.push("span", [{"n": 2}, {"n": 3}, {"n": 4}])
        self.assertEqual(self.telemetry.llen("obs:buf:span"), 2)
        self.assertEqual(list(buffer.pending_drops().values()), [2])
        buffer.push("segment", [{"n": 5}])
        [key] = self.telemetry.keys("obs:dropped:*")
        self.assertEqual(self.telemetry.hgetall(key), {"span:buffer_full": "2"})

    def test_a_full_redis_counts_as_a_full_buffer(self):
        # covers: AC-4
        full = mock.Mock()
        full.pipeline.return_value.execute.side_effect = redis_lib.ResponseError(
            "OOM command not allowed when used memory > 'maxmemory'."
        )
        buffer.set_client(full)
        buffer.push("span", [{"n": 1}])
        self.assertEqual([k[1:] for k in buffer.pending_drops()], [("span", "buffer_full")])

    def test_take_removes_what_it_returns(self):
        buffer.push("log", [{"n": i} for i in range(5)])
        self.assertEqual(buffer.take("log", 3), [{"n": 0}, {"n": 1}, {"n": 2}])
        self.assertEqual(buffer.take("log", 3), [{"n": 3}, {"n": 4}])


def segment_row(trace_id, at, school_id=None, **fields):
    return {
        "root_span_id": fields.pop("root_span_id", trace_id[:16]),
        "trace_id": trace_id,
        "started_at": at.isoformat(),
        "school_id": school_id,
        "kind": "http",
        "root_name": "GET api/ok/",
        "route": "api/ok/",
        "method": "GET",
        "status_code": 200,
        "duration_ms": 12,
        "is_error": False,
        "is_slow": False,
        "span_count": 1,
        "truncated": False,
        "user_id": None,
        "request_id": "",
        "release": "dev",
        "process_role": "web",
        **fields,
    }


class FlushTests(TracingMixin, SchoolTestCase):
    def test_a_bad_batch_is_discarded_and_counted_and_the_next_one_lands(self):
        # covers: AC-5
        now = timezone.now()
        broken = segment_row("a" * 32, now)
        del broken["kind"]
        buffer.push("segment", [broken, segment_row("b" * 32, now)])
        with self.assertLogs("ordo.observability", "ERROR"):
            self.assertFalse(flush.flush())
        self.assertFalse(ObsSegment.objects.exists())
        [key] = self.telemetry.keys("obs:dropped:*")
        self.assertEqual(self.telemetry.hgetall(key), {"segment:flush_error": "2"})

        buffer.push("segment", [segment_row("c" * 32, now, route='with "quotes", and, commas', request_id="")])
        self.assertTrue(flush.flush())
        row = ObsSegment.objects.get()
        self.assertEqual((row.route, row.request_id, row.user_id), ('with "quotes", and, commas', "", None))

    def test_only_one_flush_runs_at_a_time(self):
        # covers: AC-5
        self.telemetry.set(flush.LOCK_KEY, "someone else")
        buffer.push("segment", [segment_row("a" * 32, timezone.now())])
        self.assertFalse(flush.flush())
        self.assertEqual(self.telemetry.llen("obs:buf:segment"), 1)


class PartitionTests(SchoolTestCase):
    def names(self, table):
        with connection.cursor() as cursor:
            return [name for name, _, _ in partitions.partitions(cursor, table)]

    def test_old_days_are_dropped_and_the_next_days_created(self):
        # covers: AC-6
        today = timezone.now().date()
        with connection.cursor() as cursor:
            partitions.ensure(cursor, partitions.TABLES, today - timedelta(days=6), days_ahead=6)
            # Left detached by a run that failed between detach and drop.
            old = partitions.partition_name("obs_span", today - timedelta(days=9))
            cursor.execute(f"CREATE TABLE public.{old} (LIKE public.obs_span)")
        with self.settings(OBS_TRACE_KEEP_DAYS=3):
            dropped = partitions.maintain(now=timezone.now())
        expected = [partitions.partition_name("obs_segment", today + timedelta(days=d)) for d in range(-3, 4)]
        self.assertEqual(self.names("obs_segment")[-7:], expected)
        for day in (4, 5, 6):
            self.assertIn(partitions.partition_name("obs_segment", today - timedelta(days=day)), dropped)
        self.assertIn(old, dropped)
        self.assertNotIn(old, self.names("obs_span"))


# Nothing sampled, so the sign in requests themselves never land in the list.
@override_settings(OBS_TRACE_SAMPLE_PERCENT=0)
class ConsoleTraceTests(TracingMixin, PlatformTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.sign_in(self.make_admin())

    def store(self, *rows, spans=()):
        buffer.push("segment", list(rows))
        buffer.push("span", list(spans))
        self.assertTrue(flush.flush())

    def test_the_console_request_is_traced_with_its_admin(self):
        # covers: AC-1
        with self.settings(OBS_TRACE_SAMPLE_PERCENT=100):
            self.owner.get("/api/platform/me/")
        segment = self.buffered("segment")[-1]
        self.assertEqual((segment["school_id"], segment["user_id"], segment["route"]), (None, None, "api/platform/me/"))
        root = [s for s in self.buffered("span") if s["span_id"] == segment["root_span_id"]][0]
        self.assertEqual(root["attributes"]["ordo.platform_admin_id"], PlatformAdmin.objects.get().pk)

    def test_the_list_filters_and_pages_newest_first(self):
        # covers: AC-22
        now = timezone.now()
        rows = [
            segment_row(f"{i:032x}", now - timedelta(minutes=i), school_id=self.tenant.pk if i % 2 else None,
                        root_span_id=f"{i:016x}", is_error=i == 3, status_code=500 if i == 3 else 200,
                        duration_ms=i * 100)
            for i in range(1, 8)
        ]
        self.store(*rows)
        url = "/api/platform/observability/traces/"
        page = self.owner.get(url, {"limit": 3}).data
        self.assertEqual([r["trace_id"][-1] for r in page["results"]], ["1", "2", "3"])
        self.assertIsNone(page["previous"])
        second = self.owner.get(url, {"limit": 3, "cursor": page["next"]}).data
        self.assertEqual([r["trace_id"][-1] for r in second["results"]], ["4", "5", "6"])
        back = self.owner.get(url, {"limit": 3, "cursor": second["previous"]}).data
        self.assertEqual([r["trace_id"][-1] for r in back["results"]], ["1", "2", "3"])

        school = self.owner.get(url, {"school": self.tenant.pk}).data["results"]
        self.assertEqual([r["school"]["name"] for r in school], ["Test School"] * 4)
        self.assertEqual(len(self.owner.get(url, {"school": "platform"}).data["results"]), 3)
        self.assertEqual([r["trace_id"][-1] for r in self.owner.get(url, {"errors": "true"}).data["results"]], ["3"])
        self.assertEqual(len(self.owner.get(url, {"status": "5xx"}).data["results"]), 1)
        self.assertEqual(len(self.owner.get(url, {"min_ms": 500}).data["results"]), 3)
        self.assertEqual(len(self.owner.get(url, {"trace": "0000000000000000000000000000000"}).data["results"]), 7)
        self.assertEqual(self.owner.get(url, {"school": 99999}).status_code, 404)
        for bad in ({"trace": "abc"}, {"status": "3xx"}, {"kind": "x"}, {"cursor": "junk"},
                    {"start": (now - timedelta(days=9)).isoformat()}):
            self.assertEqual(self.owner.get(url, bad).status_code, 400, bad)

    def test_an_old_trace_is_found_by_prefix_across_retained_days(self):
        # covers: AC-14
        self.store(segment_row("ab" * 16, timezone.now() - timedelta(days=2)))
        url = "/api/platform/observability/traces/"
        self.assertEqual(self.owner.get(url).data["results"], [])
        self.assertEqual(len(self.owner.get(url, {"trace": "abababab"}).data["results"]), 1)

    def test_a_trace_shows_every_segment_its_spans_and_missing_parents(self):
        # covers: AC-22
        now = timezone.now()
        trace_id = "cd" * 16
        root = {"span_id": "1" * 16, "trace_id": trace_id, "parent_span_id": "9" * 16, "root_span_id": "1" * 16,
                "started_at": now.isoformat(), "school_id": self.tenant.pk, "name": "GET api/ok/",
                "kind": "server", "duration_ms": 12.5, "status": "ok", "attributes": {"http.route": "api/ok/"}}
        job = {**root, "span_id": "2" * 16, "parent_span_id": "1" * 16, "root_span_id": "2" * 16,
               "started_at": (now + timedelta(milliseconds=5)).isoformat(), "school_id": None, "kind": "job"}
        self.store(
            segment_row(trace_id, now, school_id=self.tenant.pk, root_span_id="1" * 16),
            segment_row(trace_id, now + timedelta(milliseconds=5), root_span_id="2" * 16, kind="job"),
            spans=[root, job],
        )
        data = self.owner.get(f"/api/platform/observability/traces/{trace_id}/").data
        self.assertEqual([s["kind"] for s in data["segments"]], ["http", "job"])
        self.assertEqual(data["segments"][0]["school"]["id"], self.tenant.pk)
        self.assertEqual([s["span_id"] for s in data["spans"]], ["1" * 16, "2" * 16])
        self.assertEqual(data["missing_parents"], ["9" * 16])
        self.assertEqual(self.owner.get(f"/api/platform/observability/traces/{'e' * 32}/").status_code, 404)
        self.assertEqual(self.owner.get("/api/platform/observability/traces/nope/").status_code, 404)

    def test_support_can_read_and_school_users_get_nothing(self):
        # covers: AC-20
        support = self.sign_in(self.make_admin("support@ordo.app", role=PlatformAdmin.Role.SUPPORT))
        self.assertEqual(support.get("/api/platform/observability/traces/").status_code, 200)
        self.assertEqual(SchoolClient(HTTP_HOST=CONSOLE).get("/api/platform/observability/traces/").status_code, 401)
        with self.assertRaises(Resolver404):
            resolve("/api/platform/observability/traces/", urlconf="core.urls")


@override_settings(ROOT_URLCONF=__name__, OBS_TRACE_SAMPLE_PERCENT=100)
class AsgiTests(TracingMixin, ObservabilityMixin, SchoolTestCase):
    """Through the ASGI handler Daphne runs, not the test client."""

    def setUp(self):
        super().setUp()
        # As the test client does: the request signals would otherwise close
        # the connection that holds the test's transaction.
        request_started.disconnect(close_old_connections)
        request_finished.disconnect(close_old_connections)
        self.addCleanup(request_started.connect, close_old_connections)
        self.addCleanup(request_finished.connect, close_old_connections)

    def get(self, path, headers=()):
        """The response's status and headers. Driven by hand, not with
        ``HttpCommunicator``: that runs the app in a new context, which moves
        Django's database work to another thread and connection."""
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
            "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
            "root_path": "", "headers": [(b"host", b"test.localhost"), *headers],
            "client": ("127.0.0.1", 40000), "server": ("test.localhost", 80),
        }
        sent = []

        async def request():
            done = asyncio.Event()
            bodies = [{"type": "http.request", "body": b"", "more_body": False}]

            async def receive():
                if bodies:
                    return bodies.pop()
                await done.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                sent.append(message)

            await ASGIHandler()(scope, receive, send)
            done.set()

        async_to_sync(request)()
        start = next(m for m in sent if m["type"] == "http.response.start")
        return {"status": start["status"], "headers": start["headers"]}

    def test_an_asgi_request_is_one_root_span_with_nested_sql_and_no_context_warnings(self):
        # covers: AC-1, AC-7
        with self.assertNoLogs("opentelemetry", "WARNING"):
            response = self.get("/api/ok/", [(b"traceparent", f"00-{TRACE_ID}-{PARENT_ID}-01".encode())])
        self.assertEqual(response["status"], 200)
        headers = dict(response["headers"])
        self.assertEqual(headers[b"X-Trace-ID"].decode(), TRACE_ID)
        [segment] = self.buffered("segment")
        self.assertEqual((segment["trace_id"], segment["school_id"], segment["route"]), (TRACE_ID, self.tenant.pk, "api/ok/"))
        spans = self.buffered("span")
        [root] = [s for s in spans if s["kind"] == "server"]
        self.assertEqual(root["parent_span_id"], PARENT_ID)
        sql = [s for s in spans if s["kind"] == "db"]
        self.assertTrue(sql)
        self.assertTrue(all(s["parent_span_id"] == root["span_id"] and s["trace_id"] == TRACE_ID for s in sql))


class LogCaptureMixin:
    """Log capture on, drained by hand: the background thread never starts."""

    def setUp(self):
        super().setUp()
        self.enterContext(override_settings(OBSERVABILITY_LOGS_ENABLED=True))
        self.enterContext(mock.patch.object(handler, "_ensure_thread"))
        handler.drain()
        self.telemetry.delete(buffer.buffer_key("log"))

    def logged(self):
        handler.drain()
        return self.buffered("log")


@override_settings(ROOT_URLCONF=__name__, OBS_TRACE_SAMPLE_PERCENT=100)
class CoverageTests(TracingMixin, ObservabilityMixin, SchoolTestCase):
    def setUp(self):
        super().setUp()
        quiet_admin_emails(self)
        self.client.raise_request_exception = False

    def test_sql_redis_and_outgoing_calls_are_child_spans_without_values(self):
        # covers: AC-1, AC-13
        trace_id = self.client.get("/api/queries/")["X-Trace-ID"]
        spans = self.buffered("span")
        [root] = [s for s in spans if s["kind"] == "server"]
        children = {s["kind"]: s for s in spans if s["parent_span_id"] == root["span_id"]}
        self.assertTrue({"db", "redis", "http_out"} <= set(children))
        self.assertTrue(all(s["trace_id"] == trace_id and s["school_id"] == self.tenant.pk for s in spans))

        [query] = [s for s in spans if s["kind"] == "db" and s["attributes"].get("db.statement") == "SELECT %s"]
        self.assertEqual(query["attributes"]["db.operation"], "SELECT")
        self.assertEqual(children["redis"]["attributes"], {"redis.command": "SET"})
        self.assertEqual(children["http_out"]["attributes"]["http.url"], "https://portal.example/students")
        self.assertNotIn("Ada", json.dumps(spans))

    def test_an_error_event_records_its_trace(self):
        # covers: AC-15
        trace_id = self.client.get("/api/boom/")["X-Trace-ID"]
        self.assertEqual(ErrorEvent.objects.get().trace_id, trace_id)


@override_settings(ROOT_URLCONF=__name__, OBS_TRACE_SAMPLE_PERCENT=100)
class JobTraceTests(LogCaptureMixin, TracingMixin, ObservabilityMixin, SchoolTestCase):
    def setUp(self):
        super().setUp()
        context.reset_schema_cache()
        PUBLISHED.clear()

    def segments(self, kind):
        return [s for s in self.buffered("segment") if s["kind"] == kind]

    def test_a_queued_job_shares_the_request_trace_with_its_school_request_and_user(self):
        # covers: AC-1, AC-2, AC-12
        user, token = self.officer()
        response = self.client.get("/api/queue/", HTTP_AUTHORIZATION=f"Token {token}")
        [(headers, args)] = PUBLISHED
        self.assertEqual(headers[jobs.REQUEST_ID_HEADER], response["X-Request-ID"])
        self.assertIsInstance(headers[jobs.SENT_AT_HEADER], int)
        self.assertIn("traceparent", headers)

        run_like_worker(traced_job.name, args, headers)
        [request] = self.segments("http")
        [job] = self.segments("job")
        self.assertEqual(job["trace_id"], request["trace_id"])
        self.assertEqual(
            (job["school_id"], job["root_name"], job["request_id"], job["user_id"], job["route"], job["status_code"]),
            (self.tenant.pk, traced_job.name, response["X-Request-ID"], 7, "", None),
        )
        job_spans = [s for s in self.buffered("span") if s["root_span_id"] == job["root_span_id"]]
        self.assertTrue(any(s["kind"] == "db" and s["school_id"] == self.tenant.pk for s in job_spans))
        [root] = [s for s in job_spans if s["span_id"] == job["root_span_id"]]
        self.assertEqual(root["attributes"]["celery.task_name"], traced_job.name)

        [line] = [item for item in self.logged() if item["logger"] == job_log.name]
        self.assertEqual(
            (line["trace_id"], line["request_id"], line["user_id"], line["school_id"]),
            (job["trace_id"], response["X-Request-ID"], 7, self.tenant.pk),
        )
        # Back to nothing once the task is done.
        self.assertEqual((context.request_id.get(), context.user_id.get()), ("", None))

    def test_a_task_with_no_request_starts_its_own_trace(self):
        # covers: AC-2
        run_like_worker(traced_job.name, ["job-beat", None])
        [job] = self.segments("job")
        self.assertEqual((job["request_id"], job["user_id"]), ("", None))
        self.assertRegex(job["trace_id"], r"^[0-9a-f]{32}$")

    @override_settings(OBS_TRACE_SAMPLE_PERCENT=0)
    def test_only_a_final_unexpected_failure_is_an_error(self):
        # covers: AC-3, AC-15
        run_like_worker(traced_job.name, ["job-ok", 7])
        run_like_worker(traced_job.name, ["job-wait", 7, "not_ready"])
        run_like_worker(traced_job.name, ["job-again", 7, "retry"])
        self.assertEqual(self.segments("job"), [])

        run_like_worker(traced_job.name, ["job-fail", 7, "fail"])
        [failed] = self.segments("job")
        self.assertTrue(failed["is_error"])
        [root] = [s for s in self.buffered("span") if s["span_id"] == failed["root_span_id"]]
        self.assertEqual((root["status"], root["attributes"]["exception.type"]), ("error", "ValueError"))
        event = ErrorEvent.objects.get(group__exc_type="builtins.ValueError")
        self.assertEqual(event.trace_id, failed["trace_id"])

    def test_observability_tasks_are_never_traced_or_logged(self):
        # covers: AC-12, AC-3
        from tenancy.observability.tasks import flush_telemetry

        with mock.patch.object(flush, "flush", side_effect=lambda: logging.getLogger("tenancy.x").error("inside")):
            run_like_worker(flush_telemetry.name, [])
        self.assertEqual(self.buffered("segment"), [])
        self.assertEqual(self.logged(), [])


@override_settings(OBS_TRACE_SAMPLE_PERCENT=100)
class SocketTraceTests(TracingMixin, ObservabilityMixin, SocketTestMixin, SchoolTestCase):
    def connect(self, token):
        return WebsocketCommunicator(
            application,
            f"/ws/notifications/?token={token}",
            headers=[(b"host", SCHOOL_TEST_HOST.encode()), (b"origin", f"http://{SCHOOL_TEST_HOST}:3000".encode())],
        )

    def test_connect_each_message_and_disconnect_are_segments_in_the_school(self):
        # covers: AC-16, AC-1
        user, token = self.officer()

        async def scenario():
            ws = self.connect(token)
            connected, _ = await ws.connect()
            self.assertTrue(connected)
            await ws.receive_json_from()
            await ws.send_json_to({"type": "mark-read", "id": 3})
            await ws.disconnect()

        async_to_sync(scenario)()
        segments = self.buffered("segment")
        self.assertEqual(
            [s["root_name"] for s in segments],
            ["ws.connect NotificationConsumer", "ws.receive NotificationConsumer.mark-read", "ws.disconnect NotificationConsumer"],
        )
        for segment in segments:
            self.assertEqual(
                (segment["kind"], segment["route"], segment["school_id"], segment["user_id"], segment["is_error"]),
                ("ws", "NotificationConsumer", self.tenant.pk, user.pk, False),
            )
        roots = {s["span_id"]: s for s in self.buffered("span")}
        connect, receive, disconnect = (roots[s["root_span_id"]] for s in segments)
        self.assertEqual((connect["kind"], connect["attributes"]["ordo.ws_accepted"]), ("ws", True))
        self.assertEqual(receive["attributes"]["ordo.message_type"], "mark-read")
        self.assertEqual(disconnect["attributes"]["ordo.ws_close_code"], 1000)
        self.assertTrue(any(s["kind"] == "db" and s["root_span_id"] == connect["span_id"] for s in roots.values()))

    def test_an_exception_in_a_handler_is_an_error_segment_linked_to_its_error(self):
        # covers: AC-16, AC-15
        _, token = self.officer()

        async def scenario():
            ws = self.connect(token)
            await ws.connect()
            with self.assertRaises(ZeroDivisionError):
                await ws.receive_json_from()

        with mock.patch("ems.api.consumers.unread_count", side_effect=ZeroDivisionError):
            async_to_sync(scenario)()
        [segment] = self.buffered("segment")
        self.assertEqual((segment["root_name"], segment["is_error"]), ("ws.connect NotificationConsumer", True))
        self.assertEqual(ErrorEvent.objects.get().trace_id, segment["trace_id"])


@override_settings(ROOT_URLCONF=__name__, OBS_TRACE_SAMPLE_PERCENT=100)
class LogCaptureTests(LogCaptureMixin, TracingMixin, ObservabilityMixin, SchoolTestCase):
    def test_a_request_s_lines_carry_its_school_trace_request_and_user(self):
        # covers: AC-12, AC-13
        user, token = self.officer()
        response = self.client.get("/api/logs/", HTTP_AUTHORIZATION=f"Token {token}")
        [line] = self.logged()
        [segment] = self.buffered("segment")
        self.assertEqual(
            (line["school_id"], line["level"], line["logger"], line["trace_id"], line["request_id"], line["user_id"]),
            (self.tenant.pk, logging.INFO, "ems.test_telemetry", response["X-Trace-ID"], response["X-Request-ID"], user.pk),
        )
        self.assertEqual(line["span_id"], segment["root_span_id"])
        self.assertEqual(line["message"], "Sent the timetable to [email]")
        self.assertEqual(line["attributes"], {"student_count": 3, "note": "for [email]"})

        self.assertTrue(flush.flush())
        row = ObsLog.objects.get()
        self.assertEqual((row.trace_id, row.user_id, row.message), (line["trace_id"], user.pk, line["message"]))

    def test_only_errors_are_kept_from_django_and_celery_and_never_telemetry_s_own(self):
        # covers: AC-12
        logging.getLogger("django.request").warning("Not Found: /api/x/")
        trace_log = logging.getLogger("celery.app.trace")
        for name in ("tenancy.observability.tasks.flush_telemetry", "ems.tasks.generate_timetable_task"):
            trace_log.error("Task %s raised", name, extra={"data": {"name": name}})
        with suppress_instrumentation():
            logging.getLogger("tenancy.x").error("Inside an observability task")
        logging.getLogger("ordo.observability").error("Flush failed")
        logging.getLogger("django.request").error("Internal Server Error: /api/x/")
        try:
            raise ValueError("bad 'Ada' value")
        except ValueError:
            logging.getLogger("core.x").exception("Import failed")
        lines = self.logged()
        self.assertEqual(
            [(line["logger"], line["message"]) for line in lines],
            [
                ("celery.app.trace", "Task ems.tasks.generate_timetable_task raised"),
                ("django.request", "Internal Server Error: /api/x/"),
                ("core.x", "Import failed\nValueError: bad '[value]' value"),
            ],
        )
        # No request or job: no trace, and the school is the connection's.
        self.assertTrue(all(line["trace_id"] is None and line["school_id"] == self.tenant.pk for line in lines))

    @override_settings(OBSERVABILITY_LOGS_ENABLED=False)
    def test_the_switch_turns_logs_off(self):
        # covers: AC-7
        logging.getLogger("ems.x").error("Not stored")
        self.assertEqual(self.logged(), [])


def log_row(at, message="Distributing tutorials", **fields):
    return {
        "logged_at": at.isoformat(),
        "school_id": None,
        "level": logging.INFO,
        "logger": "ems.tasks",
        "message": message,
        "trace_id": None,
        "span_id": None,
        "request_id": "",
        "user_id": None,
        "attributes": {},
        **fields,
    }


@override_settings(OBS_TRACE_SAMPLE_PERCENT=0)
class ConsoleLogTests(TracingMixin, ObservabilityMixin, PlatformTestCase):
    url = "/api/platform/observability/logs/"

    def setUp(self):
        super().setUp()
        self.owner = self.sign_in(self.make_admin())

    def store(self, *rows, segments=()):
        buffer.push("log", list(rows))
        buffer.push("segment", list(segments))
        self.assertTrue(flush.flush())

    def messages(self, **params):
        response = self.owner.get(self.url, params)
        self.assertEqual(response.status_code, 200, response.data)
        return [r["message"] for r in response.data["results"]]

    def test_filters_text_search_and_paging(self):
        # covers: AC-23
        now = timezone.now()
        trace_id, request_id = "ab" * 16, "cd" * 16
        self.store(
            log_row(now - timedelta(minutes=1), "Distributing tutorials", school_id=self.tenant.pk, trace_id=trace_id),
            log_row(now - timedelta(minutes=2), "Seat plan failed", level=logging.ERROR, logger="ems.api.views",
                    request_id=request_id, user_id=12),
            log_row(now - timedelta(minutes=3), "Allocated 500 seats in 50% of halls", logger="tenancy.tasks"),
            log_row(now - timedelta(days=2), "Old line", trace_id=trace_id),
            segments=[segment_row(trace_id, now - timedelta(minutes=1), school_id=self.tenant.pk)],
        )
        self.assertEqual(self.messages(), ["Distributing tutorials", "Seat plan failed", "Allocated 500 seats in 50% of halls"])
        self.assertEqual(self.messages(school=self.tenant.pk), ["Distributing tutorials"])
        self.assertEqual(self.messages(school="platform"), ["Seat plan failed", "Allocated 500 seats in 50% of halls"])
        self.assertEqual(self.messages(level="error"), ["Seat plan failed"])
        self.assertEqual(self.messages(logger="ems."), ["Distributing tutorials", "Seat plan failed"])
        self.assertEqual(self.messages(request=request_id), ["Seat plan failed"])
        self.assertEqual(self.messages(user=12), ["Seat plan failed"])
        # A trace looks across every day kept.
        self.assertEqual(self.messages(trace=trace_id), ["Distributing tutorials", "Old line"])
        self.assertEqual(self.messages(q="TUTOR"), ["Distributing tutorials"])
        self.assertEqual(self.messages(q="50%"), ["Allocated 500 seats in 50% of halls"])
        self.assertEqual(self.messages(q="0 s"), ["Allocated 500 seats in 50% of halls"])

        rows = self.owner.get(self.url).data["results"]
        self.assertEqual([r["trace_stored"] for r in rows], [True, False, False])
        self.assertEqual(rows[0]["school"]["id"], self.tenant.pk)

        page = self.owner.get(self.url, {"limit": 2}).data
        self.assertEqual(len(page["results"]), 2)
        older = self.owner.get(self.url, {"limit": 2, "cursor": page["next"]}).data
        self.assertEqual([r["message"] for r in older["results"]], ["Allocated 500 seats in 50% of halls"])

        for bad in ({"q": "ab"}, {"level": "loud"}, {"trace": "abc"}, {"request": "x" * 32}, {"after": "junk"},
                    {"user": "me"}, {"start": (now - timedelta(days=12)).isoformat()}):
            self.assertEqual(self.owner.get(self.url, bad).status_code, 400, bad)

    def test_live_polling_picks_up_lines_flushed_late(self):
        # covers: AC-23
        now = timezone.now()
        self.store(log_row(now - timedelta(seconds=5), "First"))
        latest = self.owner.get(self.url).data["latest"]
        self.assertEqual(self.owner.get(self.url, {"after": latest}).data["results"], [])

        # Logged before "First", but flushed after it (another process).
        self.store(log_row(now - timedelta(seconds=30), "Late"), log_row(now, "Newest"))
        polled = self.owner.get(self.url, {"after": latest}).data
        self.assertEqual([r["message"] for r in polled["results"]], ["Newest", "Late"])
        self.assertEqual(self.owner.get(self.url, {"after": polled["latest"]}).data["results"], [])

    def test_a_trace_lists_its_logs(self):
        # covers: AC-22
        now = timezone.now()
        trace_id = "ef" * 16
        self.store(
            log_row(now + timedelta(milliseconds=2), "Second", trace_id=trace_id),
            log_row(now + timedelta(milliseconds=1), "First", trace_id=trace_id),
            log_row(now, "Elsewhere"),
            segments=[segment_row(trace_id, now)],
        )
        data = self.owner.get(f"/api/platform/observability/traces/{trace_id}/").data
        self.assertEqual([log["message"] for log in data["logs"]], ["First", "Second"])

    def test_an_error_event_says_whether_its_trace_and_logs_are_still_kept(self):
        # covers: AC-15
        tracer = self.provider.get_tracer("test")
        with tracer.start_as_current_span("job", kind=SpanKind.CONSUMER) as span:
            try:
                1 / 0
            except ZeroDivisionError as exc:
                capture.capture_exception(exc, "task")
        event = ErrorEvent.objects.get()
        self.assertEqual(event.trace_id, f"{span.get_span_context().trace_id:032x}")
        url = f"/api/platform/errors/{event.group_id}/events/"
        [row] = self.owner.get(url).data["results"]
        self.assertEqual((row["trace_id"], row["trace_kept"], row["logs_kept"]), (event.trace_id, True, True))
        ErrorEvent.objects.update(occurred_at=timezone.now() - timedelta(days=5))
        [row] = self.owner.get(url).data["results"]
        self.assertEqual((row["trace_kept"], row["logs_kept"]), (False, True))

    def test_support_can_search_and_school_users_get_nothing(self):
        # covers: AC-20
        support = self.sign_in(self.make_admin("support@ordo.app", role=PlatformAdmin.Role.SUPPORT))
        self.assertEqual(support.get(self.url).status_code, 200)
        self.assertEqual(SchoolClient(HTTP_HOST=CONSOLE).get(self.url).status_code, 401)
        with self.assertRaises(Resolver404):
            resolve(self.url, urlconf="core.urls")


class LogPartitionTests(SchoolTestCase):
    def test_log_days_are_kept_longer_than_trace_days(self):
        # covers: AC-6
        today = timezone.now().date()
        with connection.cursor() as cursor:
            partitions.ensure(cursor, partitions.TABLES, today - timedelta(days=6), days_ahead=6)
        with self.settings(OBS_TRACE_KEEP_DAYS=3, OBS_LOG_KEEP_DAYS=5):
            dropped = partitions.maintain(now=timezone.now())
        self.assertIn(partitions.partition_name("obs_span", today - timedelta(days=4)), dropped)
        self.assertNotIn(partitions.partition_name("obs_log", today - timedelta(days=4)), dropped)
        self.assertIn(partitions.partition_name("obs_log", today - timedelta(days=6)), dropped)
