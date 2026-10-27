"""The telemetry Redis: buffers between capture and the flush (spec 0006,
Buffer).

Its own client on ``OBSERVABILITY_BUFFER_REDIS_URL``, with the same timeout
and back off rule as spec 0004's ``counters.py`` but none of its keys. A push
never raises and never waits more than ``TIMEOUT_SECONDS``: when Redis is
down, full (``noeviction`` answers OOM) or a list holds ``OBS_BUFFER_MAX``
items, the items are dropped and counted. Counts are kept in process and
written to ``obs:dropped:<minute>`` on the next call that reaches Redis.

``memory://`` runs an in process fake (``fakeredis``), for tests.
"""
import json
import logging
import threading
import time

from django.conf import settings
from opentelemetry.instrumentation.utils import suppress_instrumentation

from tenancy.observability.counters import BACKOFF_SECONDS, TIMEOUT_SECONDS

logger = logging.getLogger("ordo.observability")

SIGNALS = ("segment", "span", "log")
DROPPED_TTL = 3 * 60 * 60
# Drop counts older than this are discarded instead of pushed.
DROP_KEEP_SECONDS = 2 * 60 * 60
WARN_EVERY_SECONDS = 60

_client = None
_down_until = 0.0
_lock = threading.Lock()
# {(minute stamp, signal, reason): n}, waiting to reach Redis.
_drops = {}
_drops_lock = threading.Lock()
_warned_at = 0.0


def buffer_key(signal):
    return f"obs:buf:{signal}"


def dropped_key(stamp):
    return f"obs:dropped:{stamp}"


def minute_stamp(epoch):
    return time.strftime("%Y%m%d%H%M", time.gmtime(epoch))


def client():
    """The telemetry Redis client, or None while it is backing off."""
    global _client
    if time.monotonic() < _down_until:
        return None
    if _client is None:
        with _lock:
            if _client is None:
                url = settings.OBSERVABILITY_BUFFER_REDIS_URL
                if url.startswith("memory://"):
                    import fakeredis

                    _client = fakeredis.FakeRedis(decode_responses=True)
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


def count_drop(signal, reason, n=1):
    if n <= 0:
        return
    key = (minute_stamp(time.time()), signal, reason)
    with _drops_lock:
        _drops[key] = _drops.get(key, 0) + n


def pending_drops():
    """A copy of the counts not yet pushed (tests)."""
    with _drops_lock:
        return dict(_drops)


def _take_drops():
    global _drops
    oldest = minute_stamp(time.time() - DROP_KEEP_SECONDS)
    with _drops_lock:
        taken, _drops = _drops, {}
    return {k: n for k, n in taken.items() if k[0] >= oldest}


def _restore_drops(taken):
    with _drops_lock:
        for key, n in taken.items():
            _drops[key] = _drops.get(key, 0) + n


def _warn(message):
    global _warned_at
    now = time.monotonic()
    if now - _warned_at >= WARN_EVERY_SECONDS:
        _warned_at = now
        logger.warning(message)


def run(fn, signal=None, items=0):
    """``fn(redis)``'s result, or None. On failure the ``items`` being
    written for ``signal`` are counted as dropped. Never traced."""
    global _down_until
    redis = client()
    if redis is None:
        if signal:
            count_drop(signal, "redis_down", items)
        return None
    try:
        with suppress_instrumentation():
            result = fn(redis)
            _push_drops(redis)
        return result
    except Exception as exc:
        _down_until = time.monotonic() + BACKOFF_SECONDS
        if signal:
            full = str(exc).startswith("OOM")
            count_drop(signal, "buffer_full" if full else "redis_down", items)
        _warn(f"Telemetry Redis unavailable, dropping telemetry: {exc}")
        return None


def _push_drops(redis):
    taken = _take_drops()
    if not taken:
        return
    try:
        pipe = redis.pipeline(transaction=False)
        for (stamp, signal, reason), n in taken.items():
            pipe.hincrby(dropped_key(stamp), f"{signal}:{reason}", n)
            pipe.expire(dropped_key(stamp), DROPPED_TTL)
        pipe.execute()
    except Exception:
        _restore_drops(taken)
        raise


def push(signal, items):
    """Append ``items`` (JSON safe dicts) to the signal's buffer list."""
    if not items:
        return
    payload = [json.dumps(item, separators=(",", ":"), default=str) for item in items]
    key = buffer_key(signal)
    limit = settings.OBS_BUFFER_MAX

    def write(redis):
        # The trim keeps the list at the cap; what it cut is ours, dropped.
        pipe = redis.pipeline(transaction=True)
        pipe.rpush(key, *payload)
        pipe.ltrim(key, 0, limit - 1)
        length = pipe.execute()[0]
        return min(len(payload), max(0, length - limit))

    over = run(write, signal, len(payload))
    if over:
        count_drop(signal, "buffer_full", over)
        _warn(f"Telemetry buffer {key} is full, dropping {signal} items")


def take(signal, n):
    """Up to ``n`` items from the front of the signal's buffer, removed
    atomically; [] when empty or when Redis is down."""
    key = buffer_key(signal)

    def read(redis):
        pipe = redis.pipeline(transaction=True)
        pipe.lrange(key, 0, n - 1)
        pipe.ltrim(key, n, -1)
        return pipe.execute()[0]

    raw = run(read) or []
    items = []
    for value in raw:
        try:
            items.append(json.loads(value))
        except ValueError:
            count_drop(signal, "flush_error")
    return items
