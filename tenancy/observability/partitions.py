"""Daily partitions of the ``obs_*`` tables (spec 0006, Partitions).

Each partitioned table has one partition per UTC day, ``<table>_pYYYYMMDD``
covering ``[day, day + 1)``, and no default partition. ``maintain`` keeps
today and the next 3 days created, and removes whole days past retention
by detaching the partition first (concurrently, so the flush and readers
are never stuck behind a lock on the parent) and then dropping it.
"""
import logging
import re
from datetime import datetime, time, timedelta, timezone as dt_timezone

from django.conf import settings
from django.db import DatabaseError, connections, transaction
from django.utils import timezone

logger = logging.getLogger("ordo.observability")

DAYS_AHEAD = 3
LOCK_TIMEOUT = "5s"
STATEMENT_TIMEOUT = "60s"

# Table -> the setting holding its retention in days. Each slice's
# migration creates its table before it is listed here.
TABLES = {
    "obs_segment": "OBS_TRACE_KEEP_DAYS",
    "obs_span": "OBS_TRACE_KEEP_DAYS",
    "obs_log": "OBS_LOG_KEEP_DAYS",
    "obs_metric_point": "OBS_POINT_KEEP_DAYS",
}

_SUFFIX = re.compile(r"_p(\d{8})$")


def partition_name(table, day):
    return f"{table}_p{day:%Y%m%d}"


def _start(day):
    return datetime.combine(day, time.min, tzinfo=dt_timezone.utc)


def _bound(day):
    return _start(day).isoformat()


def ensure(cursor, tables, today, days_ahead=DAYS_AHEAD):
    """Create today's partition and the next ``days_ahead`` for each table."""
    for table in tables:
        for offset in range(days_ahead + 1):
            day = today + timedelta(days=offset)
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS public.{partition_name(table, day)} "
                f"PARTITION OF public.{table} "
                f"FOR VALUES FROM ('{_bound(day)}') TO ('{_bound(day + timedelta(days=1))}')"
            )


def partitions(cursor, table):
    """``[(name, day, detach_pending)]`` attached to ``table``, oldest first,
    plus tables already detached but not yet dropped (``detach_pending`` None)."""
    cursor.execute(
        """
        SELECT c.relname, i.inhdetachpending
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        JOIN pg_namespace n ON n.oid = p.relnamespace
        WHERE n.nspname = 'public' AND p.relname = %s
        """,
        [table],
    )
    found = {name: pending for name, pending in cursor.fetchall()}
    cursor.execute(
        """
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relispartition
          AND c.relname LIKE %s
        """,
        [table.replace("_", r"\_") + r"\_p%"],
    )
    for (name,) in cursor.fetchall():
        found.setdefault(name, None)
    rows = []
    for name, pending in found.items():
        match = _SUFFIX.search(name)
        if match:
            rows.append((name, datetime.strptime(match.group(1), "%Y%m%d").date(), pending))
    return sorted(rows, key=lambda r: r[1])


def _remove(connection, table, name, pending):
    """Detach (unless already detached) and drop one partition."""
    concurrently = not connection.in_atomic_block
    with connection.cursor() as cursor:
        if pending is True:
            cursor.execute(f"ALTER TABLE public.{table} DETACH PARTITION public.{name} FINALIZE")
        elif pending is False:
            mode = " CONCURRENTLY" if concurrently else ""
            cursor.execute(f"ALTER TABLE public.{table} DETACH PARTITION public.{name}{mode}")
        cursor.execute(f"DROP TABLE IF EXISTS public.{name}")


def maintain(using=None, now=None):
    """Create partitions ahead and drop expired ones. A partition that can't
    be locked within ``LOCK_TIMEOUT`` is left for the next run. Returns the
    names dropped."""
    alias = using or settings.OBSERVABILITY_DB_ALIAS
    connection = connections[alias]
    now = now or timezone.now()
    today = now.astimezone(dt_timezone.utc).date()
    dropped = []
    with connection.cursor() as cursor:
        cursor.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        cursor.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
    try:
        with connection.cursor() as cursor:
            ensure(cursor, TABLES, today)
        for table, keep_setting in TABLES.items():
            cutoff = now - timedelta(days=getattr(settings, keep_setting))
            with connection.cursor() as cursor:
                old = [
                    (name, pending)
                    for name, day, pending in partitions(cursor, table)
                    if _start(day + timedelta(days=1)) <= cutoff
                ]
            for name, pending in old:
                try:
                    if connection.in_atomic_block:
                        with transaction.atomic(using=alias):
                            _remove(connection, table, name, pending)
                    else:
                        _remove(connection, table, name, pending)
                    dropped.append(name)
                except DatabaseError as exc:
                    logger.warning("Partition %s not dropped, retrying next run: %s", name, exc)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET lock_timeout")
            cursor.execute("RESET statement_timeout")
    return dropped


# The partitioned tables holding a school's rows: by ``school_id``, or (the
# metric points, which have no school column) by the school's series.
SCHOOL_ROWS = {
    "obs_segment": "school_id = %s",
    "obs_span": "school_id = %s",
    "obs_log": "school_id = %s",
    "obs_metric_point": "series_id IN (SELECT id FROM public.tenancy_metricseries WHERE school_id = %s)",
}


def delete_school(school_id, using=None):
    """Delete a purged school's rows from every partitioned table, one
    partition at a time so no single statement scans every day. Returns the
    rows deleted."""
    alias = using or settings.OBSERVABILITY_DB_ALIAS
    connection = connections[alias]
    deleted = 0
    with connection.cursor() as cursor:
        cursor.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
        try:
            for table, where in SCHOOL_ROWS.items():
                for name, _day, _pending in partitions(cursor, table):
                    cursor.execute(f"DELETE FROM public.{name} WHERE {where}", [school_id])
                    deleted += cursor.rowcount
        finally:
            cursor.execute("RESET statement_timeout")
    return deleted
