"""Traces in the console (spec 0006, child 0003 read endpoints). Platform
staff only, under ``/api/platform/observability/`` on the console host.

Lists page with opaque cursors over (time, id), newest first, so a page
stays stable while new rows arrive. Live log search polls with ``after``,
a cursor over the log id: ids come from a sequence at flush time, so a line
flushed late (logged before lines already seen) is still picked up.
"""
import base64
import json
import re
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from tenancy.models import ObsLog, ObsSegment, ObsSpan, School
from tenancy.views import PlatformView

DEFAULT_RANGE = timedelta(hours=1)
LIST_LIMIT = 50
LIST_LIMIT_MAX = 100
TRACE_PREFIX_MIN = 8
LOG_LIMIT = 100
LOG_LIMIT_MAX = 200
TRACE_LOGS_MAX = 500
SEARCH_MIN = 3
LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}
STATUS_CLASSES = {"2xx": (200, 300), "4xx": (400, 500), "5xx": (500, 600)}
KINDS = ("http", "job", "ws")
_HEX = re.compile(r"^[0-9a-f]+$")
_ID = re.compile(r"^[0-9a-f]{32}$")

SEGMENT_FIELDS = (
    "root_span_id",
    "trace_id",
    "started_at",
    "school_id",
    "kind",
    "root_name",
    "route",
    "method",
    "status_code",
    "duration_ms",
    "is_error",
    "is_slow",
    "span_count",
    "truncated",
    "user_id",
    "request_id",
    "release",
    "process_role",
)
SPAN_FIELDS = (
    "span_id",
    "parent_span_id",
    "root_span_id",
    "started_at",
    "school_id",
    "name",
    "kind",
    "duration_ms",
    "status",
    "attributes",
)
LOG_FIELDS = (
    "id",
    "logged_at",
    "school_id",
    "level",
    "logger",
    "message",
    "trace_id",
    "span_id",
    "request_id",
    "user_id",
    "attributes",
)


def retained_window(keep_days=None):
    """Every day still stored: retention plus today."""
    return timedelta(days=(settings.OBS_TRACE_KEEP_DAYS if keep_days is None else keep_days) + 1)


def _bad(field, message):
    return ValidationError({field: [message]})


def _time(params, name):
    value = params.get(name)
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise _bad(name, "Give an ISO 8601 time.")
    if timezone.is_naive(parsed):
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


def time_range(params, default, maximum):
    """``(start, end)`` from ``start`` and ``end``, else the last ``default``."""
    end = _time(params, "end") or timezone.now()
    start = _time(params, "start") or end - default
    if start >= end:
        raise _bad("start", "Start must be before end.")
    if end - start > maximum:
        raise _bad("start", f"The range can be at most {maximum.days} days.")
    return start, end


def school_filter(params):
    """A Q for the ``school`` param (an id or "platform"), or None for all.
    An unknown school is a 404."""
    value = params.get("school", "")
    if not value:
        return None
    if value == "platform":
        return Q(school_id__isnull=True)
    if not value.isdigit():
        raise _bad("school", 'Give a school id or "platform".')
    from tenancy.school_views import schools

    get_object_or_404(schools(), pk=int(value))
    return Q(school_id=int(value))


def _int(params, name, minimum=0, maximum=None):
    value = params.get(name)
    if value in (None, ""):
        return None
    if not value.isdigit() or int(value) < minimum:
        raise _bad(name, "Give a whole number.")
    return min(int(value), maximum) if maximum else int(value)


def encode_after(log_id):
    return base64.urlsafe_b64encode(json.dumps({"a": log_id}).encode()).decode().rstrip("=")


def decode_after(value):
    try:
        padded = value + "=" * (-len(value) % 4)
        log_id = json.loads(base64.urlsafe_b64decode(padded.encode()))["a"]
        if not isinstance(log_id, int):
            raise ValueError
        return log_id
    except (ValueError, KeyError, TypeError):
        raise _bad("after", "This cursor isn't valid.")


def encode_cursor(at, key, direction):
    raw = json.dumps({"t": at.isoformat(), "k": key, "d": direction})
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(value):
    try:
        padded = value + "=" * (-len(value) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        at = datetime.fromisoformat(data["t"])
        if data["d"] not in ("next", "previous"):
            raise ValueError
        return at, data["k"], data["d"]
    except (ValueError, KeyError, TypeError):
        raise _bad("cursor", "This cursor isn't valid.")


def cursor_page(rows, params, time_field, key_field, limit):
    """One page of ``rows`` (a queryset) newest first, with ``next`` and
    ``previous`` cursors."""
    cursor = params.get("cursor")
    direction = "next"
    if cursor:
        at, key, direction = decode_cursor(cursor)
        if direction == "next":
            rows = rows.filter(Q(**{f"{time_field}__lt": at}) | Q(**{time_field: at, f"{key_field}__lt": key}))
        else:
            rows = rows.filter(Q(**{f"{time_field}__gt": at}) | Q(**{time_field: at, f"{key_field}__gt": key}))
    if direction == "next":
        page = list(rows.order_by(f"-{time_field}", f"-{key_field}")[: limit + 1])
        more_after, more_before = len(page) > limit, bool(cursor)
        page = page[:limit]
    else:
        page = list(rows.order_by(time_field, key_field)[: limit + 1])
        more_before, more_after = len(page) > limit, True
        page = list(reversed(page[:limit]))
    first, last = (page[0], page[-1]) if page else (None, None)
    return {
        "page": page,
        "next": encode_cursor(last[time_field], last[key_field], "next") if page and more_after else None,
        "previous": encode_cursor(first[time_field], first[key_field], "previous")
        if page and more_before
        else None,
    }


def school_refs(ids):
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {
        s["id"]: s
        for s in School.objects.filter(pk__in=ids).values("id", "name", "slug")
    }


def _with_school(rows):
    refs = school_refs(r["school_id"] for r in rows)
    return [{**r, "school": refs.get(r["school_id"])} for r in rows]


class TraceListView(PlatformView):
    """Stored segments, newest first (AC-22)."""

    def get(self, request):
        params = request.query_params
        prefix = params.get("trace", "").lower()
        if prefix and (len(prefix) < TRACE_PREFIX_MIN or len(prefix) > 32 or not _HEX.match(prefix)):
            raise _bad("trace", f"Give at least {TRACE_PREFIX_MIN} characters of a trace id.")
        window = retained_window()
        start, end = time_range(params, window if prefix else DEFAULT_RANGE, window)
        rows = ObsSegment.objects.filter(started_at__gte=start, started_at__lt=end)
        school = school_filter(params)
        if school is not None:
            rows = rows.filter(school)
        if prefix:
            rows = rows.filter(trace_id__startswith=prefix)
        kind = params.get("kind")
        if kind:
            if kind not in KINDS:
                raise _bad("kind", "Give http, job or ws.")
            rows = rows.filter(kind=kind)
        route = params.get("route")
        if route:
            rows = rows.filter(route=route)
        status = params.get("status")
        if status:
            if status not in STATUS_CLASSES:
                raise _bad("status", "Give 2xx, 4xx or 5xx.")
            low, high = STATUS_CLASSES[status]
            rows = rows.filter(status_code__gte=low, status_code__lt=high)
        if params.get("errors") in ("1", "true"):
            rows = rows.filter(is_error=True)
        min_ms = _int(params, "min_ms")
        if min_ms:
            rows = rows.filter(duration_ms__gte=min_ms)
        limit = _int(params, "limit", minimum=1, maximum=LIST_LIMIT_MAX) or LIST_LIMIT
        result = cursor_page(rows.values(*SEGMENT_FIELDS), params, "started_at", "root_span_id", limit)
        return Response(
            {"results": _with_school(result["page"]), "next": result["next"], "previous": result["previous"]}
        )


class TraceDetailView(PlatformView):
    """Every stored segment of one trace, all its spans, and its logs."""

    def get(self, request, trace_id):
        trace_id = trace_id.lower()
        if len(trace_id) != 32 or not _HEX.match(trace_id):
            raise NotFound()
        segments = list(
            ObsSegment.objects.filter(trace_id=trace_id).order_by("started_at").values(*SEGMENT_FIELDS)
        )
        if not segments:
            raise NotFound("This trace isn't stored. It may not have been sampled, or it has expired.")
        spans = list(
            ObsSpan.objects.filter(trace_id=trace_id).order_by("started_at", "span_id").values(*SPAN_FIELDS)
        )
        stored = {s["span_id"] for s in spans}
        missing = sorted({s["parent_span_id"] for s in spans if s["parent_span_id"] and s["parent_span_id"] not in stored})
        logs = list(
            ObsLog.objects.filter(trace_id=trace_id).order_by("logged_at", "id").values(*LOG_FIELDS)[:TRACE_LOGS_MAX]
        )
        return Response(
            {
                "trace_id": trace_id,
                "segments": _with_school(segments),
                "spans": spans,
                "logs": _with_school(logs),
                "missing_parents": missing,
            }
        )


def _level(params):
    value = params.get("level", "").lower()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    if value not in LEVELS:
        raise _bad("level", "Give debug, info, warning, error or critical.")
    return LEVELS[value]


def _id(params, name, label):
    value = params.get(name, "").lower()
    if value and not _ID.match(value):
        raise _bad(name, f"Give a whole {label} (32 characters).")
    return value


def _like(text):
    """``text`` as an ILIKE pattern matching it anywhere, taken literally."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class LogListView(PlatformView):
    """Stored log lines, newest first (AC-23). With ``after``, only lines
    flushed since, for live polling."""

    def get(self, request):
        params = request.query_params
        trace_id = _id(params, "trace", "trace id")
        request_id = _id(params, "request", "request id")
        window = retained_window(settings.OBS_LOG_KEEP_DAYS)
        # A trace or request names its lines: look across every day kept.
        start, end = time_range(params, window if trace_id or request_id else DEFAULT_RANGE, window)
        rows = ObsLog.objects.filter(logged_at__gte=start, logged_at__lt=end)
        school = school_filter(params)
        if school is not None:
            rows = rows.filter(school)
        level = _level(params)
        if level:
            rows = rows.filter(level__gte=level)
        logger = params.get("logger", "")
        if logger:
            rows = rows.filter(logger__startswith=logger)
        if trace_id:
            rows = rows.filter(trace_id=trace_id)
        if request_id:
            rows = rows.filter(request_id=request_id)
        user = _int(params, "user")
        if user is not None:
            rows = rows.filter(user_id=user)
        text = params.get("q", "").strip()
        if text:
            if len(text) < SEARCH_MIN:
                raise _bad("q", f"Search for at least {SEARCH_MIN} characters.")
            # ILIKE, not Django's UPPER() LIKE, so the trigram index is used.
            rows = rows.extra(where=["obs_log.message ILIKE %s"], params=[_like(text)])
        limit = _int(params, "limit", minimum=1, maximum=LOG_LIMIT_MAX) or LOG_LIMIT

        after = params.get("after")
        if after:
            newer = list(rows.filter(id__gt=decode_after(after)).order_by("id").values(*LOG_FIELDS)[:limit])
            page = sorted(newer, key=lambda r: (r["logged_at"], r["id"]), reverse=True)
            result = {"page": page, "next": None, "previous": None}
            latest = max((r["id"] for r in newer), default=decode_after(after))
        else:
            result = cursor_page(rows.values(*LOG_FIELDS), params, "logged_at", "id", limit)
            latest = max((r["id"] for r in result["page"]), default=0)
        page = result["page"]
        return Response(
            {
                "results": _with_traces(_with_school(page)),
                "next": result["next"],
                "previous": result["previous"],
                "latest": encode_after(latest),
            }
        )


def _with_traces(rows):
    """Each line marked with whether its trace was kept (logs are not
    sampled, traces are)."""
    ids = {r["trace_id"] for r in rows if r["trace_id"]}
    kept = set()
    if ids:
        since = timezone.now() - retained_window()
        kept = set(
            ObsSegment.objects.filter(trace_id__in=ids, started_at__gte=since).values_list("trace_id", flat=True)
        )
    return [{**r, "trace_stored": r["trace_id"] in kept} for r in rows]
