"""Ordo support on the school host: redeem a console ticket, end the visit.

The ticket is the redeem call's only credential; ending needs the support
token itself (spec 0003, child 0006).
"""
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ems.api.exceptions import Conflict
from ems.api.permissions import IsSuperAdmin
from tenancy import support
from tenancy.models import SupportAccess, SupportSession


class _RedeemSerializer(serializers.Serializer):
    ticket = serializers.CharField(max_length=200)


class SupportRedeemView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = _RedeemSerializer(data=request.data)
        if not serializer.is_valid():
            raise support.TicketGone()
        session, token = support.redeem(request, serializer.validated_data["ticket"])
        return Response({"token": token.key, "expires_at": session.expires_at})


class SupportEndView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.is_support:
            raise PermissionDenied("Only an Ordo support session can be ended here.")
        with transaction.atomic():
            session = support.current_session(request.user, request.tenant)
            if session is not None:
                support.end(request, session)
            else:
                # Closed from the console a moment ago: the token still goes.
                request.auth.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------- the school asks Ordo in


class _AccessRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000)


def _access_data(access):
    if access is None:
        return None
    return {
        "id": access.pk,
        "reason": access.reason,
        "requested_by": access.requested_by_name or access.requested_by_email,
        "created_at": access.created_at,
        "expires_at": access.expires_at,
    }


class _NotSupport(IsSuperAdmin):
    """The school's own Super Admins; support can't let itself in."""

    message = "Only the school's own Super Admins can let Ordo support in."

    def has_permission(self, request, view):
        return super().has_permission(request, view) and not request.user.is_support


class SupportAccessView(APIView):
    """``GET``: whether Ordo support may come in, and its recent visits.
    ``POST {reason}``: ask support in for a day. ``DELETE``: withdraw that,
    ending any visit running under it."""

    permission_classes = [_NotSupport]

    def get(self, request):
        school = request.tenant
        now = timezone.now()
        visits = (
            SupportSession.objects.filter(school=school, started_at__isnull=False)
            .select_related("admin")[:10]
        )
        return Response(
            {
                "is_demo": school.is_demo,
                "current": _access_data(SupportAccess.open(school, now).first()),
                "visits": [
                    {
                        "id": s.pk,
                        "admin_name": s.admin.name,
                        "reason": s.reason,
                        "started_at": s.started_at,
                        "ended_at": s.ended_at or (s.expires_at if s.expires_at <= now else None),
                    }
                    for s in visits
                ],
            }
        )

    def post(self, request):
        serializer = _AccessRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        access = support.request_access(request, serializer.validated_data["reason"])
        return Response(_access_data(access), status=status.HTTP_201_CREATED)

    def delete(self, request):
        access = SupportAccess.open(request.tenant, timezone.now()).first()
        if access is None:
            raise Conflict("Ordo support hasn't been asked in.")
        support.withdraw_access(request, access)
        return Response(status=status.HTTP_204_NO_CONTENT)
