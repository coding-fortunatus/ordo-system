"""Read endpoints for timetable, distribution, allocation, hall allocation,
and distribution statistics. The generate/manual-assign endpoints live in
``jobs.py``; these are the corresponding GETs."""

from datetime import datetime

from django.db.models import Count, Prefetch, Q
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ems import publication, readiness
from ems.api.permissions import IsSuperAdmin
from ems.models import (
    Class,
    Course,
    Distribution,
    GenerationConstraints,
    Hall,
    SeatArrangement,
    TimeTable,
)
from ems import seating_rules
from ems.seating_rules import RELAXED, STRICT
from ems.utils import (
    cbe_min_days,
    cbe_sections,
    get_courses,
    hall_effective_capacity,
    slot_relaxed_course_id,
)
from tenancy.permissions import requires_feature


class ReadinessView(APIView):
    """GET /api/readiness/?stage=timetable|distribution|allocation
    [&date=YYYY-MM-DD&period=AM|PM]

    What would stop the stage's generate run. No date/period = every slot
    (the generate all case). Same checks the generate views and tasks run.
    """

    permission_classes = [IsSuperAdmin]

    def get(self, request):
        stage = request.query_params.get("stage")
        if stage not in readiness.STAGES:
            raise ValidationError(
                {"detail": f"stage must be one of: {', '.join(readiness.STAGES)}."}
            )
        date = request.query_params.get("date")
        period = request.query_params.get("period")
        if bool(date) != bool(period):
            raise ValidationError(
                {"detail": "Pass date and period together, or neither."}
            )
        slots = None
        if date:
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                raise ValidationError({"detail": "date must be in YYYY-MM-DD format."})
            if period not in ("AM", "PM"):
                raise ValidationError({"detail": "period must be AM or PM."})
            slots = [(date, period)]
        return Response(readiness.check(stage, slots))


class TimetableListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        date = request.query_params.get("date")
        period = request.query_params.get("period")
        qs = TimeTable.objects.all().select_related("course", "class_obj__department")
        published = publication.is_published()
        if not (request.user.is_staff or published):
            # A draft: department officers see nothing until it is published.
            qs = qs.none()
        if date:
            qs = qs.filter(date=date)
        if period:
            qs = qs.filter(period=period)
        if not request.user.is_staff and request.user.department_id:
            qs = qs.filter(class_obj__department_id=request.user.department_id)
        rows = [
            {
                "id": t.id,
                "date": str(t.date),
                "period": t.period,
                "seating_rule": t.seating_rule,
                "course": {"id": t.course_id, "code": t.course.code, "name": t.course.name},
                "class": {
                    "id": t.class_obj_id,
                    "name": t.class_obj.name,
                    "department": {
                        "id": t.class_obj.department_id,
                        "name": t.class_obj.department.name,
                        "slug": t.class_obj.department.slug,
                    },
                },
            }
            for t in qs.order_by("date", "period", "class_obj__department__name", "class_obj__name")
        ]
        return Response(
            {
                "generated": TimeTable.objects.exists(),
                "published": published,
                "results": rows,
            }
        )


class TimetableDatesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = TimeTable.objects.all()
        if not publication.visible_to(request.user):
            qs = qs.none()
        if not request.user.is_staff and request.user.department_id:
            qs = qs.filter(class_obj__department_id=request.user.department_id)
        dates = list(
            qs.values_list("date", flat=True).distinct().order_by("date")
        )
        return Response({"dates": [str(d) for d in dates]})


class TimetableEstimateView(APIView):
    """Estimate the minimum exam-window span needed to schedule every class
    without conflicts, given the current course catalog and constraints.

    Three binding constraints:

    1. Per-class load — a class sits at most one exam a day, in either
       period, so the busiest class needs a day per course it takes.

    2. Hall seat throughput — total seat demand across all PBE courses must
       fit into (days × seats-per-period × utilization). Independent for AM
       and PM. When seat demand dominates, this is what causes most skips.

    3. CBE capacity — a CBE course above the full-day threshold takes a day
       of its own, and smaller CBE papers share days up to the daily cap
       (``cbe_min_days``, the generator's rules).

    The recommended day count is the max of the three, plus a small buffer
    for conflict resolution.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        constraints = GenerationConstraints.objects.first()
        excluded_weekdays = set(
            (constraints.excluded_weekdays if constraints else None) or [6]
        )
        overrides_raw = (
            constraints.class_period_overrides if constraints else None
        ) or {}
        overrides = {
            (k or "").strip().lower(): (v or "").upper()
            for k, v in overrides_raw.items()
        }
        utilization = (
            float(constraints.pbe_hall_utilization) if constraints else 0.9
        )
        pattern = (
            constraints.seat_pattern if constraints else "checkerboard"
        )

        # ─── Per-class load (1 exam per class per day) ──────────────────
        worst_am = worst_pm = worst_total = 0
        class_count = 0
        for cls in Class.objects.active().prefetch_related("courses").only("id", "name"):
            class_count += 1
            am = pm = 0
            key = (cls.name or "").strip().lower()
            default_period = overrides.get(key, "AM")
            for course in cls.courses.all():
                if course.exam_type == "CBE":
                    am += 1
                elif default_period == "PM":
                    pm += 1
                else:
                    am += 1
            worst_am = max(worst_am, am)
            worst_pm = max(worst_pm, pm)
            # The generator gives a class one exam a day, AM or PM, so its
            # AM and PM papers all need days of their own.
            worst_total = max(worst_total, am + pm)

        # ─── Seat-throughput (PBE only; CBE is computer-based) ──────────
        # Use allocation-reachable seats for the active pattern so the
        # estimate matches what timetable and distribution will budget.
        hall_specs = list(
            Hall.objects.open().values_list("rows", "columns", "capacity", "layout")
        )
        hall_sizes = [(rows, cols, mask) for rows, cols, _cap, mask in hall_specs]
        total_effective_seats = sum(
            hall_effective_capacity(rows, cols, pattern, cap, mask=mask)
            for rows, cols, cap, mask in hall_specs
        )
        # Same limits and classify as timetable generation (spec 0002).
        strict_limit = seating_rules.strict_limit(hall_sizes)
        relaxed_limit = seating_rules.relaxed_limit(hall_sizes)
        relaxed_count = refused_count = 0
        seats_per_period = int(total_effective_seats * utilization)

        am_seat_demand = pm_seat_demand = 0
        for course in (
            Course.objects.filter(exam_type="PBE")
            .prefetch_related(
                Prefetch(
                    "courses", queryset=Class.objects.active().with_student_count()
                )
            )
        ):
            # Live student count of the course's active classes.
            seats = sum(cls.student_count for cls in course.courses.all())
            if course.courses.all():  # no active class: never planned
                rule = seating_rules.classify(seats, strict_limit, relaxed_limit)
                relaxed_count += rule == RELAXED
                refused_count += rule == seating_rules.REFUSED
            # split_course's logic: PM only if any class is mapped to PM;
            # else AM (default).
            period = "AM"
            for cls in course.courses.all():
                if overrides.get((cls.name or "").strip().lower()) == "PM":
                    period = "PM"
                    break
            if period == "PM":
                pm_seat_demand += seats
            else:
                am_seat_demand += seats

        def _ceil_div(a: int, b: int) -> int:
            return -(-a // b) if b else 0

        am_days_for_seats = _ceil_div(am_seat_demand, seats_per_period)
        pm_days_for_seats = _ceil_div(pm_seat_demand, seats_per_period)
        throughput_days = max(am_days_for_seats, pm_days_for_seats)

        # ─── CBE capacity (same split and thresholds as generation) ─────
        cbe, cbe_refused = cbe_sections(
            get_courses(),
            autosplit_threshold=(
                constraints.cbe_autosplit_threshold if constraints else 9000
            ),
            group_count=constraints.cbe_group_count if constraints else 2,
            faculty_groups=(constraints.cbe_faculty_groups if constraints else None),
        )
        cbe_days = cbe_min_days(
            cbe,
            fullday_threshold=(
                constraints.cbe_fullday_threshold if constraints else 4500
            ),
            daily_cap=constraints.cbe_daily_cap_per_period if constraints else 4500,
        )

        min_exam_days = max(worst_total, throughput_days, cbe_days)
        recommended_exam_days = max(
            min_exam_days, int(round(min_exam_days * 1.2))
        )

        valid_per_week = max(1, 7 - len(excluded_weekdays))
        full_weeks = recommended_exam_days // valid_per_week
        remainder = recommended_exam_days % valid_per_week
        min_calendar_days = full_weeks * 7 + remainder

        # Surface which constraint dominates so the UI can explain the number.
        if worst_total >= max(throughput_days, cbe_days):
            bottleneck = "per_class"
        elif cbe_days >= throughput_days:
            bottleneck = "cbe_capacity"
        else:
            bottleneck = "seat_throughput"

        return Response(
            {
                "min_exam_days": min_exam_days,
                "recommended_exam_days": recommended_exam_days,
                "min_calendar_days": min_calendar_days,
                "min_calendar_weeks": (
                    round(min_calendar_days / 7, 1) if min_calendar_days else 0
                ),
                "excluded_weekdays": sorted(excluded_weekdays),
                "valid_exam_days_per_week": valid_per_week,
                "class_count": class_count,
                "worst_class_am": worst_am,
                "worst_class_pm": worst_pm,
                "per_class_min_days": worst_total,
                "throughput_min_days": throughput_days,
                "cbe_min_days": cbe_days,
                "cbe_refused": cbe_refused,
                "am_seat_demand": am_seat_demand,
                "pm_seat_demand": pm_seat_demand,
                "seats_per_period": seats_per_period,
                "bottleneck": bottleneck,
                "seat_pattern": pattern,
                "relaxed_count": relaxed_count,
                "refused_count": refused_count,
                "strict_limit": strict_limit,
                "relaxed_limit": relaxed_limit,
            }
        )


class DistributionListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        date = request.query_params.get("date")
        period = request.query_params.get("period")
        qs = Distribution.objects.all().select_related("hall")
        if not publication.visible_to(request.user):
            qs = qs.none()
        if date:
            qs = qs.filter(date=date)
        if period:
            qs = qs.filter(period=period)
        if not request.user.is_staff and request.user.department_id:
            qs = qs.filter(
                items__schedule__class_obj__department_id=request.user.department_id
            ).distinct()

        results = []
        for d in qs.prefetch_related("items__schedule__course", "items__schedule__class_obj__department"):
            results.append(
                {
                    "id": d.id,
                    "date": d.date,
                    "period": d.period,
                    "hall": {
                        "id": d.hall_id,
                        "name": d.hall.name,
                        "capacity": d.hall.capacity,
                    },
                    "items": [
                        {
                            "id": item.id,
                            "no_of_students": item.no_of_students,
                            "schedule": {
                                "id": item.schedule_id,
                                "course_code": item.schedule.course.code,
                                "course_name": item.schedule.course.name,
                                "class_name": item.schedule.class_obj.name,
                                "department_slug": item.schedule.class_obj.department.slug,
                            },
                        }
                        for item in d.items.all()
                    ],
                }
            )
        return Response(
            {"generated": Distribution.objects.exists(), "results": results}
        )


class DistributionStatisticsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        date = request.query_params.get("date")
        period = request.query_params.get("period")
        if not date or not period:
            return Response({"detail": "date and period are required."}, status=400)

        distributions = Distribution.objects.filter(date=date, period=period)
        if not publication.visible_to(request.user):
            distributions = distributions.none()
        agg = distributions.aggregate(
            total_halls=Count("id", distinct=True),
        )
        items_qs = (
            distributions.values_list("items__no_of_students", flat=True)
        )
        total_seats = sum(s or 0 for s in items_qs)
        return Response(
            {
                "date": date,
                "period": period,
                "halls_used": agg["total_halls"] or 0,
                "students_seated": total_seats,
            }
        )


class AllocationListView(APIView):
    """Aggregated by hall — drives the allocation summary page."""

    permission_classes = [IsAuthenticated, requires_feature("seat_allocation")]

    def get(self, request):
        date = request.query_params.get("date")
        period = request.query_params.get("period")
        qs = SeatArrangement.objects.all()
        if not publication.visible_to(request.user):
            qs = qs.none()
        if date:
            qs = qs.filter(date=date)
        if period:
            qs = qs.filter(period=period)
        if not request.user.is_staff and request.user.department_id:
            qs = qs.filter(cls__department_id=request.user.department_id)
        rows = (
            qs.values("hall_id", "hall__name", "date", "period")
            .annotate(
                placed=Count("id", filter=Q(seat_number__isnull=False)),
                not_placed=Count("id", filter=Q(seat_number__isnull=True)),
            )
            .order_by("hall__name")
        )
        return Response(
            {
                "generated": SeatArrangement.objects.exists(),
                "results": list(rows),
            }
        )


class HallAllocationView(APIView):
    """Full seat grid + placed/unplaced lists for one hall."""

    permission_classes = [IsAuthenticated, requires_feature("seat_allocation")]

    def get(self, request):
        date = request.query_params.get("date")
        period = request.query_params.get("period")
        hall_id = request.query_params.get("hall_id")
        if not all([date, period, hall_id]):
            return Response(
                {"detail": "date, period, and hall_id are required."},
                status=400,
            )
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return Response(
                {"detail": "date must be in YYYY-MM-DD format."},
                status=400,
            )
        try:
            hall = Hall.objects.get(pk=hall_id)
        except (Hall.DoesNotExist, ValueError):
            return Response({"detail": "Hall not found."}, status=404)

        qs = SeatArrangement.objects.filter(
            date=date, period=period, hall_id=hall_id
        ).select_related("student", "course", "cls", "cls__department")
        if not publication.visible_to(request.user):
            qs = qs.none()
        if not request.user.is_staff and request.user.department_id:
            qs = qs.filter(cls__department_id=request.user.department_id)

        relaxed_course_id = slot_relaxed_course_id(date, period)
        placed = []
        unplaced = []
        courses = {}
        for sa in qs:
            courses.setdefault(
                sa.course_id,
                {
                    "code": sa.course.code,
                    "name": sa.course.name,
                    "seating_rule": (
                        RELAXED if sa.course_id == relaxed_course_id else STRICT
                    ),
                },
            )
            row = {
                "id": sa.id,
                "seat_number": sa.seat_number,
                "course": {"id": sa.course_id, "code": sa.course.code},
                "class": {"id": sa.cls_id, "name": sa.cls.full_label},
                "student": (
                    {
                        "id": sa.student_id,
                        "matric_no": sa.student.matric_no,
                        "first_name": sa.student.first_name,
                        "last_name": sa.student.last_name,
                    }
                    if sa.student_id
                    else None
                ),
            }
            (placed if sa.seat_number is not None else unplaced).append(row)

        return Response(
            {
                "hall": {
                    "id": hall.id,
                    "name": hall.name,
                    "rows": hall.rows,
                    "columns": hall.columns,
                    "layout": hall.layout,
                    "seat_order": hall.seat_order,
                    "seat_count": hall.seat_count,
                    "capacity": hall.capacity,
                },
                "date": date,
                "period": period,
                "courses": sorted(courses.values(), key=lambda c: c["code"]),
                "placed": placed,
                "unplaced": unplaced,
            }
        )
