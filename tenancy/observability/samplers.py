"""Sampled metrics (spec 0006, child 0002): the beat tasks that look at the
broker, the host, the database and the media volume, and turn yesterday's
active users into a number. They run on the ``observability`` queue, which
only ``worker-observability`` serves in production (it mounts the disks and
media read only)."""
import logging
import os
from datetime import datetime, time as dt_time, timedelta, timezone as dt_timezone

from django.conf import settings
from django.db import connection
from django.utils import timezone

from tenancy.observability import buffer, context, metrics

logger = logging.getLogger("ordo.observability")

# Kombu's Redis transport keeps each queue's priorities in separate lists.
PRIORITY_SEPARATOR = "\x06\x16"
PRIORITY_STEPS = (3, 6, 9)
BROKER_TIMEOUT = 2


def live_schools():
    from django_tenants.utils import get_public_schema_name

    from tenancy.models import School

    return (
        School.objects.filter(status__in=School.LIVE_STATUSES)
        .exclude(schema_name=get_public_schema_name())
        .values_list("pk", "schema_name")
    )


def queues():
    from core.celery import OBSERVABILITY_QUEUE

    return ("celery", OBSERVABILITY_QUEUE)


def observe_queues():
    """Jobs waiting in each Celery queue (every minute)."""
    url = settings.CELERY_BROKER_URL
    if not url.startswith(("redis://", "rediss://")):
        return
    import redis

    broker = redis.Redis.from_url(url, socket_timeout=BROKER_TIMEOUT, socket_connect_timeout=BROKER_TIMEOUT)
    try:
        for queue in queues():
            names = [queue, *(f"{queue}{PRIORITY_SEPARATOR}{step}" for step in PRIORITY_STEPS)]
            pipe = broker.pipeline(transaction=False)
            for name in names:
                pipe.llen(name)
            metrics.QUEUE_DEPTH.set(sum(pipe.execute()), queue=queue)
    finally:
        broker.close()


def observe_host():
    """CPU, memory and disk use of the host (every minute)."""
    import psutil

    metrics.HOST_CPU.set(psutil.cpu_percent(interval=1))
    metrics.HOST_MEMORY.set(psutil.virtual_memory().percent)
    for path in settings.OBS_DISK_PATHS:
        try:
            usage = psutil.disk_usage(path)
        except OSError:
            logger.warning("Disk path %s can't be read", path)
            continue
        metrics.HOST_DISK_USED.set(usage.used, path=path)
        metrics.HOST_DISK_FREE.set(usage.free, path=path)


def observe_db_size():
    """The database's size, and each live school's schema (hourly)."""
    schools = dict((schema, pk) for pk, schema in live_schools())
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_database_size(current_database())")
        metrics.DB_DATABASE_BYTES.set(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT n.nspname, COALESCE(SUM(pg_total_relation_size(c.oid)), 0)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'm', 'p') AND n.nspname = ANY(%s)
            GROUP BY n.nspname
            """,
            [list(schools)],
        )
        sizes = dict(cursor.fetchall())
    for schema, pk in schools.items():
        with context.for_school(pk):
            metrics.DB_SCHEMA_BYTES.set(sizes.get(schema, 0))


def folder_bytes(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def observe_media():
    """Bytes of each live school's uploaded files on local storage (daily).
    Logos on Cloudinary are not counted."""
    root = settings.MEDIA_ROOT
    if not root or not os.path.isdir(root):
        return
    for pk, schema in live_schools():
        with context.for_school(pk):
            metrics.MEDIA_BYTES.set(folder_bytes(os.path.join(root, schema)))


def record_daily_active(now=None):
    """Yesterday's distinct signed in users per school, stamped 23:59 of
    that day (daily, 00:10 UTC)."""
    now = now or timezone.now()
    day = now.astimezone(dt_timezone.utc).date() - timedelta(days=1)
    stamp = datetime.combine(day, dt_time(23, 59), tzinfo=dt_timezone.utc).timestamp()
    schools = list(live_schools())
    counts = buffer.run(
        lambda redis: [redis.pfcount(metrics.dau_key(pk, day)) for pk, _ in schools]
    )
    if counts is None:
        logger.warning("Telemetry Redis unreachable: daily actives for %s not recorded", day)
        return
    for (pk, _), count in zip(schools, counts):
        with context.for_school(pk):
            metrics.DAILY_ACTIVE.set(count, at=stamp)
