"""Starting and running a sync (spec 0005, *State transitions*).

Only one run is ``running`` per school: a start locks the connection row
(``nowait``) and checks for a running run inside that lock (AC-14). The
task fetches every enabled endpoint completely before applying anything, so
a partial fetch can never look like deletions (AC-6).

Until a person approves, every sync is a preview (AC-4): it runs the same
reconcile inside one transaction and rolls it back, so its counts are what
a real sync would do and nothing it computed stays.

Records the portal stopped sending are deleted after every type applied,
behind the mass delete and reference guards in ``deletes`` (AC-10, AC-11).
"""
import logging
import uuid
from contextlib import nullcontext
from datetime import timedelta

from django.db import DatabaseError, transaction
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context

from ems.api.exceptions import Conflict
from ems.models import BackgroundJob, PortalConnection, SyncIssue, SyncRun, SystemSettings
from ems.portal_sync import deletes
from ems.portal_sync.combined import reconcile_combined
from ems.portal_sync import fields as ordo_fields
from ems.portal_sync.fetch import Budget, FetchError, fetch_rows
from ems.portal_sync.reconcile import Results, reconcile_students
from ems.portal_sync.structure import (
    reconcile_classes,
    reconcile_courses,
    reconcile_departments,
)
from tenancy.models import School
from tenancy.observability.capture import job_failed

logger = logging.getLogger(__name__)

_CRASHED = "The sync stopped on an unexpected error."

# Runs (and their issues) are kept this long.
KEEP_DAYS = 90

# Highest first: a run takes the most severe status any type reached.
_PRIORITY = ("failed", "held", "partial", "succeeded")

# Apply order (spec 0005, *Matching*): each kind may name records of the
# kinds before it. Faculties apply inside the departments pass. A combined
# endpoint is a connection's only one and runs every type in that order.
_APPLY = (
    ("combined", reconcile_combined),
    ("departments", reconcile_departments),
    ("classes", reconcile_classes),
    ("courses", reconcile_courses),
    ("students", reconcile_students),
)


def readiness_problem(connection):
    """Why ``connection`` can't sync, or None."""
    endpoints = list(connection.endpoints.filter(is_enabled=True))
    if connection.mode == "per_type" and not any(e.kind == "students" for e in endpoints):
        return "Add and enable a students endpoint first."
    if connection.mode == "combined" and not endpoints:
        return "Add and enable the combined endpoint first."
    for endpoint in endpoints:
        missing = ordo_fields.unmapped_required(endpoint.kind, endpoint.field_mapping)
        if missing:
            return (
                f"Map every required field of the {endpoint.kind} endpoint: "
                f"{', '.join(missing)}."
            )
    return None


def timetable_exists():
    """Sync follows the upload lock: nothing syncs while a timetable
    exists (AC-13), as ``UploadsUnlocked`` stops CSV uploads."""
    settings_row = SystemSettings.objects.first()
    return bool(settings_row and settings_row.has_timetable)


def _fail_stale_runs():
    """A run whose job was killed (and so marked failed by
    ``fail_stale_jobs``) never wrote its own end. Close it, or it would
    block every later sync."""
    from ems.api.views.jobs import fail_stale_jobs

    fail_stale_jobs()
    SyncRun.objects.filter(
        status=SyncRun.Status.RUNNING, job__status="failed"
    ).update(
        status=SyncRun.Status.FAILED,
        finished_at=timezone.now(),
        error="Stopped without finishing: the worker was killed or restarted.",
    )


def reset_approval(connection):
    """The config a preview showed has changed: the next sync is a preview
    again, and any preview waiting for approval is out of date (AC-4).
    Saving bumps ``updated_at``, which a preview still running checks."""
    connection.first_sync_approved_at = None
    connection.save(update_fields=["first_sync_approved_at", "updated_at"])
    SyncRun.objects.filter(
        connection=connection, status=SyncRun.Status.AWAITING_APPROVAL
    ).update(status=SyncRun.Status.SUPERSEDED)


def disconnect(connection):
    """Delete ``connection`` (AC-17), inside the caller's transaction and
    lock. Synced records lose their portal IDs, so they are Ordo owned and
    editable again; endpoints cascade; runs stay with ``connection`` null.
    A preview waiting for approval can never be approved now. Returns how
    many records of each type were released."""
    SyncRun.objects.filter(
        connection=connection, status=SyncRun.Status.AWAITING_APPROVAL
    ).update(status=SyncRun.Status.SUPERSEDED)
    released = {
        data_type: model.objects.filter(external_id__isnull=False).update(external_id=None)
        for data_type, model in deletes.MODELS.items()
    }
    connection.delete()
    return released


def start(connection_id, *, user, trigger, approving=None, allow_mass_delete=()):
    """Create a running ``SyncRun`` and queue its task. 409 when another
    run is in progress. Call after the readiness and lock checks.

    The run is a preview while the connection has no approval, unless it
    is the run ``approving`` (a preview's pk) starts; that preview becomes
    ``approved``, and the run keeps the mass deletes it was allowed. Older
    previews waiting for approval are superseded. ``allow_mass_delete``
    lets the listed types past the mass delete hold (AC-11)."""
    from ems.tasks import portal_sync_task

    _fail_stale_runs()
    job_id = str(uuid.uuid4())
    with transaction.atomic():
        try:
            connection = PortalConnection.objects.select_for_update(nowait=True).get(
                pk=connection_id
            )
        except DatabaseError:
            raise Conflict("A sync is starting already. Try again in a moment.")
        if SyncRun.objects.filter(status=SyncRun.Status.RUNNING).exists():
            raise Conflict("A sync is already running. Wait for it to finish.")
        waiting = SyncRun.objects.filter(
            connection=connection, status=SyncRun.Status.AWAITING_APPROVAL
        )
        if approving is not None:
            preview = waiting.filter(pk=approving).first()
            if preview is None:
                raise Conflict("This preview isn't waiting for approval any more.")
            allow_mass_delete = preview.allow_mass_delete
            waiting.filter(pk=approving).update(status=SyncRun.Status.APPROVED)
            connection.first_sync_approved_at = timezone.now()
            connection.save(update_fields=["first_sync_approved_at"])
        waiting.exclude(pk=approving).update(status=SyncRun.Status.SUPERSEDED)
        job = BackgroundJob.objects.create(
            job_id=job_id,
            job_type="portal_sync",
            status="pending",
            created_by=user,
            params={},
        )
        run = SyncRun.objects.create(
            connection=connection,
            trigger=trigger,
            is_preview=connection.first_sync_approved_at is None,
            allow_mass_delete=sorted(allow_mass_delete),
            status=SyncRun.Status.RUNNING,
            started_by=user,
            job=job,
        )
        transaction.on_commit(lambda: portal_sync_task.delay(job_id, run.pk))
    return run


def _report(job, pct):
    job.progress = pct
    job.save(update_fields=["progress"])


def _finish(run, job, status, *, error="", exc=None):
    """Write the run's end. A failed run is recorded through spec 0004: a
    portal that failed to answer is ``expected`` (the school's to fix), a
    crash is an error. Messages carry the portal host at most, and the
    capture scrubs them again (AC-16)."""
    now = timezone.now()
    run.status = status
    run.error = error
    run.finished_at = now
    run.save(update_fields=["status", "error", "finished_at", "counts"])
    job.status = "failed" if status == SyncRun.Status.FAILED else "success"
    job.progress = job.total_steps
    job.completed_at = now
    job.error_message = error
    # References only: the run's id and outcome, never row content (AC-16).
    job.result_data = {"run_id": run.pk, "status": status}
    if status == SyncRun.Status.FAILED:
        job_failed(job, exc, expected=exc is None or isinstance(exc, FetchError))
    job.save()


def execute(run_id, job_id):
    """The body of ``portal_sync_task``."""
    job = BackgroundJob.objects.get(job_id=job_id)
    run = SyncRun.objects.select_related("connection").get(pk=run_id)
    job.status = "running"
    job.save(update_fields=["status"])
    connection = run.connection
    if connection is None:
        _finish(run, job, SyncRun.Status.FAILED, error="The portal connection was removed.")
        return run

    kinds = [kind for kind, _ in _APPLY]
    endpoints = sorted(
        (e for e in connection.endpoints.filter(is_enabled=True) if e.kind in kinds),
        key=lambda e: kinds.index(e.kind),
    )
    budget = Budget()
    fetched = {}
    endpoint = None
    try:
        for i, endpoint in enumerate(endpoints):
            fetched[endpoint.kind] = (fetch_rows(endpoint, connection, budget=budget), endpoint)
            _report(job, 10 + int(50 * (i + 1) / max(len(endpoints), 1)))
    except FetchError as exc:
        SyncIssue.objects.create(
            run=run,
            data_type=endpoint.kind,
            kind=SyncIssue.Kind.FETCH_ERROR,
            message=exc.message,
        )
        _finish(run, job, SyncRun.Status.FAILED, error=exc.message, exc=exc)
        return run
    except Exception as exc:
        logger.exception("Portal sync fetch crashed (run %s).", run.pk)
        _finish(run, job, SyncRun.Status.FAILED, error=_CRASHED, exc=exc)
        return run

    results = Results()
    try:
        # A real run keeps each type's own transaction, so a failed type
        # doesn't undo those before it. A preview wraps them all and rolls
        # back. Its progress saves roll back too; ``_finish`` writes the end.
        with transaction.atomic() if run.is_preview else nullcontext():
            plans = deletes.plan(fetched)
            deletes.hold_mass_deletes(plans, run.allow_mass_delete, results)
            for kind, reconcile in _APPLY:
                if kind in fetched:
                    rows, endpoint = fetched.pop(kind)
                    # These passes settle holds themselves: departments'
                    # faculties, or a combined endpoint's other types, may apply.
                    if kind in ("departments", "combined") or kind not in results.held:
                        reconcile(rows, endpoint.field_mapping, results)
                    _report(job, job.progress + int(40 / len(endpoints)))
            deletes.apply(plans, results)
            if run.is_preview:
                transaction.set_rollback(True)
    except Exception as exc:
        logger.exception("Portal sync apply crashed (run %s).", run.pk)
        _finish(run, job, SyncRun.Status.FAILED, error=_CRASHED, exc=exc)
        return run

    for result in results.values():
        SyncIssue.objects.bulk_create(
            [_attach(issue, run) for issue in result.issues], batch_size=500
        )
    run.counts = {
        r.data_type: r.as_counts(examples=run.is_preview) for r in results.values()
    }
    if run.is_preview:
        # The config changed while this preview ran: what it shows is stale.
        changed = PortalConnection.objects.filter(
            pk=connection.pk, updated_at__gt=run.started_at
        ).exists()
        status = SyncRun.Status.SUPERSEDED if changed else SyncRun.Status.AWAITING_APPROVAL
        _finish(run, job, status)
    else:
        _finish(run, job, _status(results.values()))
    return run


def _attach(issue, run):
    issue.run = run
    return issue


def _status(results):
    statuses = set()
    for r in results:
        if r.status == "held":
            statuses.add("held")
        elif r.issues:
            statuses.add("partial")
        else:
            statuses.add("succeeded")
    for status in _PRIORITY:
        if status in statuses:
            return status
    return "succeeded"


def prune(now=None):
    """Delete runs older than 90 days in every school (beat, daily, from the
    public schema). A running run is never pruned. One school failing never
    stops the rest."""
    cutoff = (now or timezone.now()) - timedelta(days=KEEP_DAYS)
    for school in School.objects.filter(status__in=School.LIVE_STATUSES).exclude(
        schema_name=get_public_schema_name()
    ):
        try:
            with schema_context(school.schema_name):
                SyncRun.objects.filter(started_at__lt=cutoff).exclude(
                    status=SyncRun.Status.RUNNING
                ).delete()
        except Exception:
            logger.exception("Pruning portal sync runs of %s failed", school.schema_name)
