from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from datetime import datetime, timedelta
from django.db.models import Prefetch
from django.utils import timezone
import time
from .models import User
from .models import (
    BackgroundJob, Class, Course, Distribution, GenerationConstraints,
    Hall, TimeTable, SeatArrangement, DistributionItem, Student
)
from . import seating_rules
from tenancy.observability.capture import job_failed
from .readiness import NotReady, check as check_readiness, failed_result
from .utils import (
    get_courses, get_halls, split_course, generate, classify_courses,
    distribute_classes_to_halls, save_to_db, build_seat_arrangements,
    hall_fill_cap, in_walk_order,
    record_planned_students, slot_student_counts, relaxed_course_code,
    reset_timetable, save_to_timetable_db,
    clear_slot_distribution, slot_is_distributed,
    convert_hall_to_dict, courses_short_of_halls,
)


def _load_constraints():
    """Load the singleton GenerationConstraints, materializing defaults if absent."""
    obj = GenerationConstraints.objects.first()
    if not obj:
        obj = GenerationConstraints.objects.create()
    return obj


def _allocation_is_complete(date, period):
    """True only if a slot's allocation is actually finished, not just
    started.

    A bare ``SeatArrangement.objects.filter(...).exists()`` can't tell a
    fully-allocated slot apart from one a crashed worker left half-built.
    Two failure modes have hit this in practice: a hard timeout mid-hall-loop
    leaves some of the slot's halls with no rows at all, and a timeout
    between the hall loop and the final cross-hall reconciliation step
    leaves rows sitting with ``seat_number=NULL``. Either one must trigger a
    re-run, not a skip.
    """
    sa = SeatArrangement.objects.filter(date=str(date), period=period)
    if not sa.exists():
        return False
    if sa.filter(seat_number__isnull=True).exists():
        return False
    dist_halls = set(
        Distribution.objects.filter(date=str(date), period=period)
        .values_list('hall_id', flat=True)
    )
    seated_halls = set(sa.values_list('hall_id', flat=True))
    return dist_halls <= seated_halls


def _warning(title, items, hint):
    """One entry of a job's ``result_data['warnings']``: something the run
    finished without doing, which the job dialog lists for the officer."""
    return {'title': title, 'items': items, 'hint': hint}


def _plural(n, one, many):
    return f"{n} {one if n == 1 else many}"


def _unplaced_by_class_warning(unplaced):
    """Classes distribution found halls for only part of."""
    if not unplaced:
        return []
    return [_warning(
        f"{_plural(len(unplaced), 'class', 'classes')} did not get seats for "
        "every student",
        [
            f"{u['date'] + ' ' + u['period'] + ' · ' if 'date' in u else ''}"
            f"{u['label']} ({u['course']}): planned {u['planned']}, "
            f"got {u['assigned']}"
            for u in unplaced
        ],
        "Open more halls or raise the hall fill cap, then clear and "
        "regenerate the distribution.",
    )]


def _unplaced_students_warning(date, period):
    """Students allocation and the cross hall reconcile left without a seat,
    counted by hall."""
    from django.db.models import Count
    per_hall = list(
        SeatArrangement.objects.filter(
            date=date, period=period, seat_number__isnull=True
        )
        .values_list('hall__name')
        .annotate(n=Count('id'))
        .order_by('hall__name')
    )
    total = sum(n for _, n in per_hall)
    if not total:
        return []
    return [_warning(
        f"{_plural(total, 'student has', 'students have')} no seat on "
        f"{date} {period}",
        [f"{name}: {n}" for name, n in per_hall],
        "Assign them by hand from the hall's allocation page.",
    )]


def _fail_not_ready(job, report):
    """Stop a job the readiness safety net refused. Expected, so no
    traceback: the lists are what the officer needs to fix the data."""
    error = NotReady(report)
    job.status = 'failed'
    job.error_message = str(error)
    job.result_data = failed_result(report)
    job.completed_at = timezone.now()
    job_failed(job, error, expected=True)
    job.save()
    return {'status': 'failed', 'message': job.error_message}


def _assert_ready(stage, slots=None):
    report = check_readiness(stage, slots)
    if not report['ready']:
        raise NotReady(report)
    return report


class _Progress:
    """Throttled progress reporting for a long task.

    Each report costs about three writes (the BackgroundJob row plus
    Celery's result row). Reporting per hall made those writes a large
    share of an allocation's run time on a remote database, so reports
    closer together than ``min_interval`` seconds are dropped unless forced.
    """

    def __init__(self, task, job, min_interval=2.0):
        self.task = task
        self.job = job
        self.min_interval = min_interval
        self._last = None

    def report(self, pct, status, force=False):
        now = time.monotonic()
        if not force and self._last is not None and now - self._last < self.min_interval:
            return
        self._last = now
        self.job.progress = pct
        self.job.save(update_fields=['progress'])
        self.task.update_state(state='PROGRESS', meta={'progress': pct, 'status': status})


def _distribute_slot(
    date, period, halls_list, group_order=None, small_course_threshold=0
):
    """Distribute one slot, save it, and record what it planned.

    Returns ``(result, skipped_inactive, unplaced_by_class)``. The student
    counts are read once, so ``planned_students`` is exactly the number the
    halls were planned for.
    """
    from django.db import transaction
    from .readiness import skipped_inactive

    timetables = list(
        TimeTable.objects.filter(date=date, period=period).select_related(
            'course', 'class_obj', 'class_obj__department'
        )
    )
    size_map = slot_student_counts(timetables)
    result = distribute_classes_to_halls(
        timetables, halls_list, size_map=size_map,
        relaxed_course=relaxed_course_code(timetables),
        group_order=group_order,
        small_course_threshold=small_course_threshold,
    )
    # Replace, never add to, what the slot has: leftover rows, or a second
    # job for the same slot, must not leave two plans for one slot.
    with transaction.atomic():
        clear_slot_distribution(date, period)
        save_to_db(result, str(date), period)
        unplaced = record_planned_students(timetables, size_map, result)
    return result, skipped_inactive([(date, period)]), unplaced


@shared_task(bind=True)
def generate_timetable_task(self, job_id, user_id, start_date_str, end_date_str):
    """
    Celery task for timetable generation
    """
    job = BackgroundJob.objects.get(job_id=job_id)
    progress = _Progress(self, job)

    try:
        job.status = 'running'
        job.total_steps = 100  # progress is a percentage
        job.save()

        print(f"\n[TASK] Starting timetable generation task {job_id}")
        print(f"[TASK] Date range: {start_date_str} to {end_date_str}")
        print(f"[TASK] Requested by user: {user_id}")
        
        # Parse dates
        startDate = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        endDate = datetime.strptime(end_date_str, "%Y-%m-%d").date()

        # Safety net: the view checks this before queuing, but a list may
        # have changed since.
        _assert_ready('timetable')

        constraints = _load_constraints()
        excluded_days = set(constraints.excluded_weekdays or [])

        # Generate dates honouring admin-configured excluded weekdays
        dates = []
        currentDate = startDate
        while currentDate <= endDate:
            if currentDate.weekday() not in excluded_days:
                dates.append(currentDate)
            currentDate = currentDate + timedelta(days=1)

        print(f"[TASK] Generated {len(dates)} valid dates (excluded weekdays: {sorted(excluded_days)})")
        
        progress.report(5, 'Loading courses and halls...', force=True)

        # Get courses and halls
        courses = get_courses()
        halls = get_halls(pattern=constraints.seat_pattern)
        
        print(f"[TASK] Loaded {len(courses)} courses and {len(halls)} halls")

        # Classify every course once, before the AM/PM split (spec 0002).
        hall_sizes = list(Hall.objects.open().values_list('rows', 'columns', 'layout'))
        strict_limit = seating_rules.strict_limit(hall_sizes)
        relaxed_limit = seating_rules.relaxed_limit(hall_sizes)
        courses, refused_oversized = classify_courses(
            courses, strict_limit, relaxed_limit
        )
        print(
            f"[TASK] Seating limits per course per period - strict: {strict_limit}, "
            f"relaxed: {relaxed_limit}. Refused as oversized: "
            f"{[r['code'] for r in refused_oversized]}"
        )
        
        # Split courses into AM and PM periods using admin-configured map
        AM_courses, PM_courses = split_course(
            courses, class_period_overrides=constraints.class_period_overrides
        )
        
        print(f"[TASK] Split courses - AM: {len(AM_courses)}, PM: {len(PM_courses)}")
        
        progress.report(10, 'Planning the timetable...', force=True)

        # Each planned period is checked with the real distribution packer,
        # on the halls and settings distribution will use, so no slot is
        # booked fuller than distribution can seat.
        open_halls = list(Hall.objects.open())

        def fit_check(period_courses):
            halls_now = convert_hall_to_dict(
                open_halls,
                safety_factor=float(constraints.pbe_hall_utilization),
                pattern=constraints.seat_pattern,
                course_limits=constraints.hall_course_limits,
            )
            return courses_short_of_halls(
                period_courses, halls_now,
                group_order=constraints.hall_group_order,
                small_course_threshold=constraints.small_course_threshold,
            )

        def planned_date(done, total):
            # Planning the dates is most of the run: 10% to 90%.
            progress.report(
                10 + int(done / total * 80),
                f'Planned date {done}/{total}',
                force=done == total,
            )

        # Plan in memory first: the old timetable stays in place (and
        # published, if it was) until the new one is ready to replace it.
        summary = generate(
            dates,
            AM_courses,
            PM_courses,
            halls,
            autosplit_threshold=constraints.cbe_autosplit_threshold,
            fullday_threshold=constraints.cbe_fullday_threshold,
            daily_cap=constraints.cbe_daily_cap_per_period,
            pbe_utilization=float(constraints.pbe_hall_utilization),
            cbe_group_count=constraints.cbe_group_count,
            cbe_faculty_groups=constraints.cbe_faculty_groups or {},
            save=False,
            on_date_planned=planned_date,
            fit_check=fit_check,
        )
        schedules = summary.pop('schedules')

        print(f"[TASK] Timetable generation completed")
        print(f"[TASK] Summary: {summary}")

        progress.report(92, 'Saving the timetable...', force=True)

        # Swap the timetable in one transaction: a failure leaves the old
        # one, its distribution, seating and publication exactly as they
        # were. Distribution and seating were planned from the old
        # timetable, so they go with it. A new timetable is a draft: it
        # needs publishing before department officers see it.
        from django.db import transaction
        from .models import SystemSettings
        from . import publication
        with transaction.atomic():
            reset_timetable()
            save_to_timetable_db(schedules)
            settings = SystemSettings.objects.first()
            if settings:
                settings.has_timetable = True
                settings.save()
            publication.clear(settings)

        # Complete job
        job.status = 'success'
        job.progress = 100
        job.completed_at = timezone.now()

        # A course left out has no exam: say so, don't call it a success.
        skipped = [
            f"{code} ({period})"
            for period, key in (('AM', 'skipped_am_codes'), ('PM', 'skipped_pm_codes'))
            for code in summary.get(key, [])
        ]
        warnings = []
        if skipped:
            warnings.append(_warning(
                f"{_plural(len(skipped), 'course', 'courses')} could not be "
                "scheduled",
                skipped,
                "No date in the range had room for them. Widen the date "
                "range or open more halls, then generate again.",
            ))
        if refused_oversized:
            warnings.append(_warning(
                f"{_plural(len(refused_oversized), 'course is', 'courses are')} "
                "too big for one period, even with relaxed seating",
                [f"{r['code']} ({r['students']} students)" for r in refused_oversized],
                f"One period seats at most {relaxed_limit} students of a "
                "course. Open more halls, or split the course.",
            ))
        refused_cbe = summary.get('refused_cbe', [])
        if refused_cbe:
            warnings.append(_warning(
                f"{_plural(len(refused_cbe), 'CBE paper is', 'CBE papers are')} "
                "too big for one day",
                [f"{r['code']} ({r['students']} students)" for r in refused_cbe],
                f"A CBE paper seats at most {refused_cbe[0]['limit']} students "
                "over a full day. Assign its classes' faculties to different "
                "CBE groups so it splits, or raise the auto-split threshold.",
            ))
        left_out = len(skipped) + len(refused_oversized) + len(refused_cbe)
        if left_out:
            message = (
                f"Timetable generated without "
                f"{_plural(left_out, 'course', 'courses')}"
            )
        else:
            message = 'Timetable generated successfully'
        if summary.get('relaxed_courses'):
            codes = ', '.join(r['code'] for r in summary['relaxed_courses'])
            message += f'. Relaxed seating: {codes}'
        job.result_data = {
            'warnings': warnings,
            'message': message,
            'dates_count': len(dates),
            'total_scheduled': summary.get('total_scheduled', 0),
            'am_scheduled': summary.get('am_scheduled', 0),
            'pm_scheduled': summary.get('pm_scheduled', 0),
            'am_skipped': summary.get('am_skipped', 0),
            'pm_skipped': summary.get('pm_skipped', 0),
            'skipped_courses': {
                'AM': summary.get('skipped_am_codes', []),
                'PM': summary.get('skipped_pm_codes', []),
            },
            'timetables_created': TimeTable.objects.count(),
            'relaxed_courses': [
                {**r, 'strict_limit': strict_limit, 'relaxed_limit': relaxed_limit}
                for r in summary.get('relaxed_courses', [])
            ],
            'refused_oversized': refused_oversized,
            'refused_cbe': summary.get('refused_cbe', []),
            'rebooked_after_fit_check': summary.get('rebooked_after_fit_check', 0),
        }
        job.save()
        
        print(f"[TASK] Job completed successfully. Job ID: {job_id}")
        return job.result_data

    except NotReady as e:
        return _fail_not_ready(job, e.report)
    except Exception as e:
        import traceback
        print(f"[ERROR] Timetable generation failed: {str(e)}")
        print(f"[ERROR] Traceback:\\n{traceback.format_exc()}")
        job.status = 'failed'
        job.error_message = str(e)
        # Store full traceback for debugging
        job.result_data = {
            'error': str(e),
            'error_type': type(e).__name__,
            'traceback': traceback.format_exc()
        }
        job.completed_at = timezone.now()
        job_failed(job, e)
        job.save()
        raise


@shared_task(bind=True)
def generate_distribution_task(self, job_id, user_id, date, period):
    """
    Celery task for distribution generation
    """
    job = BackgroundJob.objects.get(job_id=job_id)
    
    try:
        job.status = 'running'
        job.total_steps = 100
        job.save()
        
        # Update progress: 10%
        job.progress = 10
        job.save()
        self.update_state(state='PROGRESS', meta={'progress': 10, 'status': 'Loading halls and timetables...'})
        
        # Load data
        halls = Hall.objects.open()
        timetables = TimeTable.objects.filter(date=date, period=period).select_related(
            'course', 'class_obj', 'class_obj__department'
        )
        
        if not timetables.exists():
            raise ValueError(f"No timetables found for {date} {period}")

        _assert_ready('distribution', [(date, period)])
        
        # Update progress: 30%
        job.progress = 30
        job.save()
        self.update_state(state='PROGRESS', meta={'progress': 30, 'status': 'Converting halls...'})
        
        # Update progress: 50%
        job.progress = 50
        job.save()
        self.update_state(state='PROGRESS', meta={'progress': 50, 'status': 'Distributing classes to halls...'})

        # Convert halls with the same safety factor timetable uses, so both
        # stages see one shared capacity model.
        constraints = _load_constraints()
        halls_list = convert_hall_to_dict(
            halls,
            safety_factor=float(constraints.pbe_hall_utilization),
            pattern=constraints.seat_pattern,
            course_limits=constraints.hall_course_limits,
        )

        # Distribute classes across halls (bulk placement + tail
        # consolidation), save, and record planned_students.
        _, skipped, unplaced = _distribute_slot(
            date, period, halls_list, constraints.hall_group_order,
            constraints.small_course_threshold,
        )

        job.progress = 70
        job.save()
        self.update_state(state='PROGRESS', meta={'progress': 70, 'status': 'Distribution saved...'})
        
        # Complete job
        job.status = 'success'
        job.progress = 100
        job.completed_at = timezone.now()
        distributions_count = Distribution.objects.filter(date=date, period=period).count()
        job.result_data = {
            'warnings': _unplaced_by_class_warning(unplaced),
            'message': 'Distribution generated successfully',
            'distributions_created': distributions_count,
            'date': date,
            'period': period,
            'skipped_inactive': skipped,
            'unplaced_by_class': unplaced,
        }
        job.save()

        return {'status': 'success', 'message': 'Distribution generated successfully'}

    except NotReady as e:
        return _fail_not_ready(job, e.report)
    except Exception as e:
        import traceback
        job.status = 'failed'
        job.error_message = str(e)
        # Store full traceback for debugging
        job.result_data = {
            'error': str(e),
            'error_type': type(e).__name__,
            'traceback': traceback.format_exc()
        }
        job.completed_at = timezone.now()
        job_failed(job, e)
        job.save()
        raise


@shared_task(bind=True)
def generate_allocation_task(self, job_id, user_id, date, period):
    """
    Celery task for seat allocation generation
    """
    job = BackgroundJob.objects.get(job_id=job_id)
    progress = _Progress(self, job)

    try:
        job.status = 'running'
        job.save()

        constraints = _load_constraints()

        progress.report(5, 'Loading distributions...')
        
        # Get distributions for the date and period. They are seated in
        # walk order (sorted below), the order distribution filled them in:
        # the first hall takes each class's lowest matric numbers, the next
        # hall continues where it left off, so a class's matric run follows
        # the halls it sits in. Hall name breaks ties so a re-run hands out
        # the same blocks.
        distributions = Distribution.objects.filter(
            date=date, period=period
        ).select_related('hall').prefetch_related(
            Prefetch(
                'items',
                queryset=DistributionItem.objects.select_related(
                    'schedule__course', 'schedule__class_obj'
                ),
            )
        ).order_by('hall__name', 'id')
        
        if not distributions.exists():
            raise ValueError(f"No distributions found for {date} {period}. Please generate distribution first.")

        # Safety net: refuse a slot whose student lists changed after its
        # distribution, before anything is cleared or seated.
        _assert_ready('allocation', [(date, period)])

        distributions = in_walk_order(
            distributions, constraints, key=lambda d: d.hall
        )
        total_halls = len(distributions)
        job.total_steps = 100  # Use percentage scale
        job.save()

        # Every student the slot can draw from, read in one query instead of
        # one per distribution item. Each class's list is matric ordered, and
        # a hall takes the next ``no_of_students`` from where the previous
        # hall stopped, which is the same block the old per item
        # ``exclude(already allocated).order_by('matric_no')[:n]`` query
        # returned. On a remote database those per item round trips (~400 a
        # slot) were most of the task's run time.
        class_ids = {
            item.schedule.class_obj_id
            for distribution in distributions
            for item in distribution.items.all()
        }
        pool_by_class = {}
        for sid, matric_no, level_id, department_id in (
            Student.objects.filter(level_id__in=class_ids)
            .order_by('matric_no', 'id')
            .values_list('id', 'matric_no', 'level_id', 'department_id')
        ):
            pool_by_class.setdefault((level_id, department_id), []).append((sid, matric_no))
        course_map = {
            item.schedule.course.code: item.schedule.course
            for distribution in distributions
            for item in distribution.items.all()
        }

        # How far into its class's pool each (class, course) has been handed
        # out across the halls seen so far.
        taken_by_class = {}

        processed_halls = 0
        total_allocated = 0
        total_unplaced = 0
        arrangements = []

        for distribution in distributions:
            processed_halls += 1
            # Progress from 5% to 85% during hall processing
            progress.report(
                5 + int((processed_halls - 1) / total_halls * 80),
                f'Processing hall {processed_halls}/{total_halls}: {distribution.hall.name}',
            )

            rows = distribution.hall.rows
            cols = distribution.hall.columns
            mask = distribution.hall.layout
            # The hard fill cap (pbe_hall_utilization of the hall's seats).
            # Distribution never plans past it; this refuses a plan made
            # under a higher cap, which must be distributed again.
            hall_capacity = hall_fill_cap(distribution.hall, constraints)
            students = []
            # Courses here seated under the relaxed rule (spec 0002).
            relaxed_courses = set()

            # Build student list for this hall
            for item in distribution.items.all():
                course_code = item.schedule.course.code
                class_obj = item.schedule.class_obj
                if item.schedule.seating_rule == seating_rules.RELAXED:
                    relaxed_courses.add(course_code)
                class_key = f"{class_obj.id}_{course_code}"
                pool = pool_by_class.get((class_obj.id, class_obj.department_id), [])
                start = taken_by_class.get(class_key, 0)
                block = pool[start:start + item.no_of_students]
                taken_by_class[class_key] = start + len(block)
                for student_id, matric_no in block:
                    students.append({
                        "student_id": student_id,
                        "name": matric_no,
                        "course": course_code,
                        "cls_id": class_obj.id
                    })

            # Check capacity
            if len(students) > hall_capacity:
                raise ValueError(
                    f"Cannot allocate {len(students)} students to "
                    f"{distribution.hall.name}: its fill cap is {hall_capacity} "
                    f"({float(constraints.pbe_hall_utilization):.0%} of its seats). "
                    "Run distribution again for this slot."
                )

            # Skip if no students to allocate
            if len(students) == 0:
                continue

            # Perform seat allocation. We always use the full per-course
            # fallback (checkerboard → diagonal → sequential): the first
            # course in a hall takes even-parity cells, the second takes
            # odd-parity, and subsequent courses land randomly under the
            # adjacency check. This gives the highest placement rate
            # regardless of which seat_pattern was used to size the hall.
            hall_rows, hall_placed, hall_unplaced = build_seat_arrangements(
                students, rows, cols,
                datetime.strptime(date, "%Y-%m-%d").date(),
                period, distribution.hall, course_map,
                success_threshold_pct=constraints.placement_success_threshold_pct,
                relaxed_courses=relaxed_courses,
                mask=mask,
                seat_order=distribution.hall.seat_order,
            )
            arrangements.extend(hall_rows)
            total_allocated += hall_placed
            total_unplaced += hall_unplaced

        progress.report(88, 'Saving seat arrangements...')
        # Clear any prior attempt for this slot and write the new one in a
        # single transaction, so the slot is never left half-built: a hard
        # timeout before this point leaves the old arrangement untouched, and
        # one inside it rolls back. The slot is still cleared first so a
        # re-run never duplicates students already placed.
        from django.db import transaction
        with transaction.atomic():
            SeatArrangement.objects.filter(date=date, period=period).delete()
            SeatArrangement.objects.bulk_create(arrangements, batch_size=2000)

        # Update progress: 90% - all halls processed
        progress.report(90, 'Reconciling unplaced students...', force=True)

        # ─── Cross-hall reconciliation ────────────────────────────────
        # Move unplaced students into any other hall that still has empty
        # seats and adjacency-room for that course. Uses the same quarter
        # rule as the per-hall placer: a student lands in a parity quarter
        # where their course doesn't already conflict.
        from .utils import reconcile_unplaced
        reconciled = reconcile_unplaced(date, period)
        total_allocated += reconciled
        total_unplaced -= reconciled

        # Complete job
        job.status = 'success'
        job.progress = 100
        job.completed_at = timezone.now()
        job.result_data = {
            'warnings': _unplaced_students_warning(date, period),
            'message': 'Seat allocation completed successfully',
            'total_allocated': total_allocated,
            'total_unplaced': total_unplaced,
            'halls_processed': processed_halls,
            'reconciled_across_halls': reconciled,
            'date': date,
            'period': period
        }
        job.save()
        
        return {
            'status': 'success',
            'message': f'Allocated {total_allocated} students across {processed_halls} halls. {total_unplaced} unplaced.'
        }

    except NotReady as e:
        return _fail_not_ready(job, e.report)
    except Exception as e:
        import traceback
        job.status = 'failed'
        job.error_message = str(e)
        # Store full traceback for debugging
        job.result_data = {
            'error': str(e),
            'error_type': type(e).__name__,
            'traceback': traceback.format_exc()
        }
        job.completed_at = timezone.now()
        job_failed(job, e)
        job.save()
        raise


@shared_task(bind=True)
def generate_distribution_all_task(self, job_id, user_id):
    """Distribute every (date, period) slot present in the timetable.
    Skips any slot that already has a distribution plan."""
    job = BackgroundJob.objects.get(job_id=job_id)
    try:
        job.status = 'running'
        job.total_steps = 100
        job.save()
        constraints = _load_constraints()
        slots = list(
            TimeTable.objects.values_list('date', 'period').distinct().order_by('date', 'period')
        )
        if not slots:
            raise ValueError('No timetable rows found — generate the timetable first.')
        # Check every slot before distributing any.
        _assert_ready('distribution')
        total = len(slots)
        results = []
        # Halls don't change between slots — read + convert them once instead
        # of re-querying every iteration. distribute_classes_to_halls mutates
        # its hall dicts in place, so rebuild a fresh copy per slot below.
        halls_qs = list(Hall.objects.open())
        skipped_inactive = []
        unplaced_by_class = []
        for i, (date, period) in enumerate(slots):
            progress = int((i / total) * 95)
            self.update_state(state='PROGRESS', meta={
                'progress': progress,
                'status': f'Distributing {date} {period} ({i+1}/{total})',
            })
            job.progress = progress
            job.save(update_fields=['progress'])
            if slot_is_distributed(date, period):
                results.append({'date': str(date), 'period': period, 'skipped': True})
                continue
            halls_list = convert_hall_to_dict(
                halls_qs,
                safety_factor=float(constraints.pbe_hall_utilization),
                pattern=constraints.seat_pattern,
                course_limits=constraints.hall_course_limits,
            )
            result, skipped, unplaced = _distribute_slot(
                date, period, halls_list, constraints.hall_group_order,
                constraints.small_course_threshold,
            )
            skipped_inactive.extend(skipped)
            unplaced_by_class.extend(
                {'date': str(date), 'period': period, **u} for u in unplaced
            )
            results.append({'date': str(date), 'period': period, 'halls_used': len(result)})

        job.status = 'success'
        job.progress = 100
        job.completed_at = timezone.now()
        skipped = sum(1 for r in results if r.get('skipped'))
        job.result_data = {
            'warnings': _unplaced_by_class_warning(unplaced_by_class),
            'message': f'Bulk distribution completed across {total} slot(s) ({skipped} skipped)',
            'slots_processed': total,
            'slots_skipped': skipped,
            'slots': results,
            'skipped_inactive': skipped_inactive,
            'unplaced_by_class': unplaced_by_class,
        }
        job.save()
        return {'status': 'success', 'message': job.result_data['message']}
    except NotReady as e:
        return _fail_not_ready(job, e.report)
    except SoftTimeLimitExceeded as exc:
        # Soft limit hit — fail gracefully before the hard SIGKILL so the job
        # isn't left stuck in 'running'. This task is resumable: it skips slots
        # that already have a Distribution, so a re-run continues where it
        # stopped.
        done = len(results) if 'results' in locals() else 0
        total_slots = total if 'total' in locals() else 0
        msg = (
            f'Timed out after distributing {done}/{total_slots} slot(s). '
            'Re-run to finish the rest — already-distributed slots are skipped.'
        )
        job.status = 'failed'
        job.error_message = msg
        job.result_data = {
            'error': msg,
            'error_type': 'SoftTimeLimitExceeded',
            'slots_processed': done,
            'slots_total': total_slots,
        }
        job.completed_at = timezone.now()
        job_failed(job, exc)
        job.save()
        raise
    except Exception as e:
        import traceback
        job.status = 'failed'
        job.error_message = str(e)
        job.result_data = {
            'error': str(e),
            'error_type': type(e).__name__,
            'traceback': traceback.format_exc(),
        }
        job.completed_at = timezone.now()
        job_failed(job, e)
        job.save()
        raise


@shared_task(bind=True)
def generate_allocation_all_task(self, job_id, user_id):
    """Allocate every (date, period) slot that has a Distribution but no
    SeatArrangement yet. Re-uses the per-slot allocation task by inline
    invocation."""
    job = BackgroundJob.objects.get(job_id=job_id)
    try:
        job.status = 'running'
        job.total_steps = 100
        job.save()
        slots = list(
            Distribution.objects.values_list('date', 'period').distinct().order_by('date', 'period')
        )
        if not slots:
            raise ValueError('No distributions found — generate distribution first.')
        # Check every slot before seating anyone in any of them.
        _assert_ready('allocation')
        total = len(slots)
        results = []
        warnings = []
        failed = []
        for i, (date, period) in enumerate(slots):
            progress = int((i / total) * 95)
            self.update_state(state='PROGRESS', meta={
                'progress': progress,
                'status': f'Allocating {date} {period} ({i+1}/{total})',
            })
            job.progress = progress
            job.save()
            if _allocation_is_complete(date, period):
                results.append({'date': str(date), 'period': period, 'skipped': True})
                continue
            sub_id = f'{job_id}::{date}::{period}'
            sub_job = BackgroundJob.objects.create(
                job_id=sub_id,
                job_type='allocation',
                status='pending',
                created_by_id=user_id,
                params={'date': str(date), 'period': period},
            )
            # Inline call — bypasses the broker, runs in this worker.
            sub_result = generate_allocation_task.apply(args=[sub_id, user_id, str(date), period])
            sub_job.refresh_from_db()
            if sub_result.failed() and isinstance(sub_result.result, SoftTimeLimitExceeded):
                # .apply() runs eagerly and swallows exceptions raised inside
                # the sub-task into a FAILURE result instead of propagating
                # them — so a soft-timeout hit while allocating this slot
                # would otherwise go unnoticed and the outer loop would keep
                # burning the remaining budget until the uncatchable hard
                # SIGKILL. Re-raise so the except SoftTimeLimitExceeded
                # handler below can save a graceful, resumable state.
                raise sub_result.result
            rd = sub_job.result_data or {}
            warnings.extend(rd.get('warnings') or [])
            if sub_job.status == 'failed':
                failed.append(f'{date} {period}: {sub_job.error_message}')
            results.append({
                'date': str(date),
                'period': period,
                'status': sub_job.status,
                'allocated': rd.get('total_allocated'),
                'unplaced': rd.get('total_unplaced'),
                'reconciled': rd.get('reconciled_across_halls'),
            })

        job.progress = 100
        job.completed_at = timezone.now()
        skipped = sum(1 for r in results if r.get('skipped'))
        job.result_data = {
            'warnings': warnings,
            'message': f'Bulk allocation completed across {total} slot(s) ({skipped} skipped)',
            'slots_processed': total,
            'slots_skipped': skipped,
            'slots': results,
        }
        if failed:
            # A slot nobody is seated in is a failure, not a success with
            # small print. Retry runs only the slots still unfinished.
            job.status = 'failed'
            job.error_message = (
                f"Allocation failed for {_plural(len(failed), 'slot', 'slots')}: "
                + '; '.join(failed)
                + '. The other slots are seated; retry to run the failed ones.'
            )
            job.result_data['failed_slots'] = failed
            # Each failed slot was recorded by its own job; this records the
            # bulk job ending failed.
            job_failed(job)
        else:
            job.status = 'success'
        job.save()
        return {'status': job.status, 'message': job.error_message or job.result_data['message']}
    except NotReady as e:
        return _fail_not_ready(job, e.report)
    except SoftTimeLimitExceeded as exc:
        # Soft limit hit — fail gracefully before the hard SIGKILL. Resumable:
        # slots that already have a SeatArrangement are skipped on re-run.
        done = len(results) if 'results' in locals() else 0
        total_slots = total if 'total' in locals() else 0
        msg = (
            f'Timed out after allocating {done}/{total_slots} slot(s). '
            'Re-run to finish the rest — already-allocated slots are skipped.'
        )
        job.status = 'failed'
        job.error_message = msg
        job.result_data = {
            'error': msg,
            'error_type': 'SoftTimeLimitExceeded',
            'slots_processed': done,
            'slots_total': total_slots,
        }
        job.completed_at = timezone.now()
        job_failed(job, exc)
        job.save()
        raise
    except Exception as e:
        import traceback
        job.status = 'failed'
        job.error_message = str(e)
        job.result_data = {
            'error': str(e),
            'error_type': type(e).__name__,
            'traceback': traceback.format_exc(),
        }
        job.completed_at = timezone.now()
        job_failed(job, e)
        job.save()
        raise


@shared_task(bind=True)
def portal_sync_task(self, job_id, run_id):
    """Fetch the school's portal and apply it (spec 0005). Runs in the
    school's schema, like every task queued from a school request."""
    from .portal_sync.runs import execute

    execute(run_id, job_id)


@shared_task
def prune_sync_runs():
    """Delete portal sync runs older than 90 days in every school (beat,
    daily)."""
    from .portal_sync.runs import prune

    prune()
