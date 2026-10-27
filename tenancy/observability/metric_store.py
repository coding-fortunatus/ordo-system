"""Metric minutes into Postgres, and hourly rollups (spec 0006, child 0002).

Part of the flush: every pending minute at least ``SETTLE`` old is read and
deleted from the telemetry Redis in one step, its series are found or
created, and its points are upserted (counts, sums and buckets add up, so a
minute merged again later adds to what's stored). Drop counts become
``obs.dropped`` points of the minute they happened in. The ``metrics``
watermark then moves to the last minute that can no longer change, the
line between 0 and missing (AC-11).

``rollup`` recomputes whole hours from points into ``MetricRollup``.
"""
import hashlib
import logging
from datetime import datetime, timedelta, timezone as dt_timezone

from django.db import connections, transaction
from django.utils import timezone
from psycopg2.extras import execute_values

from tenancy.models import MetricRollup, MetricSeries, MetricWatermark, School
from tenancy.observability import buffer, metrics

logger = logging.getLogger("ordo.observability")

# Late merges (every 15 seconds per process) have landed by then.
SETTLE = timedelta(minutes=2)
METRICS = "metrics"
ROLLUPS = "rollups"
LAST_SEEN_EVERY = timedelta(hours=1)
# Hours a late rollup run catches up on.
ROLLUP_CATCH_UP = 24

UPSERT = """
INSERT INTO public.obs_metric_point AS p
    (series_id, minute, count, sum, min, max, last, buckets)
VALUES %s
ON CONFLICT (series_id, minute) DO UPDATE SET
    count = p.count + EXCLUDED.count,
    sum = p.sum + EXCLUDED.sum,
    min = LEAST(p.min, EXCLUDED.min),
    max = GREATEST(p.max, EXCLUDED.max),
    last = COALESCE(EXCLUDED.last, p.last),
    buckets = CASE
        WHEN p.buckets IS NULL THEN EXCLUDED.buckets
        WHEN EXCLUDED.buckets IS NULL THEN p.buckets
        ELSE ARRAY(
            SELECT COALESCE(a, 0) + COALESCE(b, 0)
            FROM unnest(p.buckets, EXCLUDED.buckets) WITH ORDINALITY AS t(a, b, i)
            ORDER BY i
        )
    END
"""
UPSERT_ROW = "(%s, %s, %s, %s, %s, %s, %s, %s::integer[])"

ROLLUP = """
WITH bucket_sums AS (
    SELECT series_id, array_agg(total ORDER BY i) AS buckets
    FROM (
        SELECT p.series_id, u.i, SUM(u.n)::integer AS total
        FROM public.obs_metric_point p, unnest(p.buckets) WITH ORDINALITY AS u(n, i)
        WHERE p.minute >= %(start)s AND p.minute < %(end)s
        GROUP BY p.series_id, u.i
    ) per_bucket
    GROUP BY series_id
)
INSERT INTO tenancy_metricrollup AS r (series_id, hour, count, sum, min, max, last, buckets)
SELECT p.series_id, %(start)s, SUM(p.count), SUM(p.sum), MIN(p.min), MAX(p.max),
       (array_agg(p.last ORDER BY p.minute DESC) FILTER (WHERE p.last IS NOT NULL))[1],
       b.buckets
FROM public.obs_metric_point p
JOIN tenancy_metricseries s ON s.id = p.series_id
LEFT JOIN bucket_sums b ON b.series_id = p.series_id
WHERE p.minute >= %(start)s AND p.minute < %(end)s
GROUP BY p.series_id, b.buckets
ON CONFLICT (series_id, hour) DO UPDATE SET
    count = EXCLUDED.count,
    sum = EXCLUDED.sum,
    min = EXCLUDED.min,
    max = EXCLUDED.max,
    last = EXCLUDED.last,
    buckets = EXCLUDED.buckets
"""


def labels_hash(labels):
    return hashlib.sha1(metrics.canonical(labels).encode()).hexdigest()


def stamp_time(stamp):
    return datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=dt_timezone.utc)


def floor_minute(at):
    return at.replace(second=0, microsecond=0)


def watermark(name=METRICS):
    """The stored watermark, or None before the first flush."""
    return MetricWatermark.objects.filter(name=name).values_list("minute", flat=True).first()


def _advance(alias, name, minute):
    """Move a watermark forward (never back)."""
    with connections[alias].cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO tenancy_metricwatermark (name, minute) VALUES (%s, %s)
            ON CONFLICT (name) DO UPDATE SET minute = GREATEST(tenancy_metricwatermark.minute, EXCLUDED.minute)
            """,
            [name, minute],
        )


def _take_hash(key, pending_member=None):
    """A hash's fields, read and deleted in one step; None if Redis is down."""

    def take(redis):
        pipe = redis.pipeline(transaction=True)
        pipe.hgetall(key)
        pipe.delete(key)
        if pending_member is not None:
            pipe.srem(metrics.PENDING_KEY, pending_member)
        return pipe.execute()[0]

    return buffer.run(take)


def _drop_entries(fields):
    """``obs.dropped`` values from an ``obs:dropped:<minute>`` hash."""
    entries = {}
    for field, n in fields.items():
        signal, _, reason = field.partition(":")
        try:
            labels = metrics.DROPPED.label_values({"signal": signal, "reason": reason})
        except metrics.UndeclaredMetric:
            continue
        n = int(n)
        key = metrics.series_key(metrics.DROPPED.name, None, labels)
        entries[key] = {"count": n, "sum": float(n), "min": None, "max": None, "last": None, "buckets": None}
    return entries


class _Series:
    """Series ids for this flush run, found or created on first use."""

    def __init__(self, alias, now):
        self.alias = alias
        self.now = now
        self.ids = {}
        self.schools = None

    def resolve(self, keys):
        missing = [k for k in keys if k not in self.ids]
        if missing:
            self._load(missing)
        return {k: self.ids[k] for k in keys if k in self.ids}

    def _live_schools(self, ids):
        if self.schools is None:
            self.schools = set()
        unknown = ids - self.schools
        if unknown:
            self.schools |= set(
                School.objects.using(self.alias).filter(pk__in=unknown).values_list("pk", flat=True)
            )
        return self.schools

    def _load(self, keys):
        parsed = {}
        for key in keys:
            name, school_id, labels = metrics.parse_series_key(key)
            if metrics.lookup(name) is None:
                # Recorded by an older release: the registry no longer has it.
                continue
            parsed[key] = (name, school_id, labels, labels_hash(labels))
        schools = self._live_schools({p[1] for p in parsed.values() if p[1] is not None})
        # A school purged since the value was recorded has nowhere to go.
        parsed = {k: p for k, p in parsed.items() if p[1] is None or p[1] in schools}
        if not parsed:
            return
        MetricSeries.objects.using(self.alias).bulk_create(
            [
                MetricSeries(metric_name=n, school_id=s, labels=l, labels_hash=h, last_seen=self.now)
                for n, s, l, h in parsed.values()
            ],
            ignore_conflicts=True,
        )
        rows = MetricSeries.objects.using(self.alias).filter(
            metric_name__in={p[0] for p in parsed.values()},
            labels_hash__in={p[3] for p in parsed.values()},
        ).values_list("id", "metric_name", "school_id", "labels_hash", "last_seen")
        by_identity = {(n, s, h): (i, seen) for i, n, s, h, seen in rows}
        stale = []
        for key, (name, school_id, _, digest) in parsed.items():
            found = by_identity.get((name, school_id, digest))
            if found is None:
                continue
            self.ids[key] = found[0]
            if found[1] < self.now - LAST_SEEN_EVERY:
                stale.append(found[0])
        if stale:
            MetricSeries.objects.using(self.alias).filter(pk__in=stale).update(last_seen=self.now)


def _upsert(alias, minute, entries, series):
    ids = series.resolve(list(entries))
    rows = []
    for key, value in entries.items():
        series_id = ids.get(key)
        if series_id is None:
            continue
        rows.append(
            (
                series_id,
                minute,
                value["count"],
                value["sum"],
                value["min"],
                value["max"],
                value["last"],
                value["buckets"],
            )
        )
    if not rows:
        return 0
    connection = connections[alias]
    with transaction.atomic(using=alias):
        with connection.cursor() as cursor:
            execute_values(cursor.cursor, UPSERT, rows, template=UPSERT_ROW)
    return len(rows)


def flush_metrics(alias, now=None):
    """Write every settled minute and drop count, and move the watermark.
    False when Redis is unreachable or a minute failed."""
    now = now or timezone.now()
    cutoff = floor_minute(now) - SETTLE
    cutoff_stamp = buffer.minute_stamp(cutoff.timestamp())
    pending = buffer.run(lambda redis: redis.smembers(metrics.PENDING_KEY))
    dropped = buffer.run(
        lambda redis: [key for key in redis.scan_iter(match="obs:dropped:*", count=500)]
    )
    if pending is None or dropped is None:
        return False
    series = _Series(alias, now)
    ok = True
    for stamp in sorted(s for s in pending if s <= cutoff_stamp):
        fields = _take_hash(metrics.mx_key(stamp), pending_member=stamp)
        if fields is None:
            return False
        entries = {key: metrics.unpack(packed) for key, packed in fields.items()}
        ok = _write_minute(alias, stamp, entries, series) and ok
    for key in sorted(dropped):
        stamp = key.rsplit(":", 1)[-1]
        if stamp > cutoff_stamp:
            continue
        fields = _take_hash(key)
        if fields:
            ok = _write_minute(alias, stamp, _drop_entries(fields), series) and ok
    # Switched off, nothing is recorded: the minutes are missing, not 0.
    if metrics.metrics_enabled():
        _advance(alias, METRICS, cutoff)
    return ok


def _write_minute(alias, stamp, entries, series):
    try:
        _upsert(alias, stamp_time(stamp), entries, series)
        return True
    except Exception:
        # Discarded, and the minute marked with drops so it reads as missing.
        logger.exception("Flushing metric minute %s failed; it is discarded", stamp)
        buffer.run(
            lambda redis: redis.hincrby(buffer.dropped_key(stamp), "metric:flush_error", len(entries))
        )
        return False


def rollup(start, alias=None):
    """Recompute the hour starting at ``start`` from its points."""
    alias = alias or MetricRollup.objects.db
    with connections[alias].cursor() as cursor:
        cursor.execute(ROLLUP, {"start": start, "end": start + timedelta(hours=1)})


def rollup_due(alias, now=None):
    """Roll up every full hour since the last one rolled (at most a day
    back), and move the ``rollups`` watermark. Returns the hours rolled."""
    now = now or timezone.now()
    this_hour = now.replace(minute=0, second=0, microsecond=0)
    earliest = this_hour - timedelta(hours=ROLLUP_CATCH_UP)
    done = watermark(ROLLUPS)
    hour = max(done, earliest) if done else this_hour - timedelta(hours=1)
    rolled = []
    while hour < this_hour:
        rollup(hour, alias)
        rolled.append(hour)
        hour += timedelta(hours=1)
        _advance(alias, ROLLUPS, hour)
    return rolled
