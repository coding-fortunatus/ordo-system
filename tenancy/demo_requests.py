"""Demo and setup help requests from the marketing site.

The site's server posts the form to ``POST /api/public/demo-requests/``
(no token: the browser never calls the API, see the site's README) and
forwards the visitor's address in ``X-Forwarded-For``. Each request is
stored, the platform Owners get an email (``tenancy.alerts``), and the team
works through them on the console's Requests page.
"""
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from ems.api.exceptions import CodedAPIException
from ems.api.pagination import DefaultPagination
from tenancy import alerts, audit
from tenancy.audit import request_ip
from tenancy.auth import PlatformTokenAuthentication
from tenancy.models import DemoRequest, PlatformAdmin, School
from tenancy.observability import counters
from tenancy.permissions import IsPlatformStaff
from tenancy.views import PlatformView

# Per visitor, and for the whole form, per hour. Enough for someone fixing
# a typo; a script hits the cap long before it floods the team's inbox.
PER_IP_PER_HOUR = 5
TOTAL_PER_HOUR = 100
THROTTLE_TTL = 60 * 60

# A field people can't see. Bots fill it in; such posts are accepted and
# thrown away, so the bot learns nothing.
HONEYPOT = "website"

STUDENT_COUNTS = ("under_2k", "2k_10k", "10k_30k", "over_30k")


class TooManyRequests(CodedAPIException):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = "We've had a lot of requests from you. Please try again in an hour or email hello@ordo.app."
    default_code = "rate_limited"


def _throttled(ip):
    """Over either hourly cap. With Redis down nothing can be counted, and a
    lost lead costs more than a few extra emails, so the request goes in."""
    hour = timezone.now().strftime("%Y%m%d%H")
    by_ip = counters.hit(f"demo:ip:{ip or 'unknown'}:{hour}", THROTTLE_TTL)
    if by_ip is None:
        return False
    if by_ip > PER_IP_PER_HOUR:
        # Refused by its own IP cap: don't spend the shared budget, or one
        # client could lock every visitor out for the hour.
        return True
    total = counters.hit(f"demo:all:{hour}", THROTTLE_TTL)
    if total is None:
        return False
    return total > TOTAL_PER_HOUR


class PublicDemoRequestSerializer(serializers.ModelSerializer):
    student_count = serializers.ChoiceField(
        choices=STUDENT_COUNTS, required=False, allow_blank=True
    )

    class Meta:
        model = DemoRequest
        fields = [
            "kind", "name", "email", "phone", "institution", "job_title",
            "student_count", "message",
        ]
        extra_kwargs = {"kind": {"required": False}}

    def validate(self, data):
        for field in ("name", "institution", "phone", "job_title", "message"):
            if field in data:
                data[field] = data[field].strip()
        for field in ("name", "institution"):
            if not data.get(field):
                raise serializers.ValidationError({field: ["This field may not be blank."]})
        data["email"] = data["email"].strip().lower()
        return data


class PublicDemoRequestView(APIView):
    """``POST /api/public/demo-requests/``: open to anyone, nothing read back."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        ip = request_ip(request)
        if _throttled(ip):
            raise TooManyRequests()
        if str(request.data.get(HONEYPOT) or "").strip():
            return Response({"ok": True}, status=status.HTTP_201_CREATED)
        serializer = PublicDemoRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        demo = serializer.save(ip=ip)
        lines = [
            f"{demo.name} <{demo.email}>" + (f", {demo.phone}" if demo.phone else ""),
            demo.institution + (f" ({demo.job_title})" if demo.job_title else ""),
        ]
        if demo.student_count:
            lines.append(f"Students: {demo.student_count.replace('_', ' ')}")
        if demo.message:
            lines.append(demo.message[:500])
        alerts.send(
            f"{demo.get_kind_display()} request from {demo.institution}"[:150],
            lines,
            f"/requests?id={demo.pk}",
        )
        return Response({"ok": True}, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------- console


class DemoRequestSerializer(serializers.ModelSerializer):
    assigned_to = serializers.SerializerMethodField()
    assigned_to_id = serializers.PrimaryKeyRelatedField(
        source="assigned_to",
        queryset=PlatformAdmin.objects.filter(is_active=True),
        allow_null=True,
        required=False,
        write_only=True,
    )
    school = serializers.SerializerMethodField()
    school_id = serializers.PrimaryKeyRelatedField(
        source="school",
        queryset=School.objects.all(),
        allow_null=True,
        required=False,
        write_only=True,
    )

    class Meta:
        model = DemoRequest
        fields = [
            "id", "kind", "name", "email", "phone", "institution", "job_title",
            "student_count", "message", "status", "notes", "assigned_to",
            "assigned_to_id", "school", "school_id", "created_at", "updated_at",
        ]
        read_only_fields = [
            "kind", "name", "email", "phone", "institution", "job_title",
            "student_count", "message", "created_at", "updated_at",
        ]

    def get_assigned_to(self, obj):
        return obj.assigned_to and {"id": obj.assigned_to.id, "name": obj.assigned_to.name}

    def get_school(self, obj):
        return obj.school and {"id": obj.school.id, "name": obj.school.name, "slug": obj.school.slug}


def _requests():
    return DemoRequest.objects.select_related("assigned_to", "school")


class DemoRequestListView(generics.ListAPIView):
    """``?status=`` (``open`` for every unfinished one), ``?kind=``, ``?q=``."""

    authentication_classes = [PlatformTokenAuthentication]
    permission_classes = [IsPlatformStaff]
    serializer_class = DemoRequestSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        queryset = _requests()
        params = self.request.query_params
        wanted = params.get("status")
        if wanted == "open":
            queryset = queryset.filter(status__in=DemoRequest.OPEN_STATUSES)
        elif wanted in DemoRequest.Status.values:
            queryset = queryset.filter(status=wanted)
        if params.get("kind") in DemoRequest.Kind.values:
            queryset = queryset.filter(kind=params["kind"])
        if q := params.get("q", "").strip():
            queryset = queryset.filter(
                Q(name__icontains=q) | Q(email__icontains=q) | Q(institution__icontains=q)
            )
        return queryset


class DemoRequestSummaryView(PlatformView):
    """How many requests nobody has picked up yet, for the nav badge."""

    def get(self, request):
        return Response({"new": DemoRequest.objects.filter(status=DemoRequest.Status.NEW).count()})


class DemoRequestDetailView(PlatformView):
    """Any platform admin may triage: status, notes, owner and school."""

    def get(self, request, pk):
        return Response(DemoRequestSerializer(get_object_or_404(_requests(), pk=pk)).data)

    def patch(self, request, pk):
        demo = get_object_or_404(_requests(), pk=pk)
        before = {"status": demo.status, "assigned_to_id": demo.assigned_to_id, "school_id": demo.school_id}
        serializer = DemoRequestSerializer(demo, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        demo = serializer.save()
        after = {"status": demo.status, "assigned_to_id": demo.assigned_to_id, "school_id": demo.school_id}
        changed = {k: [before[k], after[k]] for k in before if before[k] != after[k]}
        if changed or "notes" in request.data:
            audit.record(
                request, "demo_request_updated", demo.school,
                request_id=demo.pk, institution=demo.institution, **changed,
            )
        return Response(DemoRequestSerializer(demo).data)
