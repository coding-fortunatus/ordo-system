"""Metrics (spec 0006, slice 4): the registry, the minute flush, the
watermark, rollups and the query layer."""
import random
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

import fakeredis
from celery import shared_task
from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from tenancy.models import MetricRollup, MetricSeries, ObsMetricPoint, PlatformAdmin
from tenancy.observability import (
    buffer,
    context,
    counters,
    metric_store,
    metric_views,
    metrics,
    partitions,
    query,
    samplers,
)
from tenancy.test_observability import ObservabilityMixin
from tenancy.test_platform_auth import PlatformTestCase
from tenancy.test_telemetry import TracingMixin
from tenancy.testcases import SchoolTestCase

# A fixed, minute aligned "now" for the store and the queries.
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=dt_timezone.utc)


def at(minutes_ago, second=0):
    return (NOW - timedelta(minutes=minutes_ago)).timestamp() + second


def recorded_at(epoch):
    """Observations made inside the block are stamped ``epoch``."""
    return mock.patch("tenancy.observability.metrics.time.time", return_value=epoch)


class MetricsMixin:
    def setUp(self):
        super().setUp()
        self.telemetry = fakeredis.FakeRedis(decode_responses=True)
        buffer.set_client(self.telemetry)
        buffer._take_drops()
        metrics.reset()
        context.reset_schema_cache()
        with connection.cursor() as cursor:
            partitions.ensure(cursor, ["obs_metric_point"], NOW.date() - timedelta(days=4), days_ahead=8)

    def tearDown(self):
        metrics.reset()
        buffer.set_client(None)
        super().tearDown()

    def flush(self, now=NOW):
        metrics.push()
        return metric_store.flush_metrics("default", now=now)

    def series(self, metric, aggregation, minutes=10, **options):
        options.setdefault("scope", "platform")
        return query.metric_series(
            metric, aggregation, start=NOW - timedelta(minutes=minutes), end=NOW, **options
        )


class RegistryTests(SimpleTestCase):
    def test_undeclared_metric_label_or_value_is_refused(self):
        with self.assertRaises(metrics.UndeclaredMetric):
            metrics.LOGINS.add(1, outcome="succeeded")
        with self.assertRaises(metrics.UndeclaredMetric):
            metrics.LOGINS.add(1, outcome="succeeded", via="api", school="x")
        with self.assertRaises(metrics.UndeclaredMetric):
            metrics.LOGINS.add(1, outcome="maybe", via="api")
        with self.assertRaises(metrics.UndeclaredMetric):
            metrics.LOGINS.record(1, outcome="succeeded", via="api")

    @override_settings(TESTING=False, DEBUG=False)
    def test_in_production_it_is_dropped_and_counted(self):
        buffer._take_drops()
        metrics.LOGINS.add(1, outcome="maybe", via="api")
        drops = buffer.pending_drops()
        self.assertEqual(sum(n for (_, s, r), n in drops.items() if (s, r) == ("metric", "undeclared")), 1)
        buffer._take_drops()

    def test_free_labels_are_cut(self):
        self.assertEqual(
            metrics.REDIS_DURATION.label_values({"command": "x" * 100}), {"command": "x" * metrics.LABEL_MAX}
        )

    def test_buckets(self):
        self.assertEqual(metrics.bucket_index(5), 0)
        self.assertEqual(metrics.bucket_index(5.1), 1)
        self.assertEqual(metrics.bucket_index(10**7), len(metrics.BOUNDS))

    def test_percentile_is_within_one_bucket(self):
        rng = random.Random(4)
        values = [rng.lognormvariate(5, 1) for _ in range(5000)]
        buckets = [0] * metrics.BUCKETS
        for v in values:
            buckets[metrics.bucket_index(v)] += 1
        values.sort()
        for q in (0.5, 0.95, 0.99):
            true = values[int(q * len(values)) - 1]
            got = query.percentile(buckets, len(values), values[0], values[-1], q)
            i = metrics.bucket_index(true)
            low = metrics.BOUNDS[i - 1] if i else values[0]
            high = metrics.BOUNDS[i] if i < len(metrics.BOUNDS) else values[-1]
            self.assertLessEqual(abs(got - true), high - low)


class StoreTests(MetricsMixin, SchoolTestCase):
    def test_minutes_are_merged_and_flushed(self):
        with recorded_at(at(5, 10)):
            metrics.LOGINS.add(1, outcome="succeeded", via="api")
            metrics.LOGINS.add(2, outcome="succeeded", via="api")
        metrics.push()
        # A second process merges into the same minute.
        with recorded_at(at(5, 40)):
            metrics.LOGINS.add(4, outcome="succeeded", via="api")
        self.assertTrue(self.flush())

        series = MetricSeries.objects.get(metric_name="ordo.auth.logins")
        self.assertEqual(series.school_id, self.tenant.pk)
        self.assertEqual(series.labels, {"outcome": "succeeded", "via": "api"})
        point = ObsMetricPoint.objects.get(series_id=series.pk)
        self.assertEqual((point.count, point.sum), (3, 7.0))
        self.assertEqual(point.minute, NOW - timedelta(minutes=5))
        self.assertEqual(self.telemetry.smembers(metrics.PENDING_KEY), set())

    def test_a_minute_too_recent_waits(self):
        with recorded_at(at(1)):
            metrics.LOGINS.add(1, outcome="succeeded", via="api")
        self.flush()
        self.assertFalse(ObsMetricPoint.objects.exists())
        self.assertEqual(len(self.telemetry.smembers(metrics.PENDING_KEY)), 1)

    def test_a_late_merge_adds_to_the_stored_minute(self):
        with recorded_at(at(5)):
            metrics.HTTP_DURATION.record(30, route="r", method="GET", status_class="2xx")
        self.flush()
        with recorded_at(at(5)):
            metrics.HTTP_DURATION.record(3000, route="r", method="GET", status_class="2xx")
        self.flush()
        point = ObsMetricPoint.objects.get()
        self.assertEqual((point.count, point.min, point.max), (2, 30.0, 3000.0))
        self.assertEqual(sum(point.buckets), 2)

    def test_zero_and_missing(self):
        with recorded_at(at(6)):
            metrics.LOGINS.add(3, outcome="succeeded", via="api")
        self.flush()
        result = self.series("ordo.auth.logins", "sum")
        values = [v for _, v in result["series"][0]["points"]]
        # A minute settles once it is 2 minutes old: only the last is missing.
        self.assertEqual(values, [0, 0, 0, 0, 3, 0, 0, 0, 0, None])

    def test_minutes_with_drops_are_missing(self):
        self.flush()
        self.telemetry.hincrby(buffer.dropped_key(buffer.minute_stamp(at(4))), "metric:redis_down", 7)
        self.flush()
        values = [v for _, v in self.series("ordo.auth.logins", "sum")["series"][0]["points"]]
        self.assertEqual(values[6], None)
        self.assertEqual(values[5], 0)
        dropped = self.series("obs.dropped", "sum", filters={"signal": "metric"})
        self.assertEqual(dropped["series"][0]["points"][6][1], 7)

    def test_before_the_first_flush_everything_is_missing(self):
        values = [v for _, v in self.series("ordo.auth.logins", "sum")["series"][0]["points"]]
        self.assertEqual(set(values), {None})

    @override_settings(OBSERVABILITY_METRICS_ENABLED=False)
    def test_switched_off_records_nothing(self):
        metrics.LOGINS.add(1, outcome="succeeded", via="api")
        self.assertEqual(metrics.pending(), {})

    def test_gauges_carry_forward_within_their_interval(self):
        with recorded_at(at(9)):
            metrics.HOST_CPU.set(40)
        with recorded_at(at(8)):
            metrics.HOST_CPU.set(60)
        self.flush()
        values = [v for _, v in self.series("host.cpu.percent", "last")["series"][0]["points"]]
        # Sampled every minute: carried one minute, then missing.
        self.assertEqual(values, [None, 40, 60, None, None, None, None, None, None, None])

    def test_platform_scope_sums_schools_and_school_keeps_one(self):
        with recorded_at(at(5)):
            metrics.LOGINS.add(2, outcome="succeeded", via="api")
            with mock.patch.object(context, "school_id", return_value=None):
                metrics.LOGINS.add(5, outcome="succeeded", via="api")
        self.flush()
        self.assertEqual(self.series("ordo.auth.logins", "sum")["series"][0]["points"][5][1], 7)
        mine = self.series("ordo.auth.logins", "sum", school_id=self.tenant.pk)
        self.assertEqual(mine["series"][0]["points"][5][1], 2)
        by_school = self.series("ordo.auth.logins", "sum", group_by="school")
        self.assertEqual(
            {s["labels"]["school"]: s["points"][5][1] for s in by_school["series"]},
            {self.tenant.pk: 2, None: 5},
        )

    def test_ratio_and_totals(self):
        with recorded_at(at(5)):
            for status, n in (("2xx", 3), ("5xx", 1)):
                for _ in range(n):
                    metrics.HTTP_REQUESTS.add(1, route="a", method="GET", status_class=status)
            metrics.HTTP_DURATION.record(900, route="slow", method="GET", status_class="2xx")
            metrics.HTTP_DURATION.record(20, route="fast", method="GET", status_class="2xx")
        self.flush()
        ratio = self.series(
            "http.server.requests", "sum", mode="ratio",
            filters={"status_class": "5xx"}, denominator_filters={},
        )
        points = ratio["series"][0]["points"]
        self.assertEqual(points[5][1], 0.25)
        # No requests at all: no ratio.
        self.assertIsNone(points[4][1])
        totals = self.series("http.server.duration", "p95", mode="totals", group_by="route")
        self.assertEqual([r["labels"]["route"] for r in totals], ["slow", "fast"])

    def test_more_than_twenty_groups_fold_into_other(self):
        with recorded_at(at(5)):
            for i in range(25):
                metrics.HTTP_REQUESTS.add(i + 1, route=f"r{i}", method="GET", status_class="2xx")
        self.flush()
        result = self.series("http.server.requests", "sum", group_by="route")
        self.assertEqual(len(result["series"]), query.MAX_SERIES)
        other = next(s for s in result["series"] if s["labels"]["route"] == query.OTHER)
        self.assertEqual(other["points"][5][1], sum(range(1, 7)))

    def test_hourly_rollups(self):
        hour = NOW - timedelta(hours=2)
        for minute, value in ((0, 10), (30, 1000)):
            with recorded_at((hour + timedelta(minutes=minute)).timestamp()):
                metrics.HTTP_DURATION.record(value, route="r", method="GET", status_class="2xx")
                metrics.DB_DATABASE_BYTES.set(value)
        self.flush()
        self.assertEqual(
            metric_store.rollup_due("default", now=NOW + timedelta(minutes=5)),
            [NOW - timedelta(hours=1)],
        )
        # The first run only rolls the previous hour; roll the older one too.
        metric_store.rollup(hour)
        rollups = {r.series.metric_name: r for r in MetricRollup.objects.filter(hour=hour)}
        duration = rollups["http.server.duration"]
        self.assertEqual((duration.count, duration.min, duration.max, sum(duration.buckets)), (2, 10, 1000, 2))
        self.assertEqual(rollups["db.database.bytes"].last, 1000)
        # Longer than 48 hours reads rollups, hour by hour.
        result = query.metric_series(
            "http.server.duration", "max", scope="platform", start=NOW - timedelta(days=3), end=NOW
        )
        self.assertEqual(result["step"], 3600)
        values = dict(result["series"][0]["points"])
        self.assertEqual(values[int(hour.timestamp())], 1000)

    def test_unknown_metric_or_purged_school_is_skipped(self):
        key = metrics.series_key("gone.metric", None, {})
        self.telemetry.hset(metrics.mx_key(buffer.minute_stamp(at(5))), key, "1,1,1,1,,")
        self.telemetry.sadd(metrics.PENDING_KEY, buffer.minute_stamp(at(5)))
        with recorded_at(at(5)):
            with mock.patch.object(context, "school_id", return_value=999999):
                metrics.LOGINS.add(1, outcome="succeeded", via="api")
        self.assertTrue(self.flush())
        self.assertFalse(MetricSeries.objects.exists())

    def test_query_refuses_what_the_registry_does_not_declare(self):
        for args, options in (
            (("nope", "sum"), {}),
            (("ordo.auth.logins", "p95"), {}),
            (("ordo.auth.logins", "sum"), {"group_by": "colour"}),
            (("ordo.auth.logins", "sum"), {"filters": {"outcome": "maybe"}}),
        ):
            with self.assertRaises(query.QueryError):
                query.metric_series(*args, start=NOW - timedelta(hours=1), end=NOW, **options)


def recorded(name):
    """``{(school_id, labels): [count, sum]}`` aggregated for ``name`` and not yet pushed."""
    out = {}
    for (_, key), entry in metrics.pending().items():
        metric, school_id, labels = metrics.parse_series_key(key)
        if metric == name:
            totals = out.setdefault((school_id, tuple(sorted(labels.items()))), [0, 0.0])
            totals[0] += entry[0]
            totals[1] += entry[1]
    return out


@shared_task(name="tenancy.test_metrics.works")
def works():
    return 1


@shared_task(name="tenancy.test_metrics.breaks")
def breaks():
    raise ValueError("No.")


class CaptureTests(MetricsMixin, ObservabilityMixin, SchoolTestCase):
    def test_requests_are_counted_timed_and_their_users_are_active(self):
        # covers: AC-8, AC-9
        user, token = self.officer()
        self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Token {token}")
        labels = (("method", "GET"), ("route", "api/auth/me/"), ("status_class", "2xx"))
        self.assertEqual(recorded("http.server.requests"), {(self.tenant.pk, labels): [1, 1.0]})
        self.assertEqual(recorded("http.server.duration")[(self.tenant.pk, labels)][0], 1)
        metrics.push()
        day = timezone.now().astimezone(dt_timezone.utc).date()
        self.assertEqual(self.telemetry.pfcount(metrics.dau_key(self.tenant.pk, day)), 1)
        # The same user again is not sent again.
        self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Token {token}")
        self.assertEqual(metrics._dau, set())

    def test_probes_are_not_measured(self):
        self.client.get("/healthz")
        self.assertEqual(recorded("http.server.requests"), {})

    def test_sign_ins_by_outcome(self):
        # covers: AC-9
        self.officer(email="ada@example.com")
        self.client.post("/api/auth/login/", {"email": "ada@example.com", "password": "wrong"}, format="json")
        self.client.post("/api/auth/login/", {"email": "ada@example.com", "password": "x"}, format="json")
        self.assertEqual(
            {labels: v[1] for (_, labels), v in recorded("ordo.auth.logins").items()},
            {(("outcome", "failed"), ("via", "api")): 1.0, (("outcome", "succeeded"), ("via", "api")): 1.0},
        )

    def test_generation_runs_count_once(self):
        # covers: AC-9
        from types import SimpleNamespace

        from ems.models import BackgroundJob
        from ems.tasks import _counted_run

        user, _ = self.officer()
        BackgroundJob.objects.create(job_id="j1", job_type="timetable", created_by=user, status="success")

        @_counted_run("timetable")
        def run(self, job_id):
            return "done"

        run(SimpleNamespace(request=SimpleNamespace(is_eager=False)), "j1")
        # Run inline by an outer task: part of that run.
        run(SimpleNamespace(request=SimpleNamespace(is_eager=True)), "j1")
        runs = {dict(labels)["outcome"]: v[1] for (_, labels), v in recorded("ordo.timetable.runs").items()}
        self.assertEqual(runs, {"started": 1.0, "succeeded": 1.0})
        self.assertEqual(len(recorded("ordo.timetable.duration")), 1)

    def test_portal_sync_runs_and_records(self):
        # covers: AC-9
        from ems.tasks import _count_sync

        counts = {"students": {"created": 2, "updated": 1, "type_status": "ok"}, "classes": {"created": 1}}
        _count_sync({"status": "partial", "is_preview": False, "counts": counts})
        _count_sync({"status": "awaiting_approval", "is_preview": True, "counts": counts})
        records = {dict(labels)["outcome"]: v[1] for (_, labels), v in recorded("ordo.portal_sync.records").items()}
        self.assertEqual(records, {"created": 3.0, "updated": 1.0})
        runs = {labels: v[1] for (_, labels), v in recorded("ordo.portal_sync.runs").items()}
        self.assertEqual(
            runs,
            {(("outcome", "succeeded"), ("preview", "no")): 1.0, (("outcome", "succeeded"), ("preview", "yes")): 1.0},
        )

    def test_exports_count_their_bytes(self):
        # covers: AC-9
        from django.http import HttpResponse

        from ems.export_files import counted_export

        counted_export("users", HttpResponse(b"a,b\n1,2\n"))
        counted_export("users", HttpResponse(b"nope", status=400))
        self.assertEqual(list(recorded("ordo.exports.bytes").values()), [[1, 8.0]])

    def test_jobs_are_timed_by_outcome(self):
        # covers: AC-8
        works.apply()
        breaks.apply()
        outcomes = {
            dict(labels)["task"]: dict(labels)["outcome"] for (_, labels) in recorded("celery.task.duration")
        }
        self.assertEqual(
            outcomes, {"tenancy.test_metrics.works": "succeeded", "tenancy.test_metrics.breaks": "failed"}
        )
        [(school_id, _)] = recorded("celery.task.failures")
        self.assertEqual(school_id, self.tenant.pk)


class DatabaseMetricTests(TracingMixin, MetricsMixin, ObservabilityMixin, SchoolTestCase):
    @override_settings(ROOT_URLCONF="core.urls")
    def test_queries_are_timed_and_counted_per_request(self):
        # covers: AC-8
        user, token = self.officer()
        self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Token {token}")
        operations = {dict(labels)["operation"] for (_, labels) in recorded("db.query.duration")}
        self.assertIn("select", operations)
        [(school_id, labels)] = recorded("db.queries")
        self.assertEqual((school_id, labels), (self.tenant.pk, (("route", "api/auth/me/"),)))
        self.assertGreaterEqual(recorded("db.queries")[(school_id, labels)][1], 1)


class SamplerTests(MetricsMixin, SchoolTestCase):
    def test_database_and_schema_sizes(self):
        # covers: AC-8
        samplers.observe_db_size()
        [(school_id, _)] = recorded("db.database.bytes")
        self.assertIsNone(school_id)
        [(school_id, _)] = recorded("db.schema.bytes")
        self.assertEqual(school_id, self.tenant.pk)
        self.assertGreater(list(recorded("db.schema.bytes").values())[0][1], 0)

    def test_host(self):
        # covers: AC-8
        with mock.patch("psutil.cpu_percent", return_value=12.5), self.settings(OBS_DISK_PATHS=["/", "/nope"]):
            samplers.observe_host()
        self.assertEqual(list(recorded("host.cpu.percent").values()), [[1, 12.5]])
        self.assertEqual([dict(labels)["path"] for (_, labels) in recorded("host.disk.free_bytes")], ["/"])

    def test_media_per_school(self):
        # covers: AC-8
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as root:
            folder = Path(root, self.tenant.schema_name, "uploads")
            folder.mkdir(parents=True)
            (folder / "a.csv").write_bytes(b"x" * 300)
            with self.settings(MEDIA_ROOT=root):
                samplers.observe_media()
        self.assertEqual(recorded("media.bytes"), {(self.tenant.pk, ()): [1, 300.0]})

    def test_daily_actives_are_written_for_the_day_before(self):
        # covers: AC-9
        today = NOW.date()
        self.telemetry.pfadd(metrics.dau_key(self.tenant.pk, today - timedelta(days=1)), 1, 2, 3)
        samplers.record_daily_active(now=NOW)
        [((minute, _), entry)] = [
            (k, v) for k, v in metrics.pending().items() if k[1].startswith("ordo.users.daily_active|")
        ]
        self.assertEqual(entry[4], 3)
        self.assertEqual(minute, int(datetime(2026, 10, 20, 23, 59, tzinfo=dt_timezone.utc).timestamp()))


class PanelTests(MetricsMixin, PlatformTestCase):
    def setUp(self):
        super().setUp()
        self.redis = counters.MemoryRedis()
        counters.set_client(self.redis)
        self.owner = self.sign_in(self.make_admin())
        now = timezone.now()
        with recorded_at((now - timedelta(minutes=5)).timestamp()):
            with context.for_school(self.tenant.pk):
                for status in ("2xx", "2xx", "5xx"):
                    metrics.HTTP_REQUESTS.add(1, route="api/x/", method="GET", status_class=status)
                    metrics.HTTP_DURATION.record(40, route="api/x/", method="GET", status_class=status)
            metrics.HOST_CPU.set(30)
        self.flush(now=now)

    def tearDown(self):
        counters.set_client(None)
        super().tearDown()

    def test_the_overview(self):
        # covers: AC-21
        response = self.owner.get("/api/platform/observability/overview/", {"start": (timezone.now() - timedelta(hours=1)).isoformat()})
        self.assertEqual(response.status_code, 200)
        panels = response.json()["panels"]
        self.assertEqual(set(panels), set(metric_views.OVERVIEW_PANELS))
        error_rate = [v for _, v in panels["error_rate"]["series"][0]["points"] if v]
        self.assertAlmostEqual(error_rate[0], 1 / 3)
        self.assertEqual(panels["latency_p95"]["unit"], "ms")
        # The picked school keeps its own numbers; the host stays the platform's.
        mine = self.owner.get("/api/platform/observability/overview/", {"school": self.tenant.pk}).json()["panels"]
        self.assertTrue(any(v for _, v in mine["requests"]["series"][0]["points"]))
        self.assertTrue(any(v for _, v in mine["cpu"]["series"][0]["points"]))

    def test_the_school_tab_routes_and_top_schools(self):
        # covers: AC-21
        school = self.owner.get(f"/api/platform/observability/schools/{self.tenant.pk}/overview/")
        self.assertEqual(set(school.json()["panels"]), set(metric_views.SCHOOL_PANELS))
        self.assertEqual(self.owner.get("/api/platform/observability/schools/99999/overview/").status_code, 404)
        routes = self.owner.get("/api/platform/observability/routes/").json()["results"]
        self.assertEqual([(r["route"], r["requests"]) for r in routes], [("api/x/", 3)])
        top = self.owner.get("/api/platform/observability/top-schools/").json()["results"]
        self.assertEqual([(t["school"]["id"], t["requests"]) for t in top], [(self.tenant.pk, 3)])

    def test_answers_are_cached_for_30_seconds(self):
        url = "/api/platform/observability/top-schools/"
        first = self.owner.get(url).json()
        with mock.patch.object(metric_views, "top_schools", side_effect=AssertionError("not cached")):
            self.assertEqual(self.owner.get(url).json(), first)

    def test_who_can_read(self):
        # covers: AC-20
        support = self.sign_in(self.make_admin("support@ordo.app", role=PlatformAdmin.Role.SUPPORT))
        self.assertEqual(support.get("/api/platform/observability/overview/").status_code, 200)
        self.assertIn(self.console.get("/api/platform/observability/overview/").status_code, (401, 403))

    def test_bad_ranges(self):
        url = "/api/platform/observability/overview/"
        self.assertEqual(self.owner.get(url, {"start": "yesterday"}).status_code, 400)
        self.assertEqual(self.owner.get(url, {"start": (timezone.now() - timedelta(days=91)).isoformat()}).status_code, 400)
        self.assertEqual(self.owner.get(url, {"school": "x"}).status_code, 400)


class SwitchTests(SimpleTestCase):
    def test_tracing_off_keeps_db_and_redis_metrics(self):
        # covers: AC-7
        from opentelemetry.sdk.trace import SynchronousMultiSpanProcessor

        from tenancy.observability import builtin, otel, sampling

        def kinds(provider):
            processor = provider._active_span_processor
            assert isinstance(processor, SynchronousMultiSpanProcessor)
            return {type(p) for p in processor._span_processors}

        with self.settings(OBSERVABILITY_TRACING_ENABLED=False):
            self.assertEqual(kinds(otel.build_provider("web")), {builtin.MetricsSpanProcessor})
        with self.settings(OBSERVABILITY_METRICS_ENABLED=False):
            self.assertEqual(kinds(otel.build_provider("web")), {sampling.SegmentProcessor})
