"""Metric panels in the console (spec 0006, child 0005, Fixed panel
endpoints). Platform staff only, under ``/api/platform/observability/`` on
the console host. Each panel is one ``metric_series()`` call with fixed
arguments; a response is cached in the spec 0004 counters Redis for
``CACHE_SECONDS`` per argument set, so several admins on the overview don't
repeat the same aggregates against the product database.

Times are cut to the whole minute, which also lets the cache serve admins
who opened the page a few seconds apart.
"""
import hashlib
import json
from datetime import timedelta

from django.core.serializers.json import DjangoJSONEncoder
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from tenancy.observability import counters, metrics, query
from tenancy.observability.telemetry_views import school_refs, time_range
from tenancy.views import PlatformView

CACHE_SECONDS = 30
DEFAULT_RANGE = timedelta(hours=24)
MAX_RANGE = timedelta(days=90)
ROUTES_MAX = 20
TOP_SCHOOLS_MAX = 10

REQUESTS = {"metric": "http.server.requests", "aggregation": "rate"}
# The 5xx share of all requests.
ERROR_RATE = {
    "metric": "http.server.requests",
    "aggregation": "sum",
    "mode": "ratio",
    "filters": {"status_class": "5xx"},
    "denominator_filters": {},
}
LATENCY = {"metric": "http.server.duration", "aggregation": "p95"}

# ``platform`` panels read platform metrics, whatever school is picked.
OVERVIEW_PANELS = {
    "requests": REQUESTS,
    "error_rate": ERROR_RATE,
    "latency_p95": LATENCY,
    "queue_depth": {"metric": "celery.queue.depth", "aggregation": "last", "group_by": "queue", "platform": True},
    "queue_wait_p95": {"metric": "celery.queue.wait", "aggregation": "p95", "group_by": "queue"},
    "cpu": {"metric": "host.cpu.percent", "aggregation": "last", "platform": True},
    "memory": {"metric": "host.memory.percent", "aggregation": "last", "platform": True},
    "disk_used": {"metric": "host.disk.used_bytes", "aggregation": "last", "group_by": "path", "platform": True},
    "disk_free": {"metric": "host.disk.free_bytes", "aggregation": "last", "group_by": "path", "platform": True},
    "db_size": {"metric": "db.database.bytes", "aggregation": "last", "platform": True},
}

SCHOOL_PANELS = {
    "requests": REQUESTS,
    "error_rate": ERROR_RATE,
    "latency_p95": LATENCY,
    "job_duration_p95": {"metric": "celery.task.duration", "aggregation": "p95"},
    "job_failures": {"metric": "celery.task.failures", "aggregation": "sum", "group_by": "task"},
    "runs": {"metric": "ordo.timetable.runs", "aggregation": "sum", "group_by": "outcome"},
    "run_duration_p95": {"metric": "ordo.timetable.duration", "aggregation": "p95", "group_by": "kind"},
    "syncs": {"metric": "ordo.portal_sync.runs", "aggregation": "sum", "group_by": "outcome"},
    "sync_records": {"metric": "ordo.portal_sync.records", "aggregation": "sum", "group_by": "outcome"},
    "logins": {"metric": "ordo.auth.logins", "aggregation": "sum", "group_by": "outcome"},
    "daily_active": {"metric": "ordo.users.daily_active", "aggregation": "last"},
    "uploads": {"metric": "ordo.uploads", "aggregation": "sum", "group_by": "kind"},
    "upload_bytes": {"metric": "ordo.uploads.bytes", "aggregation": "sum"},
    "exports": {"metric": "ordo.exports", "aggregation": "sum", "group_by": "kind"},
    "export_bytes": {"metric": "ordo.exports.bytes", "aggregation": "sum"},
    "schema_bytes": {"metric": "db.schema.bytes", "aggregation": "last"},
    "media_bytes": {"metric": "media.bytes", "aggregation": "last"},
}


def _minute(at):
    return at.replace(second=0, microsecond=0)


def _range(params):
    start, end = time_range(params, DEFAULT_RANGE, MAX_RANGE)
    start, end = _minute(start), _minute(end)
    if start >= end:
        raise ValidationError({"start": ["The range must be at least a minute."]})
    return start, end


def _school(params):
    """The picked school's id, or None for all schools."""
    value = params.get("school", "")
    if not value:
        return None
    if not value.isdigit():
        raise ValidationError({"school": ["Give a school id."]})
    from tenancy.school_views import schools

    return get_object_or_404(schools(), pk=int(value)).pk


def cached(name, args, build):
    """``build()``, or the same answer given in the last ``CACHE_SECONDS``."""
    digest = hashlib.sha1(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()
    key = f"obs:panel:{name}:{digest}"
    hit = counters.run(lambda redis: redis.get(key))
    if hit:
        return json.loads(hit)
    # Through JSON either way, so a cached answer reads like a fresh one.
    text = json.dumps(build(), cls=DjangoJSONEncoder)
    counters.run(lambda redis: redis.set(key, text, ex=CACHE_SECONDS))
    return json.loads(text)


def panel(spec, school_id, start, end):
    spec = dict(spec)
    platform_only = spec.pop("platform", False)
    metric = metrics.lookup(spec["metric"])
    scope = {"scope": "platform"} if platform_only or school_id is None else {"school_id": school_id}
    result = query.metric_series(
        spec.pop("metric"), spec.pop("aggregation"), start=start, end=end, **scope, **spec
    )
    return {"metric": metric.name, "unit": metric.unit, "type": metric.type, **result}


def panels(specs, school_id, start, end):
    built = {name: panel(spec, school_id, start, end) for name, spec in specs.items()}
    return {"start": start, "end": end, "school": school_id, "panels": built}


class OverviewView(PlatformView):
    """The platform panels, for all schools or one (AC-21)."""

    def get(self, request):
        start, end = _range(request.query_params)
        school_id = _school(request.query_params)
        return Response(
            cached("overview", [school_id, start, end], lambda: panels(OVERVIEW_PANELS, school_id, start, end))
        )


class SchoolOverviewView(PlatformView):
    """One school's panels and business metrics (AC-21)."""

    def get(self, request, pk):
        from tenancy.school_views import schools

        school = get_object_or_404(schools(), pk=pk)
        start, end = _range(request.query_params)
        return Response(
            cached("school", [school.pk, start, end], lambda: panels(SCHOOL_PANELS, school.pk, start, end))
        )


def slowest_routes(school_id, start, end):
    scope = {"school_id": school_id} if school_id is not None else {"scope": "platform"}
    window = {"start": start, "end": end, "mode": "totals", "group_by": "route", **scope}
    p95 = query.metric_series("http.server.duration", "p95", **window)
    counts = {r["labels"]["route"]: r["value"] for r in query.metric_series("http.server.requests", "sum", **window)}
    rows = [
        {"route": r["labels"]["route"], "p95_ms": r["value"], "requests": int(counts.get(r["labels"]["route"]) or 0)}
        for r in p95
        if r["value"] is not None
    ]
    return {"start": start, "end": end, "results": rows[:ROUTES_MAX]}


def top_schools(start, end):
    rows = query.metric_series(
        "http.server.requests", "sum", start=start, end=end, mode="totals", group_by="school", scope="platform"
    )
    rows = [r for r in rows if r["labels"]["school"] is not None and r["value"]][:TOP_SCHOOLS_MAX]
    refs = school_refs(r["labels"]["school"] for r in rows)
    return {
        "start": start,
        "end": end,
        "results": [
            {"school": refs.get(r["labels"]["school"]), "requests": int(r["value"])}
            for r in rows
            if r["labels"]["school"] in refs
        ],
    }


class RoutesView(PlatformView):
    """Slowest routes by p95, with their request counts (AC-21)."""

    def get(self, request):
        start, end = _range(request.query_params)
        school_id = _school(request.query_params)
        return Response(
            cached("routes", [school_id, start, end], lambda: slowest_routes(school_id, start, end))
        )


class TopSchoolsView(PlatformView):
    """Schools with the most requests (AC-21)."""

    def get(self, request):
        start, end = _range(request.query_params)
        return Response(cached("top-schools", [start, end], lambda: top_schools(start, end)))


def registry():
    """Every declared metric, as the explorer and the rule editor pick them."""
    out = []
    for m in sorted(metrics.REGISTRY.values(), key=lambda m: m.name):
        labels = {name: (list(m.allowed(name)) if m.allowed(name) is not None else None) for name in sorted(m.labels)}
        out.append(
            {
                "name": m.name,
                "type": m.type,
                "unit": m.unit,
                "scope": m.scope,
                "description": m.description,
                "labels": labels,
                "aggregations": list(query.AGGREGATIONS[m.type]),
                "group_by": (["school"] if m.scope == metrics.SCHOOL else []) + sorted(m.labels),
            }
        )
    return out


class RegistryView(PlatformView):
    """The declared metrics (AC-24)."""

    def get(self, request):
        return Response({"results": registry()})


def explore(metric, aggregation, *, group_by, filters, school_id, start, end, mode):
    """One explorer chart or table: ``metric_series`` with the metric's unit,
    and school names when grouped by school. A platform metric ignores the
    picked school."""
    m = query.validate(metric, aggregation, group_by, filters, mode)
    scope = {"school_id": school_id} if school_id is not None and m.scope == metrics.SCHOOL else {"scope": "platform"}
    result = query.metric_series(
        metric, aggregation, group_by=group_by or None, filters=filters or None, start=start, end=end, mode=mode, **scope
    )
    body = {"metric": m.name, "unit": m.unit, "type": m.type, "start": start, "end": end}
    if mode == "totals":
        body["results"] = result
        lines = result
    else:
        body.update(result)
        lines = result["series"]
    if group_by == "school":
        refs = school_refs(line["labels"].get("school") for line in lines if isinstance(line["labels"].get("school"), int))
        body["schools"] = {str(k): v for k, v in refs.items()}
    return body


class MetricQueryView(PlatformView):
    """Any declared metric, aggregation, group and filters (AC-24). The body
    is checked against the registry; the range is at most 90 days."""

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        params = {k: str(data[k]) for k in ("start", "end", "school") if data.get(k) not in (None, "")}
        start, end = _range(params)
        school_id = _school(params)
        filters = data.get("filters") or {}
        mode = data.get("mode") or "series"
        if not isinstance(filters, dict) or not all(isinstance(v, str) for v in filters.values()):
            raise ValidationError({"filters": ["Give label names and values."]})
        if mode not in ("series", "totals"):
            raise ValidationError({"mode": ["Mode is series or totals."]})
        try:
            body = explore(
                str(data.get("metric") or ""),
                str(data.get("aggregation") or ""),
                group_by=str(data.get("group_by") or ""),
                filters=filters,
                school_id=school_id,
                start=start,
                end=end,
                mode=mode,
            )
        except query.QueryError as exc:
            raise ValidationError({"metric": [str(exc)]})
        return Response(body)
