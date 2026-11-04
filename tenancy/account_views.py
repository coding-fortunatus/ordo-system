"""A platform admin's own account: password, authenticator and sessions.

Every change asks for the current password (and the authenticator change for
a current code too), so a session left open on someone else's screen can't
take the account over. Each write records a ``PlatformAuditLog`` row.
"""
from django.core import signing
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.response import Response

from tenancy import audit, lockout, support, totp
from tenancy.auth import ChallengeExpired
from tenancy.models import PlatformAdmin
from tenancy.permissions import IsPlatformOwner
from tenancy.serializers import check_password_strength
from tenancy.views import WRONG_CODE, PlatformView

WRONG_PASSWORD = "Your current password is wrong."
REENROL_MAX_AGE = 600  # seconds to scan the new code and confirm it

_reenrol_signer = signing.TimestampSigner(salt="tenancy.platform.reenrol")


def _check_password(request, password):
    lockout.check(request.user.email, audit.request_ip(request))
    if not request.user.check_password(password or ""):
        audit.record(request, "account_password_wrong", email=request.user.email)
        raise serializers.ValidationError({"current_password": [WRONG_PASSWORD]})


class _PasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    new_password = serializers.CharField()


class ChangePasswordView(PlatformView):
    """New password; every other session of this admin is signed out."""

    def post(self, request):
        serializer = _PasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        admin = request.user
        _check_password(request, data["current_password"])
        check_password_strength(data["new_password"], admin)
        with transaction.atomic():
            admin.set_password(data["new_password"])
            admin.save(update_fields=["password"])
            ended = admin.tokens.exclude(pk=request.auth.pk).delete()[0]
            audit.record(request, "password_changed", other_sessions_ended=ended)
        return Response(status=status.HTTP_204_NO_CONTENT)


class _StartReenrolSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    code = serializers.CharField()


class _ConfirmReenrolSerializer(serializers.Serializer):
    challenge = serializers.CharField()
    code = serializers.CharField()


class ReenrolStartView(PlatformView):
    """Step one of moving to a new authenticator: prove both factors, get a QR."""

    def post(self, request):
        serializer = _StartReenrolSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        admin = request.user
        _check_password(request, serializer.validated_data["current_password"])
        if not totp.verify(admin, serializer.validated_data["code"]):
            audit.record(request, "totp_failed", email=admin.email)
            raise serializers.ValidationError({"code": [WRONG_CODE]})
        secret, uri = totp.pending_secret(admin)
        challenge = _reenrol_signer.sign(f"{admin.pk}:{totp.seal(secret)}")
        return Response({"challenge": challenge, "otpauth_uri": uri})


class ReenrolConfirmView(PlatformView):
    """Step two: a code from the new authenticator makes it the only one."""

    def post(self, request):
        serializer = _ConfirmReenrolSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            value = _reenrol_signer.unsign(
                serializer.validated_data["challenge"], max_age=REENROL_MAX_AGE
            )
        except signing.BadSignature:
            raise ChallengeExpired("This setup has expired. Start again.")
        admin_id, _, sealed = value.partition(":")
        if str(request.user.pk) != admin_id:
            raise ChallengeExpired("This setup has expired. Start again.")
        if not totp.confirm(request.user, totp.unseal(sealed), serializer.validated_data["code"]):
            raise serializers.ValidationError({"code": [WRONG_CODE]})
        audit.record(request, "totp_reenrolled")
        return Response(status=status.HTTP_204_NO_CONTENT)


def _session(token, current):
    return {
        "id": token.public_id,
        "created_at": token.created_at,
        "expires_at": token.expires_at,
        "last_used_at": token.last_used_at,
        "user_agent": token.user_agent,
        "ip": token.ip,
        "current": token.pk == current.pk,
    }


def _live_tokens(admin):
    return admin.tokens.filter(expires_at__gt=timezone.now()).order_by("-last_used_at", "-created_at")


class SessionListView(PlatformView):
    def get(self, request):
        return Response([_session(t, request.auth) for t in _live_tokens(request.user)])


class SessionRevokeView(PlatformView):
    """End one of your own sessions, by its public id."""

    def delete(self, request, session_id):
        if len(session_id) != 16:
            return Response(status=status.HTTP_404_NOT_FOUND)
        token = get_object_or_404(request.user.tokens, key_hash__startswith=session_id)
        token.delete()
        audit.record(request, "session_revoked", session=session_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SessionRevokeOthersView(PlatformView):
    """Sign out everywhere but here."""

    def post(self, request):
        ended = request.user.tokens.exclude(pk=request.auth.pk).delete()[0]
        audit.record(request, "sessions_revoked", ended=ended)
        return Response({"ended": ended})


class AdminSignOutView(PlatformView):
    """An Owner ends every session of another admin, e.g. a lost laptop."""

    permission_classes = [IsPlatformOwner]

    def post(self, request, pk):
        target = get_object_or_404(PlatformAdmin, pk=pk)
        with transaction.atomic():
            # A school support token is a session too (a lost laptop holds it).
            support.end_for_admin(request, target, reason="admin_signed_out")
            ended = target.tokens.all().delete()[0]
        audit.record(
            request, "admin_signed_out", target_id=target.pk, email=target.email, ended=ended
        )
        return Response({"ended": ended})
