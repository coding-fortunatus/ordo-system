"""Reading metrics (spec 0006, child 0002, Query). ``metric_series`` is the
only reader of points and rollups: the console panels, the explorer, the
dashboards and the alert rules all call it.

Ranges up to 48 hours read minute points, longer ones hourly rollups. Time
buckets are whole steps (1 minute up to 6 hours, 5 minutes up to 48 hours,
1 hour beyond), aligned to the epoch, so the first one may start before
``start``.

Zero and missing (AC-11): a counter or histogram bucket reads as 0 when it
ends at or before the watermark and none of its minutes had metric drops;
otherwise it is missing (``None``). A gauge carries its last value forward
for its declared interval, then is missing.
"""
import json
from datetime import timedelta

from django.db import connection

from tenancy.observability import metric_store, metrics

POINTS_MAX = timedelta(hours=48)
MINUTE_STEPS_MAX = timedelta(hours=6)
MAX_SERIES = 20
OTHER = "other"

AGGREGATIONS = {
    metrics.COUNTER: ("rate", "sum"),
    metrics.HISTOGRAM: ("rate", "sum", "avg", "p50", "p95", "p99", "max"),
    metrics.GAUGE: ("last", "max", "avg"),
}
PERCENTILES = {"p50": 0.5, "p95": 0.95, "p99": 0.99}
MODES = ("series", "ratio", "totals")


class QueryError(ValueError):
    """A query the registry doesn't allow."""


def step_seconds(start, end):
    span = end - start
    if span <= MINUTE_STEPS_MAX:
        return 60
    if span <= POINTS_MAX:
        return 300
    return 3600


def percentile(buckets, count, low, high, q):
    """Linear interpolation inside the merged bucket holding rank ``q``. The
    first bucket starts at the merged min and the overflow ends at the
    merged max; every edge is kept within them."""
    if not count or not buckets:
        return None
    rank = q * count
    seen = 0
    for i, n in enumerate(buckets):
        if not n:
            continue
        if seen + n >= rank:
            lower = metrics.BOUNDS[i - 1] if i > 0 else (low if low is not None else 0)
            upper = metrics.BOUNDS[i] if i < len(metrics.BOUNDS) else (high if high is not None else lower)
            if low is not None:
                lower = max(lower, low)
            if high is not None:
                upper = min(upper, high)
            upper = max(upper, lower)
            return lower + (upper - lower) * (rank - seen) / n
        seen += n
    return high


def validate(metric, aggregation, group_by=None, filters=None, mode="series"):
    """The declared metric, or ``QueryError`` when the registry doesn't allow
    this query."""
    m = metrics.lookup(metric)
    if m is None:
        raise QueryError(f"No metric is declared as {metric!r}.")
    if aggregation not in AGGREGATIONS[m.type]:
        raise QueryError(f"{metric} is a {m.type}: use {', '.join(AGGREGATIONS[m.type])}.")
    if mode not in MODES:
        raise QueryError(f"Mode is one of {', '.join(MODES)}.")
    if group_by and group_by != "school" and group_by not in m.labels:
        raise QueryError(f"{metric} can be grouped by school or {', '.join(sorted(m.labels)) or 'nothing else'}.")
    for name, value in (filters or {}).items():
        if name not in m.labels:
            raise QueryError(f"{metric} has no label {name!r}.")
        allowed = m.allowed(name)
        if allowed is not None and value not in allowed:
            raise QueryError(f"{value!r} is not a value of {name}.")
    return m


class _Source:
    def __init__(self, start, end):
        self.points = end - start <= POINTS_MAX
        self.table = "public.obs_metric_point" if self.points else "tenancy_metricrollup"
        self.time = "minute" if self.points else "hour"


def _where(m, filters, school_id, scope, params):
    clauses = ["s.metric_name = %(name)s", "s.labels @> %(filters)s::jsonb"]
    params["name"] = m.name
    params["filters"] = json.dumps(filters or {})
    if school_id is not None:
        clauses.append("s.school_id = %(school)s")
        params["school"] = school_id
    elif scope != "platform":
        clauses.append("s.school_id IS NULL")
    return " AND ".join(clauses)


def _group_expr(group_by, params):
    if not group_by:
        return "''"
    if group_by == "school":
        return "COALESCE(s.school_id::text, '')"
    params["group"] = group_by
    return "COALESCE(s.labels ->> %(group)s, '')"


def _fetch(sql, params):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def _aggregates(m, source, filters, school_id, scope, group_by, start, end, step, with_buckets):
    """``{(group, t): {count, sum, min, max, buckets}}`` for counters and
    histograms."""
    params = {"start": start, "end": end, "step": step}
    where = _where(m, filters, school_id, scope, params)
    group = _group_expr(group_by, params)
    base = f"""
        FROM {source.table} p JOIN tenancy_metricseries s ON s.id = p.series_id
        WHERE {where} AND p.{source.time} >= %(start)s AND p.{source.time} < %(end)s
    """
    bucket = f"(FLOOR(EXTRACT(EPOCH FROM p.{source.time}) / %(step)s) * %(step)s)::bigint"
    out = {}
    for g, t, count, total, low, high in _fetch(
        f"SELECT {group}, {bucket}, SUM(p.count), SUM(p.sum), MIN(p.min), MAX(p.max) {base} GROUP BY 1, 2",
        params,
    ):
        out[(g, t)] = {"count": int(count), "sum": float(total), "min": low, "max": high, "buckets": None}
    if with_buckets:
        rows = _fetch(
            f"""SELECT {group}, {bucket}, u.i, SUM(u.n)
                FROM {source.table} p JOIN tenancy_metricseries s ON s.id = p.series_id,
                     unnest(p.buckets) WITH ORDINALITY AS u(n, i)
                WHERE {where} AND p.{source.time} >= %(start)s AND p.{source.time} < %(end)s
                GROUP BY 1, 2, 3""",
            params,
        )
        for g, t, i, n in rows:
            entry = out.get((g, t))
            if entry is None:
                continue
            if entry["buckets"] is None:
                entry["buckets"] = [0] * metrics.BUCKETS
            entry["buckets"][int(i) - 1] += int(n)
    return out


def _either(pick, a, b):
    if a is None or b is None:
        return b if a is None else a
    return pick(a, b)


def _merge(into, value):
    if into is None:
        return {**value, "buckets": list(value["buckets"]) if value["buckets"] else None}
    into["count"] += value["count"]
    into["sum"] += value["sum"]
    into["min"] = _either(min, into["min"], value["min"])
    into["max"] = _either(max, into["max"], value["max"])
    if value["buckets"]:
        if into["buckets"] is None:
            into["buckets"] = [0] * metrics.BUCKETS
        into["buckets"] = [a + b for a, b in zip(into["buckets"], value["buckets"])]
    return into


def _value(m, aggregation, agg, seconds):
    """One number from merged values, or None when there is nothing to read."""
    if agg is None:
        agg = {"count": 0, "sum": 0.0, "min": None, "max": None, "buckets": None}
    if aggregation == "rate":
        return (agg["sum"] if m.type == metrics.COUNTER else agg["count"]) / seconds
    if aggregation == "sum":
        return agg["sum"]
    if not agg["count"]:
        return None
    if aggregation == "avg":
        return agg["sum"] / agg["count"]
    if aggregation == "max":
        return agg["max"]
    return percentile(agg["buckets"], agg["count"], agg["min"], agg["max"], PERCENTILES[aggregation])


def _missing_buckets(m, source, grid, step, start, end):
    """Buckets that read as missing: past the watermark, or with drops
    (except for the drop counts themselves)."""
    if source.points:
        mark = metric_store.watermark(metric_store.METRICS)
        # The bucket's last minute must be at or before the watermark.
        last_complete = mark.timestamp() + 60 - step if mark else None
    else:
        mark = metric_store.watermark(metric_store.ROLLUPS)
        last_complete = mark.timestamp() - step if mark else None
    missing = {t for t in grid if last_complete is None or t > last_complete}
    if m is metrics.DROPPED:
        return missing
    return missing | _drop_buckets(source, step, start, end, {"signal": "metric"})


def _drop_buckets(source, step, start, end, filters=None):
    """Buckets in which ``obs.dropped`` counted anything (of ``filters``)."""
    params = {"start": start, "end": end, "step": step}
    where = _where(metrics.DROPPED, filters, None, "platform", params)
    return {
        t
        for (t,) in _fetch(
            f"""SELECT DISTINCT (FLOOR(EXTRACT(EPOCH FROM p.{source.time}) / %(step)s) * %(step)s)::bigint
                FROM {source.table} p JOIN tenancy_metricseries s ON s.id = p.series_id
                WHERE {where} AND p.{source.time} >= %(start)s AND p.{source.time} < %(end)s""",
            params,
        )
    }


def _gauge_values(m, aggregation, source, filters, school_id, scope, group_by, start, end, step, grid):
    """``{group: {t: value}}``: each series' value per bucket, carried
    forward within the gauge's interval, summed per group."""
    interval = m.interval.total_seconds()
    params = {"start": start - m.interval, "end": end, "step": step}
    where = _where(m, filters, school_id, scope, params)
    group = _group_expr(group_by, params)
    rows = _fetch(
        f"""SELECT s.id, {group},
                   (FLOOR(EXTRACT(EPOCH FROM p.{source.time}) / %(step)s) * %(step)s)::bigint,
                   (array_agg(p.last ORDER BY p.{source.time} DESC) FILTER (WHERE p.last IS NOT NULL))[1],
                   EXTRACT(EPOCH FROM MAX(p.{source.time})), MAX(p.max), SUM(p.sum), SUM(p.count)
            FROM {source.table} p JOIN tenancy_metricseries s ON s.id = p.series_id
            WHERE {where} AND p.{source.time} >= %(start)s AND p.{source.time} < %(end)s
            GROUP BY 1, 2, 3 ORDER BY 1, 3""",
        params,
    )
    per_series = {}
    for series_id, g, t, last, at, high, total, count in rows:
        per_series.setdefault((series_id, g), []).append((t, last, float(at), high, total, count))
    out = {}
    for (_, g), buckets in per_series.items():
        values = out.setdefault(g, {})
        by_t = {b[0]: b for b in buckets}
        carried = None
        earlier = [b for b in buckets if b[0] < grid[0]] if grid else []
        if earlier:
            carried = (earlier[-1][1], earlier[-1][2])
        for t in grid:
            b = by_t.get(t)
            if b is not None:
                if aggregation == "max":
                    value = b[3]
                elif aggregation == "avg":
                    value = b[4] / b[5] if b[5] else None
                else:
                    value = b[1]
                if b[1] is not None:
                    carried = (b[1], b[2])
            elif carried is not None and t < carried[1] + interval:
                value = carried[0]
            else:
                value = None
            if value is not None:
                values[t] = values.get(t, 0.0) + float(value)
    return out


def _labels(group_by, g):
    if not group_by:
        return {}
    if g == OTHER:
        return {group_by: OTHER}
    if group_by == "school":
        return {"school": int(g) if g else None}
    return {group_by: g}


def _top(groups, total, max_series):
    """The groups to show on their own; the rest are folded into ``other``.
    ``max_series`` None keeps every group."""
    if max_series is None or len(groups) <= max_series:
        return groups, []
    ranked = sorted(groups, key=lambda g: total(g) or 0, reverse=True)
    return ranked[: max_series - 1], ranked[max_series - 1 :]


def _series(m, aggregation, *, filters, school_id, scope, group_by, start, end, max_series):
    step = step_seconds(start, end)
    source = _Source(start, end)
    first = int(start.timestamp()) // step * step
    grid = list(range(first, int(end.timestamp()), step)) or [first]
    start = start.__class__.fromtimestamp(first, tz=start.tzinfo)

    if m.type == metrics.GAUGE:
        values = _gauge_values(m, aggregation, source, filters, school_id, scope, group_by, start, end, step, grid)
        if not group_by and not values:
            values = {"": {}}
        shown, rest = _top(list(values), lambda g: max(values[g].values(), default=0), max_series)
        result = [{"labels": _labels(group_by, g), "points": [[t, values[g].get(t)] for t in grid]} for g in shown]
        if rest:
            other = {}
            for g in rest:
                for t, v in values[g].items():
                    other[t] = other.get(t, 0.0) + v
            result.append({"labels": _labels(group_by, OTHER), "points": [[t, other.get(t)] for t in grid]})
        # A gauge with no value in reach is missing: its None says so.
        return step, result, set()

    aggs = _aggregates(
        m, source, filters, school_id, scope, group_by, start, end, step, aggregation in PERCENTILES
    )
    missing = _missing_buckets(m, source, grid, step, start, end)
    groups = sorted({g for g, _ in aggs}) or ([""] if not group_by else [])
    totals = {}
    for (g, _), value in aggs.items():
        totals[g] = totals.get(g, 0.0) + (value["sum"] if m.type == metrics.COUNTER else value["count"])
    shown, rest = _top(groups, totals.get, max_series)
    by_group = {g: {t: aggs.get((g, t)) for t in grid} for g in shown}
    if rest:
        other = {}
        for g in rest:
            for t in grid:
                value = aggs.get((g, t))
                if value is not None:
                    other[t] = _merge(other.get(t), value)
        by_group[OTHER] = other
    result = []
    for g, per_t in by_group.items():
        points = [
            [t, None if t in missing else _value(m, aggregation, per_t.get(t), step)] for t in grid
        ]
        result.append({"labels": _labels(group_by, g), "points": points})
    return step, result, missing


def _totals(m, aggregation, *, filters, school_id, scope, group_by, start, end):
    seconds = (end - start).total_seconds()
    source = _Source(start, end)
    if m.type == metrics.GAUGE:
        step = int(seconds) + 1
        grid = [int(start.timestamp()) // step * step]
        values = _gauge_values(m, aggregation, source, filters, school_id, scope, group_by, start, end, step, grid)
        rows = [{"labels": _labels(group_by, g), "value": v.get(grid[0])} for g, v in values.items()]
    else:
        whole = int(seconds) + 1
        aggs = _aggregates(
            m, source, filters, school_id, scope, group_by, start, end, whole, aggregation in PERCENTILES
        )
        merged = {}
        for (g, _), value in aggs.items():
            merged[g] = _merge(merged.get(g), value)
        rows = [{"labels": _labels(group_by, g), "value": _value(m, aggregation, v, seconds)} for g, v in merged.items()]
    return sorted(rows, key=lambda r: (r["value"] is None, -(r["value"] or 0)))


def metric_series(
    metric,
    aggregation,
    *,
    group_by=None,
    filters=None,
    school_id=None,
    scope="school",
    start,
    end,
    mode="series",
    denominator_filters=None,
    max_series=MAX_SERIES,
):
    """See the module docstring. ``scope="platform"`` adds up every school
    (and the platform's own values); ``school_id`` keeps one school; neither
    reads the platform's own series only.

    Series and ratio results list the ``missing`` buckets of a counter or
    histogram, so a caller can tell missing from a minute with nothing to
    read (a p95 with no requests); a gauge's None is always missing. They
    also list the ``dropped`` buckets, in which any telemetry was dropped,
    which every chart shades (AC-4).
    ``max_series`` None returns every group (the alert rules)."""
    if start >= end:
        raise QueryError("Start must be before end.")
    m = validate(metric, aggregation, group_by, filters, mode)
    if mode == "ratio":
        validate(metric, aggregation, None, denominator_filters, mode)
    options = {"school_id": school_id, "scope": scope, "start": start, "end": end}
    if mode == "totals":
        return _totals(m, aggregation, filters=filters, group_by=group_by, **options)
    options["max_series"] = max_series
    if mode == "ratio":
        group_by = None
    step, series, missing = _series(m, aggregation, filters=filters, group_by=group_by, **options)
    if mode == "ratio":
        _, below, _ = _series(m, aggregation, filters=denominator_filters, group_by=None, **options)
        above = series[0]["points"] if series else []
        denominators = dict((t, v) for t, v in below[0]["points"]) if below else {}
        points = []
        for t, v in above:
            d = denominators.get(t)
            points.append([t, None if v is None or not d else v / d])
        series = [{"labels": {}, "points": points}]
    first = int(start.timestamp()) // step * step
    dropped = _drop_buckets(_Source(start, end), step, start.__class__.fromtimestamp(first, tz=start.tzinfo), end)
    return {"step": step, "series": series, "missing": sorted(missing), "dropped": sorted(dropped)}
