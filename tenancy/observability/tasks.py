"""Observability beat tasks (spec 0004, spec 0006). All run in the public
schema. Spec 0006's run on the ``observability`` queue with instrumentation
suppressed, so they produce no spans of their own."""
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from opentelemetry.instrumentation.utils import suppress_instrumentation

from tenancy.models import (
    AlertIncident,
    AlertState,
    ErrorEvent,
    ErrorGroup,
    HealthBucket,
    MetricRollup,
    MetricSeries,
    SchoolHealth,
)
from tenancy.observability import alert_rules, alerts, flush, health, metric_store, partitions, samplers

logger = logging.getLogger("ordo.observability")

PRUNE_BATCH = 1000


@shared_task(ignore_result=True)
def observe_health():
    """Copy counters into buckets, recompute every school's status, and send
    what's due (beat, every 5 minutes; AC-12, AC-13, AC-17 to AC-19)."""
    if not settings.OBSERVABILITY_ENABLED:
        return
    turned_failing = health.observe()
    if turned_failing is None:
        logger.warning("Redis unreachable: health left as it was")
        return
    alerts.sweep(turned_failing)


def _delete_in_batches(queryset):
    model = queryset.model
    deleted = 0
    while True:
        ids = list(queryset.values_list("pk", flat=True)[:PRUNE_BATCH])
        if not ids:
            return deleted
        deleted += model.objects.filter(pk__in=ids).delete()[0]


@shared_task(ignore_result=True)
def prune_observability():
    """Delete old events, buckets and closed groups (beat, daily 03:30 UTC;
    AC-20). Open groups are never pruned."""
    now = timezone.now()
    _delete_in_batches(
        ErrorEvent.objects.filter(occurred_at__lt=now - timedelta(days=settings.OBS_EVENT_KEEP_DAYS))
    )
    _delete_in_batches(
        HealthBucket.objects.filter(hour__lt=now - timedelta(days=settings.OBS_BUCKET_KEEP_DAYS))
    )
    stale_groups = ErrorGroup.objects.filter(
        status__in=(ErrorGroup.Status.RESOLVED, ErrorGroup.Status.IGNORED),
        last_seen__lt=now - timedelta(days=settings.OBS_GROUP_KEEP_DAYS),
    )
    # Their events first, in batches, so no one delete cascades through
    # thousands of rows.
    _delete_in_batches(ErrorEvent.objects.filter(group__in=stale_groups))
    _delete_in_batches(stale_groups)
    # Spec 0006: hourly metric rollups past their retention.
    _delete_in_batches(
        MetricRollup.objects.filter(hour__lt=now - timedelta(days=settings.OBS_ROLLUP_KEEP_DAYS))
    )


def purge_school(school):
    """Every observability row of a school being purged (spec 0004 AC-20,
    spec 0006 AC-25)."""
    _delete_in_batches(ErrorEvent.objects.filter(school=school))
    _delete_in_batches(ErrorEvent.objects.filter(group__school=school))
    _delete_in_batches(ErrorGroup.objects.filter(school=school))
    HealthBucket.objects.filter(school=school).delete()
    SchoolHealth.objects.filter(school=school).delete()
    # Spec 0006 (AC-25): traces, spans, logs and minute points first (the
    # points are found through the series), then the series and rollups.
    # Charts fixed to the school stay, showing it as purged.
    partitions.delete_school(school.pk)
    _delete_in_batches(MetricRollup.objects.filter(series__school=school))
    _delete_in_batches(MetricSeries.objects.filter(school=school))
    _delete_in_batches(AlertIncident.objects.filter(school=school))
    AlertState.objects.filter(school=school).delete()


@shared_task(ignore_result=True)
def flush_telemetry():
    """Buffered telemetry into Postgres (beat, every 10 seconds; AC-5)."""
    if not settings.OBSERVABILITY_ENABLED:
        return
    with suppress_instrumentation():
        flush.flush()


@shared_task(ignore_result=True)
def maintain_partitions():
    """Create partitions ahead and drop expired ones (beat, daily 00:15 UTC,
    and after ``migrate_platform``; AC-6)."""
    with suppress_instrumentation():
        dropped = partitions.maintain()
    if dropped:
        logger.info("Dropped expired telemetry partitions: %s", ", ".join(dropped))


@shared_task(ignore_result=True)
def rollup_metrics():
    """Minute points into hourly rollups (beat, minute 5 of every hour;
    AC-11)."""
    if not settings.OBSERVABILITY_ENABLED:
        return
    with suppress_instrumentation():
        metric_store.rollup_due(settings.OBSERVABILITY_DB_ALIAS)


def _sample(sampler):
    if not (settings.OBSERVABILITY_ENABLED and settings.OBSERVABILITY_METRICS_ENABLED):
        return
    with suppress_instrumentation():
        sampler()


@shared_task(ignore_result=True)
def observe_queues():
    """Celery queue depths (beat, every minute; AC-8)."""
    _sample(samplers.observe_queues)


@shared_task(ignore_result=True)
def observe_host():
    """Host CPU, memory and disk (beat, every minute; AC-8)."""
    _sample(samplers.observe_host)


@shared_task(ignore_result=True)
def observe_db_size():
    """Database and school schema sizes (beat, hourly; AC-8)."""
    _sample(samplers.observe_db_size)


@shared_task(ignore_result=True)
def observe_media():
    """Media bytes per school (beat, daily 02:00 UTC; AC-8)."""
    _sample(samplers.observe_media)


@shared_task(ignore_result=True)
def record_daily_active():
    """Yesterday's daily active users per school (beat, daily 00:10 UTC;
    AC-9)."""
    _sample(samplers.record_daily_active)


@shared_task(ignore_result=True)
def evaluate_alert_rules():
    """Threshold rules and the flush stalled check (beat, every minute;
    AC-18, AC-19)."""
    if not settings.OBSERVABILITY_ENABLED:
        return
    with suppress_instrumentation():
        alert_rules.evaluate()
