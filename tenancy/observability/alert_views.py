"""Alert rules and incidents in the console (spec 0006, child 0004
Endpoints; AC-17, AC-20). Every admin may read them; only an Owner may
create, change, delete or preview a rule, and each of those writes is
audited.

Incidents list open ones first, then the rest newest first, paged with a
cursor over (open, started, id).
"""
import base64
import json
import math
from datetime import datetime

from django.db import transaction
from django.db.models import BooleanField, ExpressionWrapper, Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from tenancy import audit
from tenancy.models import AlertIncident, AlertRule, AlertState
from tenancy.observability import alert_rules, metrics
from tenancy.observability.dashboard_views import admin_ref
from tenancy.observability.telemetry_views import school_refs
from tenancy.permissions import IsPlatformOwner, IsPlatformStaff
from tenancy.views import PlatformView

DETAIL_INCIDENTS = 50
INCIDENT_LIMIT = 50


class AlertRuleSerializer(serializers.ModelSerializer):
    filters = serializers.DictField(child=serializers.CharField(), required=False)
    for_minutes = serializers.IntegerField(min_value=1, max_value=alert_rules.FOR_MINUTES_MAX)

    class Meta:
        model = AlertRule
        fields = [
            "id",
            "name",
            "metric_name",
            "aggregation",
            "filters",
            "comparison",
            "threshold",
            "for_minutes",
            "scope",
            "enabled",
        ]
        read_only_fields = ["id"]

    def validate_threshold(self, value):
        if not math.isfinite(value):
            raise serializers.ValidationError("Give a number.")
        return value

    def validate(self, attrs):
        # A partial edit is checked as the whole rule it makes.
        merged = {f: getattr(self.instance, f) for f in alert_rules.MATERIAL} if self.instance else {}
        merged.update(attrs)
        try:
            alert_rules.check(
                merged["metric_name"], merged["aggregation"], merged.get("filters") or {}, merged["scope"]
            )
        except alert_rules.RuleError as exc:
            raise serializers.ValidationError({exc.field: [str(exc)]})
        return attrs


def _state_rows(states, refs):
    return [
        {
            "school": refs.get(s.school_id) if s.school_id else None,
            "state": s.state,
            "last_value": s.last_value,
            "clear_streak": s.clear_streak,
            "evaluated_at": s.evaluated_at,
        }
        for s in states
    ]


def _incident_rows(incidents, refs):
    return [
        {
            "id": i.pk,
            "rule": {"id": i.rule_id, "name": i.rule.name, "metric_name": i.rule.metric_name},
            "unit": getattr(metrics.lookup(i.rule.metric_name), "unit", ""),
            "school": refs.get(i.school_id) if i.school_id else None,
            "started_at": i.started_at,
            "resolved_at": i.resolved_at,
            "peak_value": i.peak_value,
            "last_value": i.last_value,
        }
        for i in incidents
    ]


def _rule_row(rule, states, open_count, refs):
    metric = metrics.lookup(rule.metric_name)
    return {
        **AlertRuleSerializer(rule).data,
        "unit": metric.unit if metric else "",
        "description": alert_rules.describe(rule),
        "created_by": admin_ref(rule.created_by),
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
        "states": _state_rows(states, refs),
        "open_incidents": open_count,
    }


def _rules(queryset):
    rules = list(queryset.select_related("created_by"))
    states = list(AlertState.objects.filter(rule__in=rules).order_by("school_id"))
    open_ = list(AlertIncident.objects.filter(rule__in=rules, resolved_at__isnull=True).values_list("rule_id", flat=True))
    refs = school_refs(s.school_id for s in states)
    by_rule = {}
    for s in states:
        by_rule.setdefault(s.rule_id, []).append(s)
    return [_rule_row(r, by_rule.get(r.pk, []), open_.count(r.pk), refs) for r in rules]


def _changes(rule, data):
    """Set the fields that change; ``{field: [old, new]}`` for the audit."""
    changes = {}
    for field, value in data.items():
        old = getattr(rule, field)
        if old != value:
            changes[field] = [old, value]
            setattr(rule, field, value)
    return changes


class AlertRuleListView(PlatformView):
    def get_permissions(self):
        if self.request.method == "POST":
            return [IsPlatformOwner()]
        return [IsPlatformStaff()]

    def get(self, request):
        return Response({"results": _rules(AlertRule.objects.all())})

    def post(self, request):
        serializer = AlertRuleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            rule = AlertRule.objects.create(**serializer.validated_data, created_by=request.user)
            audit.record(
                request,
                "observability.rule.create",
                id=rule.pk,
                name=rule.name,
                fields={k: v for k, v in serializer.validated_data.items()},
            )
        return Response(_rules(AlertRule.objects.filter(pk=rule.pk))[0], status=status.HTTP_201_CREATED)


class AlertRuleDetailView(PlatformView):
    def get_permissions(self):
        if self.request.method in ("PATCH", "DELETE"):
            return [IsPlatformOwner()]
        return [IsPlatformStaff()]

    def get(self, request, pk):
        get_object_or_404(AlertRule, pk=pk)
        body = _rules(AlertRule.objects.filter(pk=pk))[0]
        incidents = list(
            AlertIncident.objects.filter(rule_id=pk).select_related("rule").order_by("-started_at", "-id")[
                :DETAIL_INCIDENTS
            ]
        )
        body["incidents"] = _incident_rows(incidents, school_refs(i.school_id for i in incidents))
        return Response(body)

    def patch(self, request, pk):
        with transaction.atomic():
            rule = get_object_or_404(AlertRule.objects.select_for_update(), pk=pk)
            serializer = AlertRuleSerializer(rule, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            changes = _changes(rule, serializer.validated_data)
            if changes:
                rule.save()
                # A different rule, or one switched off: what it found no
                # longer stands. A new name changes nothing else.
                if set(changes) & set(alert_rules.MATERIAL) or not rule.enabled:
                    alert_rules.reset(rule)
                audit.record(request, "observability.rule.update", id=rule.pk, name=rule.name, changes=changes)
        return Response(_rules(AlertRule.objects.filter(pk=pk))[0])

    def delete(self, request, pk):
        rule = get_object_or_404(AlertRule, pk=pk)
        with transaction.atomic():
            audit.record(request, "observability.rule.delete", id=rule.pk, name=rule.name)
            # Its states and incidents go with it, so nothing is emailed.
            rule.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AlertRulePreviewView(PlatformView):
    """An unsaved rule over the last 6 hours, and when it would have fired."""

    permission_classes = [IsPlatformOwner]

    def post(self, request):
        serializer = AlertRuleSerializer(data={"name": "Preview", **request.data})
        serializer.is_valid(raise_exception=True)
        data = {k: v for k, v in serializer.validated_data.items() if k != "enabled"}
        result = alert_rules.preview(AlertRule(**data))
        refs = school_refs(
            [f["school"] for f in result["fired"]]
            + [line["labels"].get("school") for line in result["series"] if isinstance(line["labels"].get("school"), int)]
        )
        result["schools"] = {str(k): v for k, v in refs.items()}
        result["unit"] = metrics.lookup(data["metric_name"]).unit
        return Response(result)


def _encode(open_, at, key):
    raw = json.dumps({"o": open_, "t": at.isoformat(), "k": key})
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode(value):
    try:
        padded = value + "=" * (-len(value) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return bool(data["o"]), datetime.fromisoformat(data["t"]), int(data["k"])
    except (ValueError, KeyError, TypeError):
        raise ValidationError({"cursor": ["This cursor isn't valid."]})


class IncidentListView(PlatformView):
    """Open incidents first, then recent ones; by school and rule."""

    def get(self, request):
        params = request.query_params
        rows = AlertIncident.objects.select_related("rule").annotate(
            is_open=ExpressionWrapper(Q(resolved_at__isnull=True), output_field=BooleanField())
        )
        school = params.get("school", "")
        if school == "platform":
            rows = rows.filter(school__isnull=True)
        elif school:
            if not school.isdigit():
                raise ValidationError({"school": ['Give a school id or "platform".']})
            rows = rows.filter(school_id=int(school))
        if rule := params.get("rule", ""):
            if not rule.isdigit():
                raise ValidationError({"rule": ["Give a rule id."]})
            rows = rows.filter(rule_id=int(rule))
        if cursor := params.get("cursor"):
            open_, at, key = _decode(cursor)
            after = Q(is_open=open_, started_at__lt=at) | Q(is_open=open_, started_at=at, id__lt=key)
            if open_:
                after |= Q(is_open=False)
            rows = rows.filter(after)
        page = list(rows.order_by("-is_open", "-started_at", "-id")[: INCIDENT_LIMIT + 1])
        more = len(page) > INCIDENT_LIMIT
        page = page[:INCIDENT_LIMIT]
        last = page[-1] if page else None
        return Response(
            {
                "results": _incident_rows(page, school_refs(i.school_id for i in page)),
                "next": _encode(last.is_open, last.started_at, last.pk) if more and last else None,
            }
        )
