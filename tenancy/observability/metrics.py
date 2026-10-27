"""The metric registry (spec 0006, child 0002).

Every metric is declared here once, with its type, unit, scope and label
names (and, where it matters, the label values allowed). Recording anything
else is refused: an error in tests and DEBUG, else dropped and counted as
``obs.dropped{signal=metric, reason=undeclared}``. The school is attached
from ``context.school_id()``, never passed by the caller.

Each process adds observations up in memory per (UTC minute, series), and a
background thread merges them into ``obs:mx:<minute>`` on the telemetry
Redis every ``PUSH_SECONDS`` (and at exit), one Lua call per minute. The
flush writes finished minutes to Postgres (``metric_store``).
"""
import atexit
import json
import logging
import os
import threading
import time
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import timedelta

from celery.signals import worker_process_shutdown
from django.conf import settings

from tenancy.observability import buffer, context

logger = logging.getLogger("ordo.observability")

# Upper bounds in milliseconds, shared by every histogram; one more bucket
# holds the overflow (umbrella, Histogram bounds).
BOUNDS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000, 300000)
BUCKETS = len(BOUNDS) + 1
PUSH_SECONDS = 15
# A minute's hash outlives a flush that never comes.
MX_TTL = 2 * 60 * 60
PENDING_KEY = "obs:mx:pending"
LABEL_MAX = 64
DAU_TTL = 3 * 24 * 60 * 60

COUNTER, HISTOGRAM, GAUGE = "counter", "histogram", "gauge"
SCHOOL, PLATFORM = "school", "platform"


def mx_key(stamp):
    return f"obs:mx:{stamp}"


def dau_key(school_id, day):
    return f"obs:dau:{school_id}:{day:%Y%m%d}"


class UndeclaredMetric(Exception):
    """A metric, label or label value the registry doesn't declare."""


REGISTRY = {}


@dataclass(frozen=True)
class Metric:
    name: str
    type: str
    unit: str
    scope: str
    # Label name -> allowed values: a tuple, a callable giving them, or None
    # for any string (cut to ``LABEL_MAX``).
    labels: dict = field(default_factory=dict)
    description: str = ""
    # Gauges only: how often a value is sampled; reads carry it this long.
    interval: timedelta | None = None

    def add(self, value=1, **labels):
        """Count ``value`` (counters)."""
        self._observe(COUNTER, value, labels)

    def record(self, value, **labels):
        """One observation, in milliseconds for durations (histograms)."""
        self._observe(HISTOGRAM, value, labels)

    def set(self, value, at=None, **labels):
        """The current value (gauges). ``at`` stamps it at another time."""
        self._observe(GAUGE, value, labels, at)

    def allowed(self, name):
        values = self.labels[name]
        return values() if callable(values) else values

    def label_values(self, given):
        """``given`` as the stored labels, or raise ``UndeclaredMetric``."""
        if given.keys() != self.labels.keys():
            raise UndeclaredMetric(
                f"{self.name} takes labels {sorted(self.labels)}, not {sorted(given)}"
            )
        out = {}
        for name in sorted(self.labels):
            value = str(given[name])
            allowed = self.allowed(name)
            if allowed is None:
                value = value[:LABEL_MAX]
            elif value not in allowed:
                raise UndeclaredMetric(f"{self.name}: {name}={value!r} is not allowed")
            out[name] = value
        return out

    def _observe(self, kind, value, labels, at=None):
        if not metrics_enabled():
            return
        try:
            if kind != self.type:
                raise UndeclaredMetric(f"{self.name} is a {self.type}, not a {kind}")
            labels = self.label_values(labels)
            school_id = None if self.scope == PLATFORM else context.school_id()
            _aggregate(self, school_id, labels, float(value), time.time() if at is None else at)
        except UndeclaredMetric:
            if settings.DEBUG or settings.TESTING:
                raise
            buffer.count_drop("metric", "undeclared")
        except Exception:
            # Never into the caller.
            logger.exception("Recording %s failed", self.name)


def _declare(name, type, unit, scope=SCHOOL, labels=None, description="", interval=None):
    if name in REGISTRY:
        raise ValueError(f"{name} is declared twice")
    metric = Metric(name, type, unit, scope, labels or {}, description, interval)
    REGISTRY[name] = metric
    return metric


def counter(name, unit="1", **options):
    return _declare(name, COUNTER, unit, **options)


def histogram(name, unit="ms", **options):
    return _declare(name, HISTOGRAM, unit, **options)


def gauge(name, unit, interval, **options):
    return _declare(name, GAUGE, unit, interval=interval, **options)


def lookup(name):
    """The declared metric, or None."""
    return REGISTRY.get(name)


def metrics_enabled():
    return settings.OBSERVABILITY_ENABLED and settings.OBSERVABILITY_METRICS_ENABLED


def status_class(status):
    return "5xx" if status >= 500 else "4xx" if status >= 400 else "3xx" if status >= 300 else "2xx"


def http_method(method):
    return method if method in HTTP_METHODS else "OTHER"


def _disk_paths():
    return tuple(settings.OBS_DISK_PATHS)


def _sync_buckets():
    from ems.portal_sync.reconcile import BUCKETS as SYNC_BUCKETS

    return SYNC_BUCKETS


MINUTE = timedelta(minutes=1)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "OTHER")
STATUS_CLASSES = ("2xx", "3xx", "4xx", "5xx")
HTTP_LABELS = {"route": None, "method": HTTP_METHODS, "status_class": STATUS_CLASSES}
OPERATIONS = ("select", "insert", "update", "delete", "other")
TASK_OUTCOMES = ("succeeded", "failed", "retried", "expected")
GENERATION_KINDS = ("timetable", "distribution", "allocation", "distribution_all", "allocation_all")
UPLOAD_KINDS = ("department", "hall", "course", "classes", "class_courses", "class_students", "bulk")
EXPORT_KINDS = ("timetable", "distribution", "arrangement", "directory", "users")
SUCCEEDED_FAILED = ("succeeded", "failed")

# Built in.
HTTP_REQUESTS = counter("http.server.requests", labels=HTTP_LABELS, description="API requests")
HTTP_DURATION = histogram("http.server.duration", labels=HTTP_LABELS, description="Request duration")
DB_QUERIES = histogram(
    "db.queries", unit="1", labels={"route": None}, description="Database queries per request"
)
DB_QUERY_DURATION = histogram(
    "db.query.duration", labels={"operation": OPERATIONS}, description="Database query duration"
)
DB_SLOW_QUERIES = counter(
    "db.slow_queries", labels={"operation": OPERATIONS}, description="Queries over 500 ms"
)
REDIS_DURATION = histogram("redis.duration", labels={"command": None}, description="Redis call duration")
TASK_DURATION = histogram(
    "celery.task.duration",
    labels={"task": None, "outcome": TASK_OUTCOMES},
    description="Background job duration",
)
TASK_FAILURES = counter(
    "celery.task.failures", labels={"task": None}, description="Background jobs that failed for good"
)
QUEUE_DEPTH = gauge(
    "celery.queue.depth", "1", MINUTE, scope=PLATFORM, labels={"queue": None},
    description="Jobs waiting in the queue",
)
QUEUE_WAIT = histogram(
    "celery.queue.wait", labels={"queue": None, "task": None}, description="Time a job waited to start"
)
HOST_CPU = gauge("host.cpu.percent", "%", MINUTE, scope=PLATFORM, description="Host CPU use")
HOST_MEMORY = gauge("host.memory.percent", "%", MINUTE, scope=PLATFORM, description="Host memory use")
HOST_DISK_USED = gauge(
    "host.disk.used_bytes", "By", MINUTE, scope=PLATFORM, labels={"path": _disk_paths},
    description="Disk space used",
)
HOST_DISK_FREE = gauge(
    "host.disk.free_bytes", "By", MINUTE, scope=PLATFORM, labels={"path": _disk_paths},
    description="Disk space free",
)
DB_DATABASE_BYTES = gauge(
    "db.database.bytes", "By", HOUR, scope=PLATFORM, description="Postgres database size"
)
DB_SCHEMA_BYTES = gauge("db.schema.bytes", "By", HOUR, description="The school's Postgres schema size")
MEDIA_BYTES = gauge("media.bytes", "By", DAY, description="The school's uploaded files")
DROPPED = counter(
    "obs.dropped",
    scope=PLATFORM,
    labels={
        "signal": ("segment", "span", "log", "metric"),
        "reason": ("redis_down", "buffer_full", "flush_error", "undeclared"),
    },
    description="Telemetry dropped before storage",
)
BROWSER_VITALS = histogram(
    "browser.vitals",
    labels={"name": ("LCP", "INP", "CLS"), "page": None},
    description="Web Vitals (CLS times 1000)",
)

# Business.
GENERATION_RUNS = counter(
    "ordo.timetable.runs",
    labels={"kind": GENERATION_KINDS, "outcome": ("started", *SUCCEEDED_FAILED)},
    description="Generation runs",
)
GENERATION_DURATION = histogram(
    "ordo.timetable.duration",
    labels={"kind": GENERATION_KINDS, "outcome": SUCCEEDED_FAILED},
    description="Generation run duration",
)
SYNC_RUNS = counter(
    "ordo.portal_sync.runs",
    labels={"outcome": SUCCEEDED_FAILED, "preview": ("yes", "no")},
    description="Portal sync runs",
)
SYNC_RECORDS = counter(
    "ordo.portal_sync.records", labels={"outcome": _sync_buckets}, description="Portal records synced"
)
LOGINS = counter(
    "ordo.auth.logins",
    labels={"outcome": SUCCEEDED_FAILED, "via": ("api", "session")},
    description="Sign ins",
)
DAILY_ACTIVE = gauge("ordo.users.daily_active", "1", DAY, description="Users active in the day")
UPLOADS = counter("ordo.uploads", labels={"kind": UPLOAD_KINDS}, description="Uploads")
UPLOAD_BYTES = counter(
    "ordo.uploads.bytes", unit="By", labels={"kind": UPLOAD_KINDS}, description="Uploaded bytes"
)
EXPORTS = counter("ordo.exports", labels={"kind": EXPORT_KINDS}, description="Exports")
EXPORT_BYTES = counter(
    "ordo.exports.bytes", unit="By", labels={"kind": EXPORT_KINDS}, description="Exported bytes"
)


# In process aggregation. A value is [count, sum, min, max, last, last_at,
# buckets or None].
_agg = {}
_agg_lock = threading.Lock()
# (school_id, day, user_id) waiting for PFADD, and the ones already sent.
_dau = set()
_dau_sent = set()
_dau_day = None
_thread = None
_pid = None
_thread_lock = threading.Lock()


def series_key(name, school_id, labels):
    return f"{name}|{'' if school_id is None else school_id}|{canonical(labels)}"


def parse_series_key(key):
    name, school, labels = key.split("|", 2)
    return name, (int(school) if school else None), json.loads(labels)


def canonical(labels):
    return json.dumps(labels, sort_keys=True, separators=(",", ":"))


def bucket_index(value):
    return bisect_left(BOUNDS, value)


def _aggregate(metric, school_id, labels, value, at):
    minute = int(at // 60) * 60
    key = (minute, series_key(metric.name, school_id, labels))
    _ensure_thread()
    with _agg_lock:
        entry = _agg.get(key)
        if entry is None:
            buckets = [0] * BUCKETS if metric.type == HISTOGRAM else None
            entry = _agg[key] = [0, 0.0, value, value, None, None, buckets]
        entry[0] += 1
        entry[1] += value
        entry[2] = min(entry[2], value)
        entry[3] = max(entry[3], value)
        if metric.type == GAUGE and (entry[5] is None or at >= entry[5]):
            entry[4], entry[5] = value, at
        if entry[6] is not None:
            entry[6][bucket_index(value)] += 1


def mark_active(school_id, user_id):
    """Count a signed in user towards the school's daily actives."""
    global _dau_day
    if not metrics_enabled() or school_id is None or user_id is None:
        return
    day = time.gmtime()[:3]
    item = (school_id, day, user_id)
    with _agg_lock:
        if _dau_day != day:
            _dau_day = day
            _dau_sent.clear()
        if item in _dau_sent:
            return
        _dau_sent.add(item)
        _dau.add(item)
    _ensure_thread()


def _number(value):
    if value is None:
        return ""
    return repr(int(value)) if float(value).is_integer() and abs(value) < 1e15 else repr(float(value))


def pack(entry):
    """``count,sum,min,max,last,last_at[,bucket...]``; blank is none."""
    count, total, low, high, last, last_at, buckets = entry
    parts = [_number(count), _number(total), _number(low), _number(high), _number(last), _number(last_at)]
    if buckets is not None:
        parts.extend(str(n) for n in buckets)
    return ",".join(parts)


def unpack(text):
    parts = text.split(",")

    def num(value):
        return float(value) if value != "" else None

    buckets = [int(float(n)) for n in parts[6:]] or None
    return {
        "count": int(float(parts[0])),
        "sum": float(parts[1]),
        "min": num(parts[2]),
        "max": num(parts[3]),
        "last": num(parts[4]),
        "last_at": num(parts[5]),
        "buckets": buckets,
    }


# Merge each field into the minute's hash, then mark the minute pending.
MERGE = """
local function split(s)
  local t = {}
  for v in string.gmatch(s .. ',', '([^,]*),') do t[#t + 1] = v end
  return t
end
for i = 3, #ARGV, 2 do
  local new = split(ARGV[i + 1])
  local raw = redis.call('HGET', KEYS[1], ARGV[i])
  if raw then
    local old = split(raw)
    new[1] = tostring(tonumber(old[1]) + tonumber(new[1]))
    new[2] = tostring(tonumber(old[2]) + tonumber(new[2]))
    if old[3] ~= '' and (new[3] == '' or tonumber(old[3]) < tonumber(new[3])) then new[3] = old[3] end
    if old[4] ~= '' and (new[4] == '' or tonumber(old[4]) > tonumber(new[4])) then new[4] = old[4] end
    if old[6] ~= '' and (new[6] == '' or tonumber(old[6]) > tonumber(new[6])) then
      new[5] = old[5]
      new[6] = old[6]
    end
    for j = 7, #new do new[j] = tostring(tonumber(old[j] or '0') + tonumber(new[j])) end
  end
  redis.call('HSET', KEYS[1], ARGV[i], table.concat(new, ','))
end
redis.call('EXPIRE', KEYS[1], ARGV[2])
redis.call('SADD', KEYS[2], ARGV[1])
return 1
"""
_scripts = {}


def _merge_script(redis):
    script = _scripts.get(id(redis))
    if script is None:
        script = _scripts[id(redis)] = redis.register_script(MERGE)
    return script


def push():
    """Merge everything aggregated so far into the telemetry Redis, now."""
    global _agg, _dau
    with _agg_lock:
        taken, _agg = _agg, {}
        active, _dau = _dau, set()
    by_minute = {}
    for (minute, key), entry in taken.items():
        by_minute.setdefault(minute, []).append((key, pack(entry)))
    for minute, fields in sorted(by_minute.items()):
        stamp = buffer.minute_stamp(minute)
        args = [stamp, MX_TTL]
        for key, packed in fields:
            args.extend((key, packed))
        buffer.run(
            lambda redis: _merge_script(redis)(keys=[mx_key(stamp), PENDING_KEY], args=args),
            "metric",
            len(fields),
        )
    if active:
        _push_active(active)


def _push_active(active):
    from datetime import date

    by_key = {}
    for school_id, day, user_id in active:
        by_key.setdefault(dau_key(school_id, date(*day)), []).append(user_id)

    def write(redis):
        pipe = redis.pipeline(transaction=False)
        for key, users in by_key.items():
            pipe.pfadd(key, *users)
            pipe.expire(key, DAU_TTL)
        pipe.execute()

    buffer.run(write)


def pending():
    """A copy of what's aggregated and not yet pushed (tests)."""
    with _agg_lock:
        return dict(_agg)


def reset():
    """Forget everything not yet pushed (tests)."""
    global _agg, _dau
    with _agg_lock:
        _agg, _dau = {}, set()
        _dau_sent.clear()


def _ensure_thread():
    # A forked child starts its own thread; the parent's never crosses a fork.
    # Tests push by hand.
    global _thread, _pid
    if (_thread is not None and _pid == os.getpid()) or settings.TESTING:
        return
    with _thread_lock:
        if _thread is None or _pid != os.getpid():
            _pid = os.getpid()
            _thread = threading.Thread(target=_run, name="ordo-telemetry-metrics", daemon=True)
            _thread.start()


def _run():
    while True:
        time.sleep(PUSH_SECONDS)
        try:
            push()
        except Exception:
            logger.exception("Pushing metrics failed")


def _push_at_exit(**kwargs):
    if _pid == os.getpid():
        try:
            push()
        except Exception:
            pass


atexit.register(_push_at_exit)
# A prefork child leaves through ``os._exit``, which skips ``atexit``.
worker_process_shutdown.connect(_push_at_exit, weak=False, dispatch_uid="ordo_observability_metrics_exit")
