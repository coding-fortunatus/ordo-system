"""Schools and plans in the console, ``/api/platform/`` (spec 0003, children 0004, 0005).

Creating a school saves it with its primary domain and queues provisioning,
which builds the schema in the background. Every transition locks the
``School`` row and records a ``PlatformAuditLog`` row.
"""
import logging
from datetime import date, timedelta

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.models import Count, OuterRef, Prefetch, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework import generics, serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from ems.api.exceptions import Conflict
from ems.api.pagination import DefaultPagination
from tenancy import audit, exports, support
from tenancy.auth import PlatformTokenAuthentication
from tenancy.models import (
    Domain,
    Plan,
    PlatformAuditLog,
    School,
    SupportSession,
    UsageSnapshot,
)
from tenancy.permissions import IsPlatformOwner, IsPlatformStaff
from tenancy.serializers import (
    CreateSchoolSerializer,
    PlanSerializer,
    PlatformAuditLogSerializer,
    SchoolDetailSerializer,
    SchoolListSerializer,
    SupportSessionSerializer,
    UpdateSchoolSerializer,
)
from tenancy.slugs import schema_name_for
from tenancy.tasks import export_school, provision_school
from tenancy.views import PlatformView
from tenancy.console_views import backup_summary

logger = logging.getLogger(__name__)


def schools():
    """Every school but the public schema's own row."""
    return (
        School.objects.exclude(schema_name=get_public_schema_name())
        .select_related("plan", "created_by")
        .prefetch_related("domains")
    )


def _cannot(action, school):
    return Conflict(f"Cannot {action} a school that is {school.get_status_display().lower()}.")


def _first_admin(school):
    """Whether the first admin has joined, and when their open invite expires."""
    if school.status not in School.LIVE_STATUSES:
        return None
    with schema_context(school.schema_name):
        from ems import invites

        joined = invites.user_exists(school.first_admin_email)
        pending = (
            invites.open_invites()
            .filter(email=school.first_admin_email)
            .values_list("expires_at", flat=True)
            .first()
        )
    return {"joined": joined, "invite_expires_at": None if joined else pending}


def _joined(schools):
    """``{school id: whether its first admin has joined}`` for the live ones,
    in one query across their schemas. Mirrors ``invites.user_exists``:
    support accounts don't count."""
    from ems.models import User

    live = [s for s in schools if s.status in School.LIVE_STATUSES]
    if not live:
        return {}
    table = User._meta.db_table
    parts, params = [], []
    for school in live:
        parts.append(
            f"SELECT %s, EXISTS (SELECT 1 FROM {connection.ops.quote_name(school.schema_name)}.{table}"
            " WHERE support_expires_at IS NULL AND UPPER(email) = UPPER(%s))"
        )
        params += [school.pk, school.first_admin_email]
    with connection.cursor() as cursor:
        cursor.execute(" UNION ALL ".join(parts), params)
        return dict(cursor.fetchall())


def _detail(school, code=status.HTTP_200_OK):
    data = SchoolDetailSerializer(
        school, context={"first_admin": _first_admin(school)}
    ).data
    return Response(data, status=code)


def _queue_provisioning(school):
    school_id = school.pk
    transaction.on_commit(lambda: provision_school.delay(school_id))


class SchoolListView(generics.ListAPIView):
    authentication_classes = [PlatformTokenAuthentication]
    serializer_class = SchoolListSerializer
    pagination_class = DefaultPagination

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsPlatformOwner()]
        return [IsPlatformStaff()]

    def get_queryset(self):
        # Each school's latest snapshot only: one DISTINCT ON query per page.
        latest = UsageSnapshot.objects.order_by("school_id", "-taken_on").distinct("school_id")
        queryset = schools().prefetch_related(
            Prefetch("usage_snapshots", queryset=latest, to_attr="latest_usage")
        ).order_by("-created_at", "-id")
        params = self.request.query_params
        if params.get("status") in School.Status.values:
            queryset = queryset.filter(status=params["status"])
        if q := params.get("q", "").strip():
            queryset = queryset.filter(Q(name__icontains=q) | Q(slug__icontains=q))
        return queryset

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        context = {**self.get_serializer_context(), "joined": _joined(page)}
        return self.get_paginated_response(SchoolListSerializer(page, many=True, context=context).data)

    def post(self, request):
        serializer = CreateSchoolSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        slug = data["slug"]
        try:
            with transaction.atomic():
                school = School.objects.create(
                    **data,
                    schema_name=schema_name_for(slug),
                    status=School.Status.PROVISIONING,
                    provisioning_started_at=timezone.now(),
                    created_by=request.user,
                )
                Domain.objects.create(
                    domain=f"{slug}.{settings.PLATFORM_BASE_DOMAIN}",
                    tenant=school,
                    is_primary=True,
                )
                audit.record(
                    request, "school_created", school, slug=slug, plan=school.plan.code
                )
                _queue_provisioning(school)
        except IntegrityError:
            # Another Owner took the slug between the check and the save.
            return Response(
                {
                    "detail": "Validation failed.",
                    "errors": {"slug": ["This address is already taken."]},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return _detail(schools().get(pk=school.pk), status.HTTP_202_ACCEPTED)


class SchoolDetailView(PlatformView):
    def get_permissions(self):
        if self.request.method == "PATCH":
            return [IsPlatformOwner()]
        return [IsPlatformStaff()]

    def get(self, request, pk):
        return _detail(get_object_or_404(schools(), pk=pk))

    def patch(self, request, pk):
        """Edit details, plan, overrides or dates. The slug never changes."""
        with transaction.atomic():
            school = get_object_or_404(schools().select_for_update(of=("self",)), pk=pk)
            serializer = UpdateSchoolSerializer(school, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            changes = _apply(school, serializer.validated_data)
            if changes:
                school.save(update_fields=list(changes))
                audit.record(request, "school_updated", school, changes=changes)
        return _detail(schools().get(pk=pk))


def _audit_value(value):
    if isinstance(value, Plan):
        return value.code
    if isinstance(value, date):
        return value.isoformat()
    return value


def _apply(obj, data):
    """Set the fields whose value changes; ``{field: [old, new]}`` for the audit."""
    changes = {}
    for field, value in data.items():
        old = getattr(obj, field)
        if old != value:
            changes[field] = [_audit_value(old), _audit_value(value)]
            setattr(obj, field, value)
    return changes


class SchoolRetryView(PlatformView):
    """Drop whatever the failed attempt left behind and provision again."""

    permission_classes = [IsPlatformOwner]

    def post(self, request, pk):
        with transaction.atomic():
            school = get_object_or_404(schools().select_for_update(of=("self",)), pk=pk)
            if school.status != School.Status.FAILED:
                raise _cannot("retry", school)
            # Only ever a schema that never went live: a failed school was
            # never active (spec 0003, child 0004, key invariants).
            school.drop_schema()
            previous_error = school.provisioning_error
            school.status = School.Status.PROVISIONING
            school.provisioning_started_at = timezone.now()
            school.provisioning_error = ""
            school.save(
                update_fields=["status", "provisioning_started_at", "provisioning_error"]
            )
            audit.record(
                request, "school_provisioning_retried", school, previous_error=previous_error
            )
            _queue_provisioning(school)
        return _detail(school, status.HTTP_202_ACCEPTED)


PURGE_GRACE_DAYS = 30


def _locked(pk):
    return get_object_or_404(schools().select_for_update(of=("self",)), pk=pk)


class _SuspendSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000)


class SchoolSuspendView(PlatformView):
    """Block the school's users; their data stays as it is."""

    permission_classes = [IsPlatformStaff]

    def post(self, request, pk):
        serializer = _SuspendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["reason"]
        with transaction.atomic():
            school = _locked(pk)
            if school.status != School.Status.ACTIVE:
                raise _cannot("suspend", school)
            school.status = School.Status.SUSPENDED
            school.suspended_at = timezone.now()
            school.suspended_reason = reason
            school.save(update_fields=["status", "suspended_at", "suspended_reason"])
            audit.record(request, "school_suspended", school, reason=reason)
        return _detail(schools().get(pk=pk))


class SchoolReactivateView(PlatformView):
    permission_classes = [IsPlatformStaff]

    def post(self, request, pk):
        with transaction.atomic():
            school = _locked(pk)
            if school.status != School.Status.SUSPENDED:
                raise _cannot("reactivate", school)
            reason = school.suspended_reason
            school.status = School.Status.ACTIVE
            school.suspended_at = None
            school.suspended_reason = ""
            school.save(update_fields=["status", "suspended_at", "suspended_reason"])
            audit.record(request, "school_reactivated", school, suspended_reason=reason)
        return _detail(schools().get(pk=pk))


class _DeleteSerializer(serializers.Serializer):
    confirm_slug = serializers.CharField()


class SchoolDeleteView(PlatformView):
    """Block the school, export it, and purge it once the grace period ends."""

    permission_classes = [IsPlatformOwner]

    def post(self, request, pk):
        serializer = _DeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            school = _locked(pk)
            if school.status not in (
                School.Status.ACTIVE,
                School.Status.SUSPENDED,
                School.Status.FAILED,
            ):
                raise _cannot("delete", school)
            if serializer.validated_data["confirm_slug"].strip() != school.slug:
                raise serializers.ValidationError(
                    {"confirm_slug": ["Type the school's address exactly to confirm."]}
                )
            now = timezone.now()
            school.status = School.Status.PENDING_DELETION
            school.deletion_requested_at = now
            school.purge_after = now + timedelta(days=PURGE_GRACE_DAYS)
            # A school that never went live holds no data worth keeping.
            school.final_export_ref = "" if school.was_ever_active else exports.NEVER_ACTIVE
            school.save(
                update_fields=[
                    "status", "deletion_requested_at", "purge_after", "final_export_ref"
                ]
            )
            audit.record(
                request, "school_deletion_requested", school,
                purge_after=school.purge_after.isoformat(),
            )
            if school.was_ever_active:
                school_id = school.pk
                transaction.on_commit(lambda: export_school.delay(school_id))
        return _detail(schools().get(pk=pk))


class SchoolRestoreView(PlatformView):
    """Undo a deletion before the purge. The export is no longer needed."""

    permission_classes = [IsPlatformOwner]

    def post(self, request, pk):
        with transaction.atomic():
            school = _locked(pk)
            if school.status != School.Status.PENDING_DELETION:
                raise _cannot("restore", school)
            if timezone.now() >= school.purge_after:
                raise Conflict("The grace period has ended; this school is being purged.")
            export_ref = school.final_export_ref
            # A school deleted before it ever went live has a half built
            # schema: back to failed, where Retry rebuilds it.
            school.status = (
                School.Status.ACTIVE if school.was_ever_active else School.Status.FAILED
            )
            school.deletion_requested_at = None
            school.purge_after = None
            school.final_export_ref = ""
            school.suspended_at = None
            school.suspended_reason = ""
            school.save(
                update_fields=[
                    "status", "deletion_requested_at", "purge_after",
                    "final_export_ref", "suspended_at", "suspended_reason",
                ]
            )
            audit.record(request, "school_restored", school, status=school.status)
            transaction.on_commit(lambda: _forget_export(school, export_ref))
        return _detail(schools().get(pk=pk))


def _forget_export(school, ref):
    try:
        exports.delete_export(ref)
    except Exception:
        logger.exception("Restored %s but could not delete its export %r", school.schema_name, ref)


class SchoolResendInviteView(PlatformView):
    """Email the first admin a new link; any earlier link stops working."""

    def post(self, request, pk):
        from ems import invites
        from ems.models import User

        with transaction.atomic():
            school = get_object_or_404(schools().select_for_update(of=("self",)), pk=pk)
            if school.status != School.Status.ACTIVE:
                raise Conflict(
                    "Invites can only be resent for an active school. This one is "
                    f"{school.get_status_display().lower()}."
                )
            with schema_context(school.schema_name):
                if invites.user_exists(school.first_admin_email):
                    raise Conflict("The first admin has already joined.")
                invites.invite(
                    school,
                    email=school.first_admin_email,
                    role=User.Role.SUPER_ADMIN,
                    name=school.first_admin_name,
                )
            audit.record(
                request, "school_invite_resent", school, email=school.first_admin_email
            )
        return Response(status=status.HTTP_202_ACCEPTED)


class _StartSupportSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000)


class SchoolSupportSessionsView(PlatformView):
    """Start a support session (a one time link), or list the school's sessions."""

    def get(self, request, pk):
        school = get_object_or_404(schools(), pk=pk)
        sessions = school.support_sessions.select_related("admin")[:50]
        return Response(SupportSessionSerializer(sessions, many=True).data)

    def post(self, request, pk):
        serializer = _StartSupportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            school = _locked(pk)
            session, url = support.start(
                request, school, serializer.validated_data["reason"]
            )
        return Response(
            {
                "session_id": session.pk,
                "url": url,
                "ticket_expires_at": session.ticket_expires_at,
            },
            status=status.HTTP_201_CREATED,
        )


class SupportSessionEndView(PlatformView):
    """End a session early: an Owner, or the admin who started it."""

    def post(self, request, pk):
        session = get_object_or_404(SupportSession, pk=pk)
        if not (request.user.is_owner or session.admin_id == request.user.pk):
            raise PermissionDenied("Only an Owner or the admin who started it can end it.")
        with transaction.atomic():
            support.end(request, session)
        return Response(status=status.HTTP_204_NO_CONTENT)


def plans():
    """Every plan with how many schools are on it (the public row isn't one)."""
    on_plan = (
        schools()
        .filter(plan=OuterRef("pk"))
        .order_by()
        .values("plan")
        .annotate(n=Count("pk"))
        .values("n")
    )
    return Plan.objects.annotate(school_count=Coalesce(Subquery(on_plan), 0))


class PlanListView(PlatformView):
    """Every plan, offered ones first. Plans are deactivated, never deleted."""

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsPlatformOwner()]
        return [IsPlatformStaff()]

    def get(self, request):
        return Response(PlanSerializer(plans().order_by("-is_active", "name"), many=True).data)

    def post(self, request):
        serializer = PlanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            plan = serializer.save()
            audit.record(request, "plan_created", code=plan.code, plan=serializer.data)
        return Response(
            PlanSerializer(plans().get(pk=plan.pk)).data, status=status.HTTP_201_CREATED
        )


class PlanDetailView(PlatformView):
    permission_classes = [IsPlatformOwner]

    def patch(self, request, pk):
        """Edit any field but the code; ``is_active`` false stops offering it."""
        with transaction.atomic():
            plan = get_object_or_404(Plan.objects.select_for_update(), pk=pk)
            serializer = PlanSerializer(plan, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            changes = _apply(plan, serializer.validated_data)
            if changes:
                plan.save(update_fields=list(changes))
                audit.record(request, "plan_updated", code=plan.code, changes=changes)
        return Response(PlanSerializer(plans().get(pk=pk)).data)


# How far ahead the dashboard warns about renewals, and how much usage
# history its trend covers.
RENEWAL_WARNING_DAYS = 30
TREND_DAYS = 30


def _school_ref(school):
    return {"id": school.id, "name": school.name, "slug": school.slug}


def _attention(today):
    """Schools an admin should look at, most urgent first.

    Failed setups, closures counting down to a purge, suspensions, and plans
    that lapsed or renew within ``RENEWAL_WARNING_DAYS``.
    """
    items = []
    for school in schools().filter(status=School.Status.FAILED):
        items.append({"school": _school_ref(school), "kind": "failed", "at": school.provisioning_started_at})
    for school in schools().filter(status=School.Status.PENDING_DELETION).order_by("purge_after"):
        items.append({"school": _school_ref(school), "kind": "pending_deletion", "at": school.purge_after})
    for school in schools().filter(status=School.Status.SUSPENDED).order_by("-suspended_at"):
        items.append({"school": _school_ref(school), "kind": "suspended", "at": school.suspended_at})
    renewing = schools().filter(
        status=School.Status.ACTIVE,
        renews_on__lte=today + timedelta(days=RENEWAL_WARNING_DAYS),
    ).order_by("renews_on")
    for school in renewing:
        kind = "renewal_overdue" if school.renews_on < today else "renewal_due"
        items.append({"school": _school_ref(school), "kind": kind, "at": school.renews_on})
    return items


class OverviewView(PlatformView):
    """The console dashboard, read from the public schema only.

    Usage totals add up each school's latest nightly snapshot; the trend adds
    up every school's snapshot per day. Neither enters a school's schema.
    """

    def get(self, request):
        now = timezone.now()
        today = now.date()
        everything = School.objects.exclude(schema_name=get_public_schema_name())

        by_status = dict.fromkeys(School.Status.values, 0)
        for row in everything.values("status").annotate(n=Count("id")):
            by_status[row["status"]] = row["n"]

        live = everything.filter(status__in=School.LIVE_STATUSES)
        latest = list(
            UsageSnapshot.objects.filter(school__in=live)
            .order_by("school_id", "-taken_on")
            .distinct("school_id")
        )
        totals = {
            "students": sum(u.students for u in latest),
            "staff_users": sum(u.staff_users for u in latest),
            "jobs_last_24h": sum(u.jobs_last_24h for u in latest),
            "as_of": max((u.taken_on for u in latest), default=None),
        }

        trend = (
            UsageSnapshot.objects.filter(taken_on__gt=today - timedelta(days=TREND_DAYS))
            .values("taken_on")
            .annotate(
                students=Sum("students"),
                staff_users=Sum("staff_users"),
                jobs=Sum("jobs_last_24h"),
            )
            .order_by("taken_on")
        )

        support = (
            SupportSession.objects.filter(
                started_at__isnull=False, ended_at__isnull=True, expires_at__gt=now
            )
            .select_related("admin", "school")
            .order_by("expires_at")
        )

        recent = PlatformAuditLog.objects.select_related("admin", "school")[:8]

        return Response(
            {
                "schools": {
                    "total": sum(by_status.values()) - by_status[School.Status.PURGED],
                    "by_status": by_status,
                    "new_last_30_days": everything.filter(
                        created_at__gte=now - timedelta(days=30)
                    ).count(),
                },
                "usage": totals,
                "trend": [
                    {
                        "date": row["taken_on"],
                        "students": row["students"],
                        "staff_users": row["staff_users"],
                        "jobs": row["jobs"],
                    }
                    for row in trend
                ],
                "plans": [
                    {"id": p.id, "code": p.code, "name": p.name, "schools": p.school_count}
                    for p in plans().filter(Q(is_active=True) | Q(school_count__gt=0)).order_by(
                        "-school_count", "name"
                    )
                ],
                "attention": _attention(today),
                "support": [
                    {
                        "id": s.id,
                        "school": _school_ref(s.school),
                        "admin": {"id": s.admin.id, "name": s.admin.name},
                        "reason": s.reason,
                        "expires_at": s.expires_at,
                    }
                    for s in support
                ],
                "recent_activity": PlatformAuditLogSerializer(recent, many=True).data,
                "backup": backup_summary(now),
            }
        )
