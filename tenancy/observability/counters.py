"""Redis counters, sampling and throttle keys (spec 0004, Counters).

High volume signals (every API response) never write to the database. Each
count lands in two hashes: per minute for the 60 minute status window, per
hour for the hourly buckets. Redis is a soft dependency: every call here
returns None (or does nothing) when Redis can't be reached, and after a
failure Redis is skipped for a few seconds so requests don't each wait on a
dead connection.

``OBSERVABILITY_REDIS_URL=memory://`` keeps everything in process memory,
for tests and for a single process run without Redis.
"""
import fnmatch
import logging
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("ordo.observability")

FIELDS = (
    "requests",
    "server_errors",
    "client_errors",
    "ws_errors",
    "job_failures",
    "expected_job_failures",
    "js_errors",
)
MINUTE_TTL = 2 * 60 * 60
HOUR_TTL = 3 * 60 * 60
SAMPLE_TTL = 2 * 60 * 60
# How long Redis is skipped after a failed call.
BACKOFF_SECONDS = 15
TIMEOUT_SECONDS = 0.5

_client = None
_down_until = 0.0
_lock = threading.Lock()


def school_key(school_id):
    return str(school_id) if school_id else "platform"


def minute_stamp(at):
    return at.strftime("%Y%m%d%H%M")


def hour_stamp(at):
    return at.strftime("%Y%m%d%H")


def minute_key(key, at):
    return f"obs:m:{key}:{minute_stamp(at)}"


def hour_key(key, at):
    return f"obs:h:{key}:{hour_stamp(at)}"


def client():
    """The shared Redis client, or None while Redis is backing off."""
    global _client
    if time.monotonic() < _down_until:
        return None
    if _client is None:
        with _lock:
            if _client is None:
                url = settings.OBSERVABILITY_REDIS_URL
                if url.startswith("memory://"):
                    _client = MemoryRedis()
                else:
                    import redis

                    _client = redis.Redis.from_url(
                        url,
                        socket_timeout=TIMEOUT_SECONDS,
                        socket_connect_timeout=TIMEOUT_SECONDS,
                        decode_responses=True,
                    )
    return _client


def set_client(value):
    """Swap the client (tests). None makes the next call build a new one."""
    global _client, _down_until
    _client = value
    _down_until = 0.0


def run(fn):
    """``fn(redis)``'s result, or None if Redis is down or the call fails."""
    global _down_until
    redis = client()
    if redis is None:
        return None
    try:
        return fn(redis)
    except Exception:
        _down_until = time.monotonic() + BACKOFF_SECONDS
        logger.warning("Redis unavailable for observability counters", exc_info=True)
        return None


def incr(school_id, **counts):
    """Add ``counts`` (field=n) to the school's minute and hour hashes."""
    if not settings.OBSERVABILITY_ENABLED or not counts:
        return
    now = timezone.now()
    key = school_key(school_id)

    def write(redis):
        pipe = redis.pipeline(transaction=False)
        for name, ttl in ((minute_key(key, now), MINUTE_TTL), (hour_key(key, now), HOUR_TTL)):
            for field, n in counts.items():
                pipe.hincrby(name, field, n)
            pipe.expire(name, ttl)
        pipe.execute()

    run(write)


def sample(group_id):
    """This occurrence's number within the group's clock hour, or None."""
    stamp = hour_stamp(timezone.now())

    def count(redis):
        name = f"obs:sample:{group_id}:{stamp}"
        pipe = redis.pipeline(transaction=False)
        pipe.incr(name)
        pipe.expire(name, SAMPLE_TTL)
        return int(pipe.execute()[0])

    return run(count)


def hit(name, ttl):
    """Increment a throttle counter; its new value, or None."""

    def count(redis):
        pipe = redis.pipeline(transaction=False)
        pipe.incr(name)
        pipe.expire(name, ttl)
        return int(pipe.execute()[0])

    return run(count)


def read_hashes(names):
    """``{name: {field: int}}`` for each hash, or None if Redis is down."""

    def read(redis):
        pipe = redis.pipeline(transaction=False)
        for name in names:
            pipe.hgetall(name)
        return {
            name: {f: int(v) for f, v in (values or {}).items()}
            for name, values in zip(names, pipe.execute())
        }

    return run(read)


def last_minutes(key, now, minutes=60):
    """Field totals over the last ``minutes`` minute hashes, or None."""
    names = [minute_key(key, now - timedelta(minutes=i)) for i in range(minutes)]
    hashes = read_hashes(names)
    if hashes is None:
        return None
    totals = dict.fromkeys(FIELDS, 0)
    for values in hashes.values():
        for field, n in values.items():
            if field in totals:
                totals[field] += n
    return totals


class MemoryRedis:
    """The few Redis commands observability uses, in process memory."""

    def __init__(self):
        self._data = {}
        self._expires = {}
        self._lock = threading.RLock()

    def _live(self, name):
        expires = self._expires.get(name)
        if expires is not None and expires <= time.monotonic():
            self._data.pop(name, None)
            self._expires.pop(name, None)
        return self._data.get(name)

    def pipeline(self, transaction=False):
        return _MemoryPipeline(self)

    def flushall(self):
        with self._lock:
            self._data.clear()
            self._expires.clear()

    def incr(self, name, amount=1):
        with self._lock:
            value = int(self._live(name) or 0) + amount
            self._data[name] = value
            return value

    def hincrby(self, name, field, amount=1):
        with self._lock:
            values = self._live(name)
            if values is None:
                values = self._data[name] = {}
            values[field] = int(values.get(field, 0)) + amount
            return values[field]

    def hgetall(self, name):
        with self._lock:
            return dict(self._live(name) or {})

    def expire(self, name, seconds):
        with self._lock:
            if self._live(name) is None:
                return False
            self._expires[name] = time.monotonic() + seconds
            return True

    def set(self, name, value, nx=False, ex=None):
        with self._lock:
            if nx and self._live(name) is not None:
                return None
            self._data[name] = value
            if ex:
                self._expires[name] = time.monotonic() + ex
            else:
                self._expires.pop(name, None)
            return True

    def get(self, name):
        with self._lock:
            return self._live(name)

    def exists(self, name):
        with self._lock:
            return int(self._live(name) is not None)

    def delete(self, *names):
        with self._lock:
            return sum(self._data.pop(n, None) is not None for n in names)

    def keys(self, pattern="*"):
        with self._lock:
            return [k for k in list(self._data) if self._live(k) is not None and fnmatch.fnmatch(k, pattern)]

    def zadd(self, name, mapping):
        with self._lock:
            values = self._live(name)
            if values is None:
                values = self._data[name] = {}
            values.update(mapping)
            return len(mapping)

    def zremrangebyscore(self, name, low, high):
        with self._lock:
            values = self._live(name) or {}
            low = float("-inf") if low == "-inf" else float(low)
            high = float("inf") if high == "+inf" else float(high)
            gone = [m for m, s in values.items() if low <= s <= high]
            for member in gone:
                del values[member]
            return len(gone)

    def zrange(self, name, start, end):
        with self._lock:
            members = sorted((self._live(name) or {}).items(), key=lambda kv: kv[1])
            names = [m for m, _ in members]
            return names[start:] if end == -1 else names[start : end + 1]

    def zcard(self, name):
        with self._lock:
            return len(self._live(name) or {})

    def sadd(self, name, *members):
        with self._lock:
            values = self._live(name)
            if values is None:
                values = self._data[name] = set()
            added = len(set(members) - values)
            values.update(members)
            return added

    def sismember(self, name, member):
        with self._lock:
            return int(member in (self._live(name) or set()))

    def scard(self, name):
        with self._lock:
            return len(self._live(name) or set())


class _MemoryPipeline:
    def __init__(self, redis):
        self._redis = redis
        self._calls = []

    def __getattr__(self, command):
        def queue(*args, **kwargs):
            self._calls.append((command, args, kwargs))
            return self

        return queue

    def execute(self):
        calls, self._calls = self._calls, []
        return [getattr(self._redis, c)(*a, **k) for c, a, k in calls]
