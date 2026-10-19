import os
import threading

from celery.schedules import crontab
from celery.signals import task_postrun, task_prerun
from tenant_schemas_celery.app import CeleryApp

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

# Every task runs in the schema that was active when it was queued: the
# library stamps it into a message header and switches to it before the task
# body runs (spec 0003, child 0001). Beat queues from the public schema.
app = CeleryApp('core')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# Additional configuration for better task tracking
app.conf.broker_connection_retry_on_startup = True
app.conf.task_track_started = True
app.conf.task_send_sent_event = True

# Platform beat tasks, times in UTC. Each runs in the public schema and enters
# school schemas itself with ``schema_context``. Entries land with their tasks
# in later build slices: tenancy.tasks.snapshot_usage (daily 01:00),
# close_expired_support_sessions (every 10 min), prune_usage_snapshots
# (daily 03:00).
app.conf.beat_schedule = {
    "fail-stale-provisioning": {
        "task": "tenancy.tasks.fail_stale_provisioning",
        "schedule": 10 * 60,
    },
    "close-expired-support-sessions": {
        "task": "tenancy.tasks.close_expired_support_sessions",
        "schedule": 10 * 60,
    },
    "snapshot-usage": {
        "task": "tenancy.tasks.snapshot_usage",
        "schedule": crontab(hour=1, minute=0),
    },
    "purge-schools": {
        "task": "tenancy.tasks.purge_schools",
        "schedule": crontab(hour=2, minute=0),
    },
    "index-staff": {
        "task": "tenancy.tasks.index_staff",
        "schedule": crontab(hour=1, minute=30),
    },
    "alert-renewals": {
        "task": "tenancy.tasks.alert_renewals",
        "schedule": crontab(hour=7, minute=0),
    },
    # Portal sync (spec 0005).
    "prune-sync-runs": {
        "task": "ems.tasks.prune_sync_runs",
        "schedule": crontab(hour=4, minute=0),
    },
    # Error observability (spec 0004).
    "observe-health": {
        "task": "tenancy.observability.tasks.observe_health",
        "schedule": 5 * 60,
    },
    "prune-observability": {
        "task": "tenancy.observability.tasks.prune_observability",
        "schedule": crontab(hour=3, minute=30),
    },
}


# Zones of the tasks running in this thread, innermost last. A task can run
# another inline with ``.apply()`` (allocation_all does), and the outer one
# must get its zone back when the inner one ends.
_task_zones = threading.local()


# Connected after the library's own prerun handler (imported above), so the
# schema has already been switched when this runs.
@task_prerun.connect(dispatch_uid="ordo_activate_school_timezone")
def activate_school_timezone(**kwargs):
    from django.db import connection
    from django.utils import timezone
    from django_tenants.utils import get_public_schema_name

    zone = None
    if connection.schema_name != get_public_schema_name():
        # ``schema_context`` leaves a stand in tenant with no fields on it.
        zone = getattr(connection.tenant, "timezone", None)
        if zone is None:
            from tenancy.models import School

            zone = (
                School.objects.filter(schema_name=connection.schema_name)
                .values_list("timezone", flat=True)
                .first()
            )
    _task_zones.stack = [*getattr(_task_zones, "stack", []), zone]
    if zone:
        timezone.activate(zone)
    else:
        timezone.deactivate()


@task_postrun.connect(dispatch_uid="ordo_deactivate_school_timezone")
def deactivate_school_timezone(**kwargs):
    from django.utils import timezone

    stack = getattr(_task_zones, "stack", [])[:-1]
    _task_zones.stack = stack
    # Worker processes run task after task: a zone must never carry over.
    if stack and stack[-1]:
        timezone.activate(stack[-1])
    else:
        timezone.deactivate()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
