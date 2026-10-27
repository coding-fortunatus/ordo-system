"""Buffered telemetry into Postgres (spec 0006, Flush).

One flush at a time (a Redis lock), every 10 seconds on the
``observability`` queue. Each buffer gives up to ``BATCH`` items, written
with ``COPY`` into the parent table; Postgres routes each row to its day's
partition. A batch that fails is logged, counted as ``flush_error`` and
discarded, so one bad item can't jam the queue behind it. Settled metric
minutes and drop counts follow (``metric_store``).
"""
import io
import json
import logging
import time
import uuid

from django.conf import settings
from django.db import connections, transaction

from tenancy.observability import buffer, metric_store

logger = logging.getLogger("ordo.observability")

BATCH = 20000
LOCK_KEY = "obs:flush:lock"
LOCK_SECONDS = 60
LAST_OK_KEY = "obs:flush:last_ok"
STATEMENT_TIMEOUT = "30s"

SEGMENT_COLUMNS = (
    "root_span_id",
    "trace_id",
    "started_at",
    "school_id",
    "kind",
    "root_name",
    "route",
    "method",
    "status_code",
    "duration_ms",
    "is_error",
    "is_slow",
    "span_count",
    "truncated",
    "user_id",
    "request_id",
    "release",
    "process_role",
)
SPAN_COLUMNS = (
    "span_id",
    "trace_id",
    "parent_span_id",
    "root_span_id",
    "started_at",
    "school_id",
    "name",
    "kind",
    "duration_ms",
    "status",
    "attributes",
)
# The id comes from the table's sequence.
LOG_COLUMNS = (
    "logged_at",
    "school_id",
    "level",
    "logger",
    "message",
    "trace_id",
    "span_id",
    "request_id",
    "user_id",
    "attributes",
)
# Buffer signal -> (table, columns), in the order they are flushed.
TABLES = {
    "segment": ("obs_segment", SEGMENT_COLUMNS),
    "span": ("obs_span", SPAN_COLUMNS),
    "log": ("obs_log", LOG_COLUMNS),
}


def _csv(value):
    """One field of Postgres CSV: unquoted empty is NULL, so every string is
    quoted (an empty string stays an empty string)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (dict, list)):
        value = json.dumps(value, separators=(",", ":"))
    # Postgres text can't hold NUL.
    text = str(value).replace("\x00", "")
    return '"' + text.replace('"', '""') + '"'


def _copy(alias, table, columns, items):
    data = io.StringIO()
    for item in items:
        data.write(",".join(_csv(item.get(column)) for column in columns))
        data.write("\n")
    data.seek(0)
    connection = connections[alias]
    with transaction.atomic(using=alias):
        with connection.cursor() as cursor:
            cursor.copy_expert(
                f"COPY public.{table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv)", data
            )


def flush():
    """Write what's buffered. False when another flush holds the lock or a
    batch failed; True on a full success (and ``obs:flush:last_ok`` is set)."""
    token = uuid.uuid4().hex
    if not buffer.run(lambda redis: redis.set(LOCK_KEY, token, nx=True, ex=LOCK_SECONDS)):
        return False
    alias = settings.OBSERVABILITY_DB_ALIAS
    connection = connections[alias]
    ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
        for signal, (table, columns) in TABLES.items():
            items = buffer.take(signal, BATCH)
            if not items:
                continue
            try:
                _copy(alias, table, columns, items)
            except Exception:
                ok = False
                buffer.count_drop(signal, "flush_error", len(items))
                logger.exception("Flushing %d %s items failed; the batch is discarded", len(items), signal)
        try:
            ok = metric_store.flush_metrics(alias) and ok
        except Exception:
            ok = False
            logger.exception("Flushing metrics failed")
        if ok:
            buffer.run(lambda redis: redis.set(LAST_OK_KEY, int(time.time())))
        return ok
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute("RESET statement_timeout")
        except Exception:
            pass

        def release(redis):
            if redis.get(LOCK_KEY) == token:
                redis.delete(LOCK_KEY)

        buffer.run(release)
