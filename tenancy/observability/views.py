"""Observability in the console (``/api/platform/``, platform staff only), and
the browser error and Web Vitals ingest endpoints on every host (spec 0004,
API surface; spec 0006, child 0005).
"""
import json
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_tenants.utils import get_public_schema_name
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from ems.api.pagination import DefaultPagination
from tenancy import audit
from tenancy.models import ErrorEvent, ErrorGroup, SchoolHealth
from tenancy.observability import capture, context, counters, health, metrics
from tenancy.views import PlatformView

COUNT_FIELDS = ("server_errors", "client_errors", "ws_errors", "js_errors", "job_failures", "requests")


def _school_ref(school):
    if school is None:
        return None
    return {"id": school.pk, "name": school.name, "slug": school.slug, "status": school.status}


def _health_fields(row):
    """``health``, ``reason``, counts and ``computed_at``; nulls before the
    first ``observe_health`` run."""
    return {
        "health": row.status if row else None,
        "reason": row.reason if row else "",
        **{f: getattr(row, f) if row else 0 for f in COUNT_FIELDS},
        "computed_at": row.computed_at if row else None,
    }


def _paginate(view, request, rows):
    paginator = DefaultPagination()
    page = paginator.paginate_queryset(rows, request, view=view)
    return paginator.get_paginated_response(page)


class HealthListView(PlatformView):
    """Every counted school plus Platform, worst first (AC-14)."""

    def get(self, request):
        healths = {h.school_id: h for h in SchoolHealth.objects.all()}
        open_groups = health.open_group_counts()
        rows = [
            {"school": _school_ref(school), **_health_fields(healths.get(school.pk)),
             "open_groups": open_groups.get(school.pk, 0)}
            for school in health.counted_schools().order_by("name")
        ]
        rows.insert(
            0,
            {"school": None, **_health_fields(healths.get(None)), "open_groups": open_groups.get(None, 0)},
        )
        # Not yet computed sorts after healthy.
        rows.sort(key=lambda r: (health.SEVERITY.get(r["health"], 3), -r["server_errors"]))
        return _paginate(self, request, rows)


def _health_detail(school_id):
    row = SchoolHealth.objects.filter(school_id=school_id).first()
    return {**_health_fields(row), "buckets": health.hourly_series(school_id)}


class SchoolHealthView(PlatformView):
    def get(self, request, pk):
        from tenancy.school_views import schools

        school = get_object_or_404(schools(), pk=pk)
        return Response(_health_detail(school.pk))


class PlatformHealthView(PlatformView):
    def get(self, request):
        return Response(_health_detail(None))


class ErrorGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = ErrorGroup
        fields = [
            "id", "title", "culprit", "exc_type", "source", "kind", "status",
            "count", "first_seen", "last_seen", "last_release",
        ]


class ErrorGroupDetailSerializer(ErrorGroupSerializer):
    school = serializers.SerializerMethodField()
    resolved_by = serializers.SerializerMethodField()

    class Meta(ErrorGroupSerializer.Meta):
        fields = ErrorGroupSerializer.Meta.fields + [
            "school", "last_opened_at", "resolved_at", "resolved_by",
        ]

    def get_school(self, obj):
        return _school_ref(obj.school)

    def get_resolved_by(self, obj):
        return obj.resolved_by.name if obj.resolved_by else None


class ErrorEventSerializer(serializers.ModelSerializer):
    # Whether the event's trace and logs are still stored (spec 0006): an
    # error's trace is always kept, until retention removes it.
    trace_kept = serializers.SerializerMethodField()
    logs_kept = serializers.SerializerMethodField()

    class Meta:
        model = ErrorEvent
        fields = ["id", "occurred_at", "message", "stack", "context", "release", "trace_id", "trace_kept", "logs_kept"]

    def _kept(self, obj, days):
        return bool(obj.trace_id) and obj.occurred_at >= timezone.now() - timedelta(days=days)

    def get_trace_kept(self, obj):
        return self._kept(obj, settings.OBS_TRACE_KEEP_DAYS)

    def get_logs_kept(self, obj):
        return self._kept(obj, settings.OBS_LOG_KEEP_DAYS)


def _choice(params, name, choices):
    value = params.get(name, "")
    if value and value not in choices:
        raise ValidationError({name: [f"Must be one of: {', '.join(choices)}."]})
    return value


class ErrorGroupListView(PlatformView):
    """A school's (or Platform's) error groups, newest ``last_seen`` first."""

    def get(self, request):
        params = request.query_params
        school = params.get("school", "")
        if school == "platform":
            groups = ErrorGroup.objects.filter(school__isnull=True)
        elif school.isdigit():
            groups = ErrorGroup.objects.filter(school_id=int(school))
        else:
            raise ValidationError({"school": ["Give a school id or \"platform\"."]})
        for name, field in (("status", ErrorGroup.Status), ("source", ErrorGroup.Source), ("kind", ErrorGroup.Kind)):
            value = _choice(params, name, field.values)
            if value:
                groups = groups.filter(**{name: value})
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(groups.order_by("-last_seen", "-id"), request, view=self)
        return paginator.get_paginated_response(ErrorGroupSerializer(page, many=True).data)


def _group(pk):
    return get_object_or_404(
        ErrorGroup.objects.select_related("school", "resolved_by"), pk=pk
    )


class ErrorGroupDetailView(PlatformView):
    def get(self, request, pk):
        return Response(ErrorGroupDetailSerializer(_group(pk)).data)


class ErrorEventListView(PlatformView):
    def get(self, request, pk):
        group = _group(pk)
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(
            group.events.order_by("-occurred_at", "-id"), request, view=self
        )
        return paginator.get_paginated_response(ErrorEventSerializer(page, many=True).data)


STATUS_ACTIONS = {
    ErrorGroup.Status.OPEN: "error_group.reopen",
    ErrorGroup.Status.RESOLVED: "error_group.resolve",
    ErrorGroup.Status.IGNORED: "error_group.ignore",
}


class ErrorGroupStatusView(PlatformView):
    """Owner or Support sets a group open, resolved or ignored (AC-16)."""

    def post(self, request, pk):
        status = request.data.get("status")
        if status not in STATUS_ACTIONS:
            raise ValidationError({"status": ["Must be one of: open, resolved, ignored."]})
        with transaction.atomic():
            group = get_object_or_404(ErrorGroup.objects.select_for_update(), pk=pk)
            if group.status != status:
                group.status = status
                group.resolved_by = request.user
                group.resolved_at = timezone.now() if status == ErrorGroup.Status.RESOLVED else None
                group.save(update_fields=["status", "resolved_by", "resolved_at"])
                audit.record(request, STATUS_ACTIONS[status], group.school, group_id=group.pk)
        return Response(ErrorGroupDetailSerializer(_group(pk)).data)


# Browser error ingest (AC-5).

INGEST_MAX_BYTES = 16 * 1024
STACK_MAX_BYTES = 8 * 1024
TEXT_MAX = 2000
THROTTLE_TTL = 120


def ingest_ip(request):
    """The leftmost ``X-Forwarded-For`` entry, else the socket peer
    (spec 0004, Value sourcing)."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    first = forwarded.split(",")[0].strip()
    return first or request.META.get("REMOTE_ADDR") or "unknown"


def _token_user(request):
    """The signed in user if a valid token came along, else None."""
    from rest_framework.exceptions import AuthenticationFailed
    from rest_framework.request import Request

    tenant = getattr(request, "tenant", None)
    on_school = tenant is not None and tenant.schema_name != get_public_schema_name()
    if on_school:
        from ems.api.auth import SchoolTokenAuthentication as auth_class
    else:
        from tenancy.auth import PlatformTokenAuthentication as auth_class
    try:
        result = auth_class().authenticate(Request(request))
    except AuthenticationFailed:
        return None
    return result[0] if result else None


def _parse(body):
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if not all(isinstance(data.get(name), str) for name in ("name", "message", "path")):
        return None
    if not data["name"].strip() or not data["path"].strip():
        return None
    for name in ("stack", "release"):
        if data.get(name) is not None and not isinstance(data[name], str):
            return None
    if len((data.get("stack") or "").encode()) > STACK_MAX_BYTES:
        return None
    return data


def _throttled(request, kind="js", ip_limit=None, school_limit=None):
    """Over the per IP or per school limit. With Redis down nothing can be
    counted, so reports are dropped rather than let through unlimited."""
    minute = counters.minute_stamp(timezone.now())
    school = counters.school_key(capture.current_school_id())
    by_ip = counters.hit(f"obs:{kind}:ip:{ingest_ip(request)}:{minute}", THROTTLE_TTL)
    by_school = counters.hit(f"obs:{kind}:school:{school}:{minute}", THROTTLE_TTL)
    if by_ip is None or by_school is None:
        return True
    if ip_limit is None:
        ip_limit, school_limit = settings.OBS_JS_IP_PER_MIN, settings.OBS_JS_SCHOOL_PER_MIN
    return by_ip > ip_limit or by_school > school_limit


@csrf_exempt
@require_POST
def ingest_js_error(request):
    """``POST /api/telemetry/errors/``: no token needed, nothing read back."""
    length = request.META.get("CONTENT_LENGTH") or "0"
    if (length.isdigit() and int(length) > INGEST_MAX_BYTES) or len(request.body) > INGEST_MAX_BYTES:
        return JsonResponse({"detail": "Report too large."}, status=413)
    data = _parse(request.body)
    if data is None:
        return JsonResponse({"detail": "Malformed error report."}, status=400)
    if not capture.enabled() or _throttled(request):
        return HttpResponse(status=204)

    user = _token_user(request)
    page = data["path"].split("?")[0].split("#")[0]
    capture.capture_js(
        data["name"][:255],
        data["message"][:TEXT_MAX],
        data.get("stack") or "",
        page,
        data.get("release") or "",
        user_id=user.pk if user else None,
        role=getattr(user, "role", None) if user else None,
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
    )
    return HttpResponse(status=204)


# Web Vitals ingest (spec 0006, AC-26).

VITALS_MAX_BYTES = 4 * 1024
# Highest accepted value per vital: milliseconds for LCP and INP, the
# unitless layout shift score for CLS.
VITAL_LIMITS = {"LCP": 60000, "INP": 60000, "CLS": 10}
PAGE_MAX = 255
# Distinct pages a school reports in one UTC day; vitals for further new
# pages are dropped, so a bad route pattern can't flood the series table.
VITALS_PAGES_PER_DAY = 100
PAGES_TTL = 26 * 60 * 60


def _parse_vital(body):
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    name, value, page = data.get("name"), data.get("value"), data.get("page")
    if name not in VITAL_LIMITS or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 <= value <= VITAL_LIMITS[name]:
        return None
    if not isinstance(page, str) or not page.startswith("/") or len(page) > PAGE_MAX:
        return None
    return name, float(value), page


def _page_allowed(school_id, page):
    """Within the school's distinct pages for today. With Redis down the
    page can't be checked, so it's dropped (as when throttled)."""
    day = timezone.now().strftime("%Y%m%d")
    name = f"obs:vitals:pages:{counters.school_key(school_id)}:{day}"

    def check(redis):
        pipe = redis.pipeline(transaction=False)
        pipe.sismember(name, page)
        pipe.scard(name)
        known, count = pipe.execute()
        if known:
            return True
        if int(count) >= VITALS_PAGES_PER_DAY:
            return False
        pipe = redis.pipeline(transaction=False)
        pipe.sadd(name, page)
        pipe.expire(name, PAGES_TTL)
        pipe.execute()
        return True

    return bool(counters.run(check))


@csrf_exempt
@require_POST
def ingest_vitals(request):
    """``POST /api/telemetry/vitals/``: one Web Vital from ``sendBeacon``. No
    token, and the body is read as JSON whatever its content type (a beacon
    sends ``text/plain``)."""
    length = request.META.get("CONTENT_LENGTH") or "0"
    if (length.isdigit() and int(length) > VITALS_MAX_BYTES) or len(request.body) > VITALS_MAX_BYTES:
        return JsonResponse({"detail": "Report too large."}, status=413)
    vital = _parse_vital(request.body)
    if vital is None:
        return JsonResponse({"detail": "Malformed vitals report."}, status=400)
    if not metrics.metrics_enabled():
        return HttpResponse(status=204)
    if _throttled(request, "vitals", settings.OBS_VITALS_IP_PER_MIN, settings.OBS_VITALS_SCHOOL_PER_MIN):
        return HttpResponse(status=204)
    name, value, page = vital
    school_id = capture.current_school_id()
    if not _page_allowed(school_id, page):
        return HttpResponse(status=204)
    with context.for_school(school_id):
        # CLS is stored times 1000, so the histogram's buckets resolve it.
        metrics.BROWSER_VITALS.record(value * 1000 if name == "CLS" else value, name=name, page=page)
    return HttpResponse(status=204)
