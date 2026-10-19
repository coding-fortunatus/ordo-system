"""A school's effective plan limits, and their enforcement (spec 0003, child 0005).

Each limit is the school's override if set, else its plan's value; null means
unlimited. ``effective`` is the only reader of plan fields. The caps take a
per school advisory lock inside the caller's transaction, so two concurrent
writes can't jointly go over a limit; the lock is released at commit.
"""
from dataclasses import dataclass

from django.db import connection
from django.utils import timezone
from rest_framework import status

from ems.api.exceptions import CodedAPIException


@dataclass(frozen=True)
class Limits:
    max_students: int | None
    max_staff_users: int | None
    features: frozenset


def effective(school):
    plan = school.plan
    return Limits(
        max_students=_pick(school.max_students_override, plan.max_students),
        max_staff_users=_pick(school.max_staff_users_override, plan.max_staff_users),
        features=frozenset(
            plan.features if school.features_override is None else school.features_override
        ),
    )


def _pick(override, value):
    return value if override is None else override


def current():
    """The effective limits of the school this connection is working for."""
    return effective(connection.tenant)


class PlanLimitExceeded(CodedAPIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "This would go over your plan's limit."
    default_code = "plan_limit"


class FeatureDisabled(CodedAPIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Your plan doesn't include this feature."
    default_code = "feature_disabled"


# Feature names as the error message shows them.
FEATURE_LABELS = {
    "seat_allocation": "Seat allocation",
    "exports": "Exports",
    "publication": "Timetable publishing",
    "branding": "Custom branding",
    "portal_sync": "Portal sync",
}


def has_feature(key):
    return key in current().features


def assert_feature(key):
    if not has_feature(key):
        raise FeatureDisabled(f"{FEATURE_LABELS[key]} isn't included in your plan.")


# Job types that need a feature to run. Timetable and distribution need none.
JOB_FEATURES = {"allocation": "seat_allocation", "allocation_all": "seat_allocation"}


def assert_feature_for_job(job_type):
    if key := JOB_FEATURES.get(job_type):
        assert_feature(key)


def _lock(name):
    if not connection.in_atomic_block:
        raise RuntimeError("Plan limits must be checked inside the write's transaction.")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            [f"{connection.schema_name}:{name}"],
        )


def lock_students():
    """Take the student cap lock. Read which rows are new only after this."""
    _lock("students")


def _plural(n, word):
    return f"{n:,} {word}" if n == 1 else f"{n:,} {word}s"


def assert_can_add_students(n, *, action="This upload"):
    """403 ``plan_limit`` if ``n`` more students would pass ``max_students``.

    ``n`` counts new matric numbers only: rows updating an existing student
    don't count. Call inside the insert's transaction, after ``lock_students``
    if ``n`` depends on what already exists.
    """
    lock_students()
    cap = current().max_students
    if n <= 0 or cap is None:
        return
    from ems.models import Student

    count = Student.objects.count()
    if count + n > cap:
        raise PlanLimitExceeded(
            f"{action} adds {_plural(n, 'student')}; your plan allows "
            f"{max(cap - count, 0):,} more."
        )


def staff_in_use():
    """Accounts that count toward ``max_staff_users``."""
    from ems.models import User

    return User.objects.filter(is_active=True).count()


def assert_can_add_staff(n=1, *, count_invites=False, except_email=None):
    """403 ``plan_limit`` if ``n`` more staff accounts would pass the cap.

    ``count_invites`` also counts open, unexpired invites, so outstanding
    invites can't oversubscribe the plan. ``except_email`` leaves out the
    invite a resend is about to replace. Call inside the write's transaction.
    """
    _lock("staff")
    cap = current().max_staff_users
    if n <= 0 or cap is None:
        return
    used = staff_in_use()
    if count_invites:
        from ems import invites

        pending = invites.open_invites().filter(expires_at__gt=timezone.now())
        if except_email:
            pending = pending.exclude(email=except_email)
        used += pending.count()
    if used + n > cap:
        raise PlanLimitExceeded(
            f"Your plan allows {_plural(cap, 'staff account')}, and "
            f"{used:,} {'is' if used == 1 else 'are'} already "
            f"{'in use or invited' if count_invites else 'in use'}."
        )
