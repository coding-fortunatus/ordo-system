"""Threshold alert rules on any metric (spec 0006, child 0004; AC-17 to
AC-19).

``evaluate`` runs every 60 seconds. For each enabled rule it reads the rule's
``for_minutes`` closed minutes ending at M, the newest minute at least
``LAG`` old, through ``query.metric_series`` with one point a minute, and
judges each target (each live school, or the platform):

- no data: a minute in the window is missing (child 0002's zero and missing
  rules). Nothing about an open incident changes.
- holds: every minute breaks the threshold.
- clear: minute M does not.
- otherwise neither: the clear streak starts over.

An incident opens (one email) when a rule holds with none open, and
resolves (one email) after ``CLEAR_EVALUATIONS`` clear evaluations in a row.
State lives in tables, so a Redis restart can never repeat an email.
"""
import logging
import time
import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from django_tenants.utils import get_public_schema_name

from tenancy import alerts as platform_alerts
from tenancy.models import AlertIncident, AlertRule, AlertState, School
from tenancy.observability import buffer, counters, flush, metrics, query

logger = logging.getLogger("ordo.observability")

LAG = timedelta(minutes=2)
MINUTE = timedelta(minutes=1)
CLEAR_EVALUATIONS = 5
FOR_MINUTES_MAX = 120
PREVIEW_RANGE = timedelta(hours=6)
STALL_SECONDS = 5 * 60
STALL_COOLDOWN_SECONDS = 60 * 60
LOCK_KEY = "obs:alert:eval:lock"
LOCK_SECONDS = 120
PATH = "/observability/alerts"

AGGREGATIONS = {
    metrics.COUNTER: ("rate", "sum"),
    metrics.HISTOGRAM: ("rate", "sum", "avg", "p95", "max"),
    metrics.GAUGE: ("last", "max", "avg"),
}
# Changing any of these resolves the rule's open incidents silently.
MATERIAL = ("metric_name", "aggregation", "filters", "comparison", "threshold", "for_minutes", "scope")

NO_DATA, HOLDS, CLEAR, BETWEEN = "no_data", "holds", "clear", "between"


class RuleError(ValueError):
    """A rule the registry or child 0004 doesn't allow, by field."""

    def __init__(self, field, message):
        super().__init__(message)
        self.field = field


def check(metric_name, aggregation, filters, scope):
    """The declared metric, or ``RuleError`` (AC-17)."""
    metric = metrics.lookup(metric_name)
    if metric is None:
        raise RuleError("metric_name", f"No metric is declared as {metric_name!r}.")
    allowed = AGGREGATIONS[metric.type]
    if aggregation not in allowed:
        raise RuleError("aggregation", f"{metric_name} is a {metric.type}: use {', '.join(allowed)}.")
    if scope == AlertRule.Scope.PER_SCHOOL and metric.scope == metrics.PLATFORM:
        raise RuleError("scope", "This metric has no per school values.")
    try:
        query.validate(metric_name, aggregation, filters=filters)
    except query.QueryError as exc:
        raise RuleError("filters", str(exc))
    return metric


def live_schools():
    return list(
        School.objects.filter(status__in=School.LIVE_STATUSES)
        .exclude(schema_name=get_public_schema_name())
        .values_list("pk", flat=True)
    )


def breaks(rule, value):
    if value is None:
        return False
    return value > rule.threshold if rule.comparison == AlertRule.Comparison.GT else value < rule.threshold


def judge(rule, window):
    """The verdict on ``window``, ``[(minute, value, missing)]`` oldest first."""
    if any(missing for _, _, missing in window):
        return NO_DATA
    if all(breaks(rule, value) for _, value, _ in window):
        return HOLDS
    if not breaks(rule, window[-1][1]):
        return CLEAR
    return BETWEEN


def worse(rule, a, b):
    return max(a, b) if rule.comparison == AlertRule.Comparison.GT else min(a, b)


def read(rule, start, end, schools):
    """``{school id or None: [(minute, value, missing)]}`` for every target
    of ``rule``, one entry a minute from ``start`` to ``end``."""
    metric = metrics.lookup(rule.metric_name)
    per_school = rule.scope == AlertRule.Scope.PER_SCHOOL
    result = query.metric_series(
        rule.metric_name,
        rule.aggregation,
        filters=rule.filters or None,
        group_by="school" if per_school else None,
        scope="platform",
        start=start,
        end=end,
        max_series=None,
    )
    missing = set(result["missing"])
    grid = range(int(start.timestamp()), int(end.timestamp()), 60)
    found = {line["labels"].get("school"): dict(line["points"]) for line in result["series"]}
    gauge = metric.type == metrics.GAUGE
    # A school with no rows at all: a count reads 0 where the minute isn't
    # missing; a p95 or average has nothing to read; a gauge is missing.
    absent = 0.0 if rule.aggregation in ("rate", "sum") and not gauge else None
    targets = schools if per_school else [None]
    out = {}
    for target in targets:
        values = found.get(target)
        out[target] = [
            (
                t,
                values.get(t) if values is not None else absent,
                t in missing or (gauge and (values is None or values.get(t) is None)),
            )
            for t in grid
        ]
    return out


def _target_name(school_id, names):
    return names.get(school_id, f"School {school_id}") if school_id else "Platform"


def _format(metric, value):
    if value is None:
        return "no data"
    unit = {"1": "", "%": "%", "By": " bytes"}.get(metric.unit, f" {metric.unit}")
    return f"{value:,.4g}{unit}"


def describe(rule):
    metric = metrics.lookup(rule.metric_name)
    filters = ", ".join(f"{k}={v}" for k, v in sorted((rule.filters or {}).items()))
    sign = ">" if rule.comparison == AlertRule.Comparison.GT else "<"
    threshold = _format(metric, rule.threshold) if metric else rule.threshold
    return (
        f"{rule.aggregation} of {rule.metric_name}{f' ({filters})' if filters else ''} "
        f"{sign} {threshold} for {rule.for_minutes} min"
    )


def _fired_email(rule, target, value, at):
    metric = metrics.lookup(rule.metric_name)
    platform_alerts.send(
        f"Alert: {rule.name} for {target}",
        [
            f"Rule: {describe(rule)}",
            f"Value: {_format(metric, value)} at {at:%H:%M} UTC.",
            f"It has held for every minute of the last {rule.for_minutes}.",
        ],
        PATH,
    )


def _resolved_email(rule, target, incident, at):
    metric = metrics.lookup(rule.metric_name)
    minutes = max(1, round((at - incident.started_at).total_seconds() / 60))
    platform_alerts.send(
        f"Resolved: {rule.name} for {target}",
        [
            f"Rule: {describe(rule)}",
            f"Now: {_format(metric, incident.last_value)}. "
            f"Worst while open: {_format(metric, incident.peak_value)}.",
            f"Open for about {minutes} minute{'s' if minutes != 1 else ''}, "
            f"clear for the last {CLEAR_EVALUATIONS} checks.",
        ],
        PATH,
    )


def step(rule, school_id, verdict, value, now, target):
    """Advance one target's state and incident by one evaluation."""
    with transaction.atomic():
        state, _ = AlertState.objects.select_for_update().get_or_create(
            rule=rule, school_id=school_id, defaults={"state": AlertState.State.OK, "evaluated_at": now}
        )
        incident = (
            AlertIncident.objects.select_for_update()
            .filter(rule=rule, school_id=school_id, resolved_at__isnull=True)
            .first()
        )
        state.last_value = value
        state.evaluated_at = now
        if verdict == NO_DATA:
            state.state = AlertState.State.NO_DATA
        elif verdict == HOLDS:
            state.clear_streak = 0
            state.state = AlertState.State.FIRING
            if incident:
                incident.last_value = value
                incident.peak_value = worse(rule, incident.peak_value, value)
                incident.save(update_fields=["last_value", "peak_value"])
            else:
                try:
                    with transaction.atomic():
                        AlertIncident.objects.create(
                            rule=rule,
                            school_id=school_id,
                            started_at=now,
                            peak_value=value,
                            last_value=value,
                            notified_at=now,
                        )
                except IntegrityError:
                    # Another evaluation opened it a moment ago.
                    pass
                else:
                    _fired_email(rule, target, value, now)
        elif verdict == CLEAR and incident:
            incident.last_value = value if value is not None else incident.last_value
            state.clear_streak += 1
            state.state = AlertState.State.FIRING
            if state.clear_streak >= CLEAR_EVALUATIONS:
                incident.resolved_at = now
                incident.resolved_notified_at = now
                state.state = AlertState.State.OK
                state.clear_streak = 0
                _resolved_email(rule, target, incident, now)
            incident.save(update_fields=["last_value", "resolved_at", "resolved_notified_at"])
        else:
            # Clear with nothing open, or breaking now but not for the whole
            # window.
            state.clear_streak = 0
            state.state = AlertState.State.FIRING if incident else AlertState.State.OK
        state.save()


def evaluate_rule(rule, newest, schools, names, now):
    start = newest - (rule.for_minutes - 1) * MINUTE
    targets = read(rule, start, newest + MINUTE, schools)
    for school_id, window in targets.items():
        verdict = judge(rule, window)
        step(rule, school_id, verdict, window[-1][1], now, _target_name(school_id, names))
    # Schools no longer live, or a scope that changed outside the console.
    stale = AlertState.objects.filter(rule=rule)
    if rule.scope == AlertRule.Scope.PER_SCHOOL:
        stale.exclude(school_id__in=list(targets)).delete()
    else:
        stale.filter(school__isnull=False).delete()


def evaluate(now=None):
    """Every enabled rule, once (AC-18, AC-19). One evaluation at a time,
    under a lock on the telemetry Redis; with that Redis down nothing is
    flushed either, so the rules wait and only the stalled email goes out."""
    now = now or timezone.now()
    check_flush(now)
    token = uuid.uuid4().hex
    if not buffer.run(lambda redis: redis.set(LOCK_KEY, token, nx=True, ex=LOCK_SECONDS)):
        return
    try:
        newest = now.replace(second=0, microsecond=0) - LAG
        schools = live_schools()
        names = dict(School.objects.filter(pk__in=schools).values_list("pk", "name"))
        for rule in AlertRule.objects.filter(enabled=True):
            if metrics.lookup(rule.metric_name) is None:
                logger.warning("Alert rule %s names an undeclared metric %s", rule.pk, rule.metric_name)
                continue
            try:
                evaluate_rule(rule, newest, schools, names, now)
            except Exception:
                logger.exception("Evaluating alert rule %s failed", rule.pk)
    finally:

        def release(redis):
            if redis.get(LOCK_KEY) == token:
                redis.delete(LOCK_KEY)

        buffer.run(release)


def stall_key(reason):
    return f"obs:alert:cool:flush:{reason}"


def check_flush(now):
    """Email once an hour while no flush has succeeded for ``STALL_SECONDS``,
    or while the telemetry Redis can't be reached (AC-19). The cooldown
    lives on the spec 0004 counters Redis, apart from the telemetry one."""
    answer = buffer.run(lambda redis: ("ok", redis.get(flush.LAST_OK_KEY)))
    if answer is None:
        reason = "redis"
        lines = ["The telemetry Redis can't be reached, so no telemetry is being stored."]
    else:
        last = answer[1]
        if last and now.timestamp() - int(last) <= STALL_SECONDS:
            return
        reason = "stalled"
        since = f"since {time.strftime('%H:%M', time.gmtime(int(last)))} UTC" if last else "since it was set up"
        lines = [f"No telemetry flush has succeeded {since}."]
    if not counters.run(lambda redis: redis.set(stall_key(reason), "1", nx=True, ex=STALL_COOLDOWN_SECONDS)):
        return
    platform_alerts.send(
        "Telemetry flush has stopped",
        lines
        + [
            "Metrics, logs and traces are not reaching the console, and alert rules see no data.",
            "Check the observability worker and redis-obs.",
        ],
        "/observability",
    )


def reset(rule, now=None):
    """Resolve the rule's open incidents without an email and forget its
    states, after a change that makes them meaningless."""
    AlertIncident.objects.filter(rule=rule, resolved_at__isnull=True).update(resolved_at=now or timezone.now())
    AlertState.objects.filter(rule=rule).delete()


def preview(rule, now=None):
    """The last ``PREVIEW_RANGE`` of an unsaved rule: its series (the 20
    busiest targets) and the minutes it would have opened an incident,
    replaying the same verdicts and clear streak as ``evaluate``."""
    now = now or timezone.now()
    end = now.replace(second=0, microsecond=0) - LAG + MINUTE
    start = end - PREVIEW_RANGE
    schools = live_schools()
    fired = []
    for school_id, series in read(rule, start, end, schools).items():
        open_, streak = False, 0
        for i in range(rule.for_minutes - 1, len(series)):
            verdict = judge(rule, series[i + 1 - rule.for_minutes : i + 1])
            if verdict == HOLDS:
                streak = 0
                if not open_:
                    open_ = True
                    fired.append({"school": school_id, "minute": series[i][0], "value": series[i][1]})
            elif verdict == CLEAR and open_:
                streak += 1
                if streak >= CLEAR_EVALUATIONS:
                    open_, streak = False, 0
            elif verdict == BETWEEN:
                streak = 0
    chart = query.metric_series(
        rule.metric_name,
        rule.aggregation,
        filters=rule.filters or None,
        group_by="school" if rule.scope == AlertRule.Scope.PER_SCHOOL else None,
        scope="platform",
        start=start,
        end=end,
    )
    fired.sort(key=lambda f: f["minute"])
    return {**chart, "fired": fired}
