"""Shared dashboards of saved explorer charts (spec 0006, child 0005; AC-24,
AC-20). Any platform admin may create and edit one; deleting one is Owner
only and audited. Charts are nested in the dashboard body and replaced as a
list on ``PATCH``, in the order given.
"""
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django_tenants.utils import get_public_schema_name
from rest_framework import serializers, status
from rest_framework.response import Response

from tenancy import audit
from tenancy.models import Chart, Dashboard, School
from tenancy.observability import query
from tenancy.permissions import IsPlatformOwner, IsPlatformStaff
from tenancy.views import PlatformView

CHARTS_MAX = 24


def admin_ref(admin):
    return {"id": admin.pk, "name": admin.name} if admin else None


class ChartSerializer(serializers.ModelSerializer):
    school = serializers.PrimaryKeyRelatedField(
        queryset=School.objects.exclude(schema_name=get_public_schema_name()), allow_null=True, required=False
    )
    filters = serializers.DictField(child=serializers.CharField(), required=False)

    class Meta:
        model = Chart
        fields = ["id", "title", "metric_name", "aggregation", "group_by", "filters", "school"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        try:
            query.validate(
                attrs["metric_name"], attrs["aggregation"], attrs.get("group_by") or None, attrs.get("filters")
            )
        except query.QueryError as exc:
            raise serializers.ValidationError({"metric_name": [str(exc)]})
        return attrs

    def to_representation(self, chart):
        data = super().to_representation(chart)
        school = chart.school
        data["school"] = (
            {
                "id": school.pk,
                "name": school.name,
                "slug": school.slug,
                "purged": school.status == School.Status.PURGED,
            }
            if school
            else None
        )
        return data


class DashboardSerializer(serializers.ModelSerializer):
    charts = ChartSerializer(many=True, required=False)

    class Meta:
        model = Dashboard
        fields = ["id", "name", "charts", "created_by", "updated_by", "created_at", "updated_at"]
        read_only_fields = ["id", "created_by", "updated_by", "created_at", "updated_at"]

    def validate_charts(self, value):
        if len(value) > CHARTS_MAX:
            raise serializers.ValidationError(f"A dashboard holds at most {CHARTS_MAX} charts.")
        return value

    def to_representation(self, dashboard):
        data = super().to_representation(dashboard)
        data["created_by"] = admin_ref(dashboard.created_by)
        data["updated_by"] = admin_ref(dashboard.updated_by)
        return data


def _replace_charts(dashboard, charts):
    dashboard.charts.all().delete()
    Chart.objects.bulk_create(
        Chart(dashboard=dashboard, position=i, **chart) for i, chart in enumerate(charts)
    )


def _detail(pk):
    dashboard = get_object_or_404(
        Dashboard.objects.select_related("created_by", "updated_by").prefetch_related("charts__school"), pk=pk
    )
    return DashboardSerializer(dashboard).data


class DashboardListView(PlatformView):
    def get(self, request):
        rows = Dashboard.objects.select_related("created_by", "updated_by").annotate(chart_count=Count("charts"))
        return Response(
            {
                "results": [
                    {
                        "id": d.pk,
                        "name": d.name,
                        "chart_count": d.chart_count,
                        "created_by": admin_ref(d.created_by),
                        "updated_by": admin_ref(d.updated_by),
                        "created_at": d.created_at,
                        "updated_at": d.updated_at,
                    }
                    for d in rows
                ]
            }
        )

    def post(self, request):
        serializer = DashboardSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        charts = serializer.validated_data.pop("charts", [])
        with transaction.atomic():
            dashboard = Dashboard.objects.create(
                **serializer.validated_data, created_by=request.user, updated_by=request.user
            )
            _replace_charts(dashboard, charts)
        return Response(_detail(dashboard.pk), status=status.HTTP_201_CREATED)


class DashboardDetailView(PlatformView):
    def get_permissions(self):
        if self.request.method == "DELETE":
            return [IsPlatformOwner()]
        return [IsPlatformStaff()]

    def get(self, request, pk):
        return Response(_detail(pk))

    def patch(self, request, pk):
        with transaction.atomic():
            dashboard = get_object_or_404(Dashboard.objects.select_for_update(), pk=pk)
            serializer = DashboardSerializer(dashboard, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            charts = serializer.validated_data.pop("charts", None)
            for field, value in serializer.validated_data.items():
                setattr(dashboard, field, value)
            dashboard.updated_by = request.user
            dashboard.save()
            if charts is not None:
                _replace_charts(dashboard, charts)
        return Response(_detail(pk))

    def delete(self, request, pk):
        dashboard = get_object_or_404(Dashboard, pk=pk)
        with transaction.atomic():
            audit.record(request, "observability.dashboard.delete", id=dashboard.pk, name=dashboard.name)
            dashboard.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
