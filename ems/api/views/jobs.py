import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ems.api.exceptions import Conflict
from ems.api.permissions import IsJobOwnerOrAdmin, IsSuperAdmin
from ems.api.serializers.job import BackgroundJobSerializer
from ems.api.views.constraints import get_or_create_constraints
from ems.api.views.system import _get_or_create_settings
from ems.readiness import blocking_message, check as check_readiness
from ems.seating_rules import RELAXED, STRICT
from tenancy.observability.capture import job_failed
from ems.utils import (
    current_constraints,
    hall_fill_cap,
    clear_slot_allocation,
    clear_slot_distribution,
    is_valid_position,
    slot_is_distributed,
    slot_relaxed_course_id,
)
from ems.models import (
    BackgroundJob,
    Class,
    Course,
    Department,
    Distribution,
    Faculty,
    Hall,
    SeatArrangement,
    SystemSettings,
    TimeTable,
)
from tenancy import limits
from tenancy.permissions import requires_feature


def _assert_ready(stage: str, slots=None) -> None:
    """409 before a job is queued when the stage's readiness check fails.
    The task repeats the same check (``ems.readiness``) as a safety net."""
    report = check_readiness(stage, slots)
    if not report["ready"]:
        raise Conflict(blocking_message(report))


def _assert_generation_allowed() -> None:
    """Block generation until session/semester/constraints/excluded-days are set."""
    settings_row = _get_or_create_settings()
    if not (settings_row.session or "").strip() or not (
        settings_row.semester or ""
    ).strip():
        raise ValidationError(
            {
                "detail": (
                    "Academic session and semester must be set in System Settings "
                    "before generation."
                )
            }
        )

    constraints = get_or_create_constraints()
    if constraints.configured_at is None:
        raise ValidationError(
            {
                "detail": (
                    "Generation constraints have not been initialized. "
                    "Open Admin → Constraints and save once before generating."
                )
            }
        )
    if not constraints.excluded_weekdays:
        raise ValidationError(
            {
                "detail": (
                    "Configure at least one excluded weekday in Admin → "
                    "Constraints before generating."
                )
            }
        )

    # Every department must belong to a faculty so CBE auto-split can
    # bucket classes by faculty.
    deps_without_faculty = list(
        Department.objects.filter(faculty__isnull=True)
        .values_list("name", flat=True)
        .order_by("name")
    )
    if deps_without_faculty:
        sample = ", ".join(deps_without_faculty[:5])
        suffix = "…" if len(deps_without_faculty) > 5 else ""
        raise ValidationError(
            {
                "detail": (
                    "These departments have no faculty assigned: "
                    f"{sample}{suffix}. "
                    "Assign every department to a faculty in Admin → Faculties "
                    "before generating."
                )
            }
        )

    # Every faculty must be mapped to a CBE group in the constraints.
    faculty_groups = constraints.cbe_faculty_groups or {}
    unmapped = list(
        Faculty.objects.exclude(slug__in=faculty_groups.keys())
        .values_list("name", flat=True)
        .order_by("name")
    )
    if unmapped:
        sample = ", ".join(unmapped[:5])
        suffix = "…" if len(unmapped) > 5 else ""
        raise ValidationError(
            {
                "detail": (
                    "These faculties have no CBE group assigned: "
                    f"{sample}{suffix}. "
                    "Set a group for each faculty in Admin → Constraints."
                )
            }
        )


def _stale_cutoff():
    """Jobs started before this cannot still be running: Celery's hard time
    limit kills any task older than that."""
    return timezone.now() - timedelta(seconds=settings.CELERY_TASK_TIME_LIMIT)


def fail_stale_jobs() -> int:
    """Mark failed every job still ``pending`` or ``running`` past the hard
    time limit. A killed worker (time limit, deploy, crash) never writes a
    final status, which left jobs "running" forever: undeletable, not
    retryable, and in the way of every new run. Returns how many changed."""
    stale = BackgroundJob.objects.filter(
        status__in=("pending", "running"), started_at__lt=_stale_cutoff()
    )
    stale_ids = list(stale.values_list("pk", flat=True))
    if not stale_ids:
        return 0
    stale = BackgroundJob.objects.filter(pk__in=stale_ids)
    now = timezone.now()
    never_started = stale.filter(status="pending").update(
        status="failed",
        completed_at=now,
        error_message=(
            "Never started: no worker picked the job up. Check that the "
            "Celery worker is running, then retry."
        ),
    )
    killed = stale.filter(status="running").update(
        status="failed",
        completed_at=now,
        error_message=(
            "Stopped without finishing: the worker was killed or restarted, "
            "or the job ran past its time limit. Retry to run it again."
        ),
    )
    # A bulk update sends no post_save, so each one is recorded here (AC-3).
    for job in BackgroundJob.objects.filter(pk__in=stale_ids, status="failed"):
        job_failed(job)
    return never_started + killed


_JOB_LABELS = {
    "timetable": "timetable",
    "distribution": "distribution",
    "distribution_all": "distribution",
    "allocation": "seat allocation",
    "allocation_all": "seat allocation",
    "portal_sync": "portal sync",
}


def _assert_no_active_job(action: str) -> None:
    """409 while any generation job may still be writing. Generation stages
    replace each other's rows (a timetable run clears every slot), so only
    one may run at a time. Stale jobs are failed first, so a killed worker
    never blocks."""
    fail_stale_jobs()
    active = (
        BackgroundJob.objects.filter(status__in=("pending", "running"))
        .order_by("started_at")
        .first()
    )
    if active:
        raise Conflict(
            f"A {_JOB_LABELS.get(active.job_type, active.job_type)} job is still "
            f"{active.status}. Wait for it to finish before {action}."
        )


class JobViewSet(viewsets.ReadOnlyModelViewSet):
    """List and inspect background jobs.

    - Admins see every job; everyone else only their own.
    - Includes a custom ``destroy`` (admin only) and ``retry`` action.
    """

    serializer_class = BackgroundJobSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "job_id"
    lookup_value_regex = "[\\w-]+"

    def get_queryset(self):
        # Show a killed job as failed (so it can be retried or deleted), not
        # as running forever.
        fail_stale_jobs()
        qs = BackgroundJob.objects.all().select_related("created_by")
        params = self.request.query_params
        if status_filter := params.get("status"):
            qs = qs.filter(status=status_filter)
        if job_type := params.get("job_type"):
            qs = qs.filter(job_type=job_type)
        if not self.request.user.is_staff:
            qs = qs.filter(created_by=self.request.user)
        return qs

    def get_permissions(self):
        if self.action in ("destroy", "retry"):
            return [IsSuperAdmin()]
        return [IsAuthenticated()]

    def check_object_permissions(self, request, obj):
        super().check_object_permissions(request, obj)
        if self.action == "retrieve" and not IsJobOwnerOrAdmin().has_object_permission(
            request, self, obj
        ):
            self.permission_denied(request, message="Not your job.")

    @action(detail=True, methods=["delete"])
    def destroy_job(self, request, job_id=None):
        return self.destroy(request, job_id=job_id)

    def destroy(self, request, *args, **kwargs):
        job = self.get_object()
        if job.status == "running":
            raise Conflict("Refusing to delete a running job.")
        job.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def retry(self, request, job_id=None):
        job = self.get_object()
        if job.status != "failed":
            raise Conflict("Only failed jobs can be retried.")

        if _TASK_MAP_SOURCES.get(job.job_type) is None:
            raise Conflict(f"Jobs of type '{job.job_type}' cannot be retried.")
        # A retry starts a job without its gated endpoint, so check here too.
        limits.assert_feature_for_job(job.job_type)
        # Re-dispatch the matching Celery task with the original params.
        new_id = _start_job(
            request.user, job.job_type, job.params or {}, "retrying a job"
        )
        return Response({"job_id": new_id}, status=status.HTTP_202_ACCEPTED)


def _task_args(job_type, job_id, user_id, params):
    if job_type == "timetable":
        return [
            job_id,
            user_id,
            params.get("start_date"),
            params.get("end_date"),
        ]
    if job_type in ("distribution_all", "allocation_all"):
        return [job_id, user_id]
    return [
        job_id,
        user_id,
        params.get("date"),
        params.get("period"),
    ]


# Lazy lookup so import order with celery is benign.
def _import_task(name):
    from ems import tasks

    return getattr(tasks, name)


class _TaskRegistry(dict):
    def __getitem__(self, key):
        return self.get(key)


_TASK_MAP = _TaskRegistry()


_TASK_MAP_SOURCES = {
    "timetable": "generate_timetable_task",
    "distribution": "generate_distribution_task",
    "allocation": "generate_allocation_task",
    "distribution_all": "generate_distribution_all_task",
    "allocation_all": "generate_allocation_all_task",
}


def _populate_task_map():
    if _TASK_MAP:
        return
    for job_type, name in _TASK_MAP_SOURCES.items():
        _TASK_MAP[job_type] = _import_task(name)


def _start_job(user, job_type, params, action):
    """Create a job and queue its task, unless another generation job is
    active. The settings row is locked while checking, so two requests at
    once cannot both start a job."""
    _populate_task_map()
    job_id = str(uuid.uuid4())
    with transaction.atomic():
        lock_pk = _get_or_create_settings().pk
        SystemSettings.objects.select_for_update().get(pk=lock_pk)
        _assert_no_active_job(action)
        BackgroundJob.objects.create(
            job_id=job_id,
            job_type=job_type,
            status="pending",
            created_by=user,
            params=params,
        )
        task = _TASK_MAP[job_type]
        transaction.on_commit(
            lambda: task.apply_async(
                args=_task_args(job_type, job_id, user.id, params)
            )
        )
    return job_id


# Generate endpoints --------------------------------------------------------


class _BaseGenerateView(APIView):
    permission_classes = [IsSuperAdmin]
    job_type: str = ""

    def _create_job(self, user, params):
        return _start_job(user, self.job_type, params, "starting another")


class GenerateTimetableView(_BaseGenerateView):
    job_type = "timetable"

    def post(self, request):
        _assert_generation_allowed()
        start = request.data.get("start_date")
        end = request.data.get("end_date")
        if not start or not end:
            raise ValidationError(
                {"detail": "start_date and end_date are required."}
            )

        for resource_qs, message in (
            (Course.objects.exists(), "Cannot generate: no courses in the system."),
            (Class.objects.exists(), "Cannot generate: no classes in the system."),
            (Hall.objects.open().exists(), "Cannot generate: no open halls in the system."),
        ):
            if not resource_qs:
                raise ValidationError({"detail": message})

        # Inactive classes are not sitting, so they never block a run.
        active_classes = Class.objects.active()
        missing = active_classes.filter(courses__isnull=True).select_related(
            "department"
        )
        if missing.exists():
            sample = ", ".join(
                f"{c.department.slug} {c.name}" for c in missing[:5]
            )
            extra = "…" if missing.count() > 5 else ""
            raise ValidationError(
                {
                    "detail": (
                        "Some classes have no courses assigned: "
                        f"{sample}{extra}. Upload class courses first."
                    )
                }
            )

        try:
            start_d = datetime.strptime(start, "%Y-%m-%d").date()
            end_d = datetime.strptime(end, "%Y-%m-%d").date()
        except ValueError:
            raise ValidationError(
                {"detail": "Dates must be in YYYY-MM-DD format."}
            )
        if start_d > end_d:
            raise ValidationError(
                {"detail": "End date must be on or after start date."}
            )

        # Use admin-configured excluded weekdays
        excluded = set(get_or_create_constraints().excluded_weekdays or [])
        days_available = 0
        cursor = start_d
        while cursor <= end_d:
            if cursor.weekday() not in excluded:
                days_available += 1
            cursor += timedelta(days=1)
        max_courses = max(
            (cls.courses.count() for cls in active_classes.prefetch_related("courses")),
            default=0,
        )
        if days_available < max_courses:
            raise ValidationError(
                {
                    "detail": (
                        f"Selected range gives {days_available} usable days, "
                        f"but at least {max_courses} are needed."
                    )
                }
            )

        _assert_ready("timetable")

        job_id = self._create_job(
            request.user, {"start_date": start, "end_date": end}
        )
        return Response({"job_id": job_id}, status=status.HTTP_202_ACCEPTED)


class GenerateDistributionView(_BaseGenerateView):
    job_type = "distribution"

    def post(self, request):
        _assert_generation_allowed()
        date = request.data.get("date")
        period = request.data.get("period")
        if not date or not period:
            raise ValidationError({"detail": "date and period are required."})
        if slot_is_distributed(date, period):
            raise Conflict(
                f"A distribution for {date} ({period}) already exists. "
                "Clear the slot before regenerating."
            )
        _assert_ready("distribution", [(date, period)])
        job_id = self._create_job(request.user, {"date": date, "period": period})
        return Response({"job_id": job_id}, status=status.HTTP_202_ACCEPTED)


class GenerateAllocationView(_BaseGenerateView):
    permission_classes = [IsSuperAdmin, requires_feature("seat_allocation")]
    job_type = "allocation"

    def post(self, request):
        _assert_generation_allowed()
        date = request.data.get("date")
        period = request.data.get("period")
        if not date or not period:
            raise ValidationError({"detail": "date and period are required."})
        if not slot_is_distributed(date, period):
            raise ValidationError(
                {
                    "detail": (
                        f"No distribution exists for {date} ({period}). "
                        "Generate the distribution first."
                    )
                }
            )
        if SeatArrangement.objects.filter(date=date, period=period).exists():
            raise Conflict(
                f"Seat allocation for {date} ({period}) already exists."
            )
        _assert_ready("allocation", [(date, period)])
        job_id = self._create_job(request.user, {"date": date, "period": period})
        return Response({"job_id": job_id}, status=status.HTTP_202_ACCEPTED)


class GenerateDistributionAllView(_BaseGenerateView):
    """Distribute every (date, period) slot in the timetable in one go.
    Slots that already have a distribution plan are skipped."""

    job_type = "distribution_all"

    def post(self, request):
        _assert_generation_allowed()
        # Lightweight gate: timetable must exist somewhere.
        if not TimeTable.objects.exists():
            raise ValidationError(
                {"detail": "No timetable found. Generate the timetable first."}
            )
        # Every slot is checked before any is distributed.
        _assert_ready("distribution")
        job_id = self._create_job(request.user, {})
        return Response({"job_id": job_id}, status=status.HTTP_202_ACCEPTED)


class GenerateAllocationAllView(_BaseGenerateView):
    """Allocate every (date, period) slot that has a Distribution but no
    SeatArrangement yet."""

    permission_classes = [IsSuperAdmin, requires_feature("seat_allocation")]
    job_type = "allocation_all"

    def post(self, request):
        _assert_generation_allowed()
        if not Distribution.objects.exists():
            raise ValidationError(
                {"detail": "No distributions found. Generate distribution first."}
            )
        # Every slot is checked before anyone is seated.
        _assert_ready("allocation")
        job_id = self._create_job(request.user, {})
        return Response({"job_id": job_id}, status=status.HTTP_202_ACCEPTED)


def _slot_from(data):
    date = data.get("date")
    period = data.get("period")
    if not date or not period:
        raise ValidationError({"detail": "date and period are required."})
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise ValidationError({"detail": "date must be in YYYY-MM-DD format."})
    if period not in ("AM", "PM"):
        raise ValidationError({"detail": "period must be AM or PM."})
    return date, period




class ClearDistributionView(APIView):
    """POST /api/distribution/clear/ — body: {date, period}.

    Clears one slot's distribution and the seat allocation planned from it,
    so the slot can be distributed again."""

    permission_classes = [IsSuperAdmin]

    def post(self, request):
        date, period = _slot_from(request.data)
        _assert_no_active_job("clearing a slot")
        with transaction.atomic():
            clear_slot_distribution(date, period)
        return Response(
            {"detail": f"Distribution and seat allocation for {date} ({period}) cleared."}
        )


class ClearAllocationView(APIView):
    """POST /api/allocation/clear/ — body: {date, period}. Clears one slot's
    seat allocation; its distribution stays."""

    permission_classes = [IsSuperAdmin, requires_feature("seat_allocation")]

    def post(self, request):
        date, period = _slot_from(request.data)
        _assert_no_active_job("clearing a slot")
        clear_slot_allocation(date, period)
        return Response({"detail": f"Seat allocation for {date} ({period}) cleared."})


class ManualSeatAssignmentView(APIView):
    """POST /api/allocation/manual-assign/ — body: {seat_arrangement_id, seat_number}."""

    permission_classes = [IsSuperAdmin, requires_feature("seat_allocation")]

    def post(self, request):
        sa_id = request.data.get("seat_arrangement_id")
        seat_number = request.data.get("seat_number")
        if sa_id is None or seat_number is None:
            raise ValidationError(
                {"detail": "seat_arrangement_id and seat_number are required."}
            )
        try:
            sa = SeatArrangement.objects.get(pk=sa_id)
        except SeatArrangement.DoesNotExist as exc:
            raise ValidationError(
                {"detail": "Seat arrangement record not found."}
            ) from exc

        if sa.seat_number is not None:
            raise Conflict("This student is already placed.")

        try:
            seat = int(seat_number)
        except (TypeError, ValueError):
            raise ValidationError({"detail": "seat_number must be an integer."})
        max_seats = sa.hall.seat_count
        if seat < 1 or (max_seats and seat > max_seats):
            raise ValidationError(
                {"detail": f"Seat must be between 1 and {max_seats}."}
            )

        # The hall's seat map for the slot: seat number -> course id.
        seat_map = dict(
            SeatArrangement.objects.filter(
                date=sa.date,
                period=sa.period,
                hall=sa.hall,
                seat_number__isnull=False,
            ).values_list("seat_number", "course_id")
        )
        if seat in seat_map:
            raise Conflict(f"Seat {seat} is already occupied.")

        # The fill cap is a hard rule: no hall holds more than
        # pbe_hall_utilization of its seats, even by hand.
        constraints = current_constraints()
        cap = hall_fill_cap(sa.hall, constraints)
        if len(seat_map) >= cap:
            raise Conflict(
                f"{sa.hall.name} is at its fill cap of {cap} students "
                f"({float(constraints.pbe_hall_utilization):.0%} of its seats)."
            )

        # Same adjacency rule the allocator applies (spec 0002): 4-dir for
        # the slot's relaxed course, 8-dir for everyone else.
        rule = (
            RELAXED
            if sa.course_id == slot_relaxed_course_id(sa.date, sa.period)
            else STRICT
        )
        if not is_valid_position(
            seat, sa.course_id, seat_map, sa.hall.rows, sa.hall.columns, rule,
            mask=sa.hall.layout,
            seat_order=sa.hall.seat_order,
        ):
            raise Conflict(
                f"Seat {seat} is next to a student of the same course "
                f"({sa.course.code})."
            )

        sa.seat_number = seat
        sa.save(update_fields=["seat_number"])
        return Response({"detail": "Seat assigned."})
