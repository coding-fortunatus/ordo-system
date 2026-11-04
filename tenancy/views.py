"""Platform console API under ``/api/platform/`` (spec 0003, child 0003).

Only routed in the public urlconf, so none of this exists on a school host.
Every view authenticates with ``PlatformTokenAuthentication`` alone, and
every write records a ``PlatformAuditLog`` row.
"""
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from ems.api.exceptions import Conflict
from ems.api.pagination import DefaultPagination
from tenancy import admin_invites, audit, lockout, support, totp
from tenancy.auth import (
    STEP_ENROLL,
    STEP_TOTP,
    ChallengeExpired,
    PlatformTokenAuthentication,
    issue_token,
    make_challenge,
    read_challenge,
)
from tenancy.models import PlatformAdmin, PlatformAuditLog
from tenancy.permissions import IsPlatformOwner, IsPlatformStaff
from tenancy.serializers import (
    AcceptInviteSerializer,
    CodeSerializer,
    InviteAdminSerializer,
    LoginSerializer,
    PlatformAdminListSerializer,
    PlatformAdminSerializer,
    PlatformAuditLogSerializer,
    UpdateAdminSerializer,
    check_password_strength,
)

WRONG_CREDENTIALS = "Email or password is wrong."
WRONG_CODE = "That code is wrong or has already been used."


class PlatformView(APIView):
    """Signed in platform admins only. Owner only views narrow the permission."""

    authentication_classes = [PlatformTokenAuthentication]
    permission_classes = [IsPlatformStaff]


class SignInView(APIView):
    """The sign in steps: nobody is signed in yet, and no school token counts."""

    authentication_classes = []
    permission_classes = [AllowAny]


def _signed_in(request, admin):
    key, token = issue_token(admin, request)
    audit.record(request, "login", admin=admin)
    return Response(
        {
            "token": key,
            "expires_at": token.expires_at,
            "admin": PlatformAdminSerializer(admin).data,
        }
    )


class LoginView(SignInView):
    """Password step. Says which second step comes next."""

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()
        password = serializer.validated_data["password"]
        lockout.check(email, audit.request_ip(request))

        admin = PlatformAdmin.objects.filter(email__iexact=email).first()
        if admin is None or not admin.is_active or not admin.has_usable_password():
            # Same hashing work as a real check on every path that skips
            # one (no such email, deactivated, invite not yet accepted), so
            # timing doesn't reveal which of those an email is.
            make_password(password)
            ok = False
        else:
            ok = admin.check_password(password)
        if not ok:
            audit.record(request, "login_failed", admin=admin, email=email)
            return Response(
                {"detail": WRONG_CREDENTIALS}, status=status.HTTP_400_BAD_REQUEST
            )

        if admin.totp_confirmed_at:
            return Response(
                {"step": STEP_TOTP, "challenge": make_challenge(admin, STEP_TOTP)}
            )
        return Response(
            {
                "step": STEP_ENROLL,
                "challenge": make_challenge(admin, STEP_ENROLL),
                "otpauth_uri": totp.new_secret(admin),
            }
        )


def _check_code(request, step):
    """The admin behind a valid challenge whose code checks out.

    Returns ``(admin, None)``, or ``(admin, error response)`` for a wrong code.
    """
    serializer = CodeSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    admin = read_challenge(serializer.validated_data["challenge"], step)
    lockout.check(admin.email, audit.request_ip(request))
    enrolled = admin.totp_confirmed_at is not None
    if enrolled != (step == STEP_TOTP):
        # Enrolled since the challenge was issued, or 2FA was reset.
        raise ChallengeExpired()
    if not totp.verify(admin, serializer.validated_data["code"]):
        audit.record(request, "totp_failed", admin=admin, email=admin.email)
        return admin, Response({"detail": WRONG_CODE}, status=status.HTTP_400_BAD_REQUEST)
    return admin, None


class TotpView(SignInView):
    def post(self, request):
        admin, error = _check_code(request, STEP_TOTP)
        return error or _signed_in(request, admin)


class EnrollView(SignInView):
    def post(self, request):
        admin, error = _check_code(request, STEP_ENROLL)
        if error:
            return error
        admin.totp_confirmed_at = timezone.now()
        admin.save(update_fields=["totp_confirmed_at"])
        audit.record(request, "totp_enrolled", admin=admin)
        return _signed_in(request, admin)


class LogoutView(PlatformView):
    def post(self, request):
        request.auth.delete()
        audit.record(request, "logout")
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(PlatformView):
    def get(self, request):
        return Response(PlatformAdminSerializer(request.user).data)


class AdminListView(PlatformView):
    permission_classes = [IsPlatformOwner]

    def get(self, request):
        admins = PlatformAdmin.objects.order_by("name", "email")
        return Response(PlatformAdminListSerializer(admins, many=True).data)

    def post(self, request):
        serializer = InviteAdminSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        admin = PlatformAdmin(**serializer.validated_data)
        admin.set_unusable_password()
        try:
            with transaction.atomic():
                admin.save()
                admin_invites.send(admin)
                audit.record(
                    request, "admin_invited", email=admin.email, role=admin.role
                )
        except IntegrityError:
            return Response(
                {
                    "detail": "Validation failed.",
                    "errors": {
                        "email": ["A platform admin with this email already exists."]
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            PlatformAdminListSerializer(admin).data, status=status.HTTP_201_CREATED
        )


class AdminDetailView(PlatformView):
    permission_classes = [IsPlatformOwner]

    @transaction.atomic
    def patch(self, request, pk):
        serializer = UpdateAdminSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Lock every active Owner first, always in the same order, so two
        # Owners demoting each other at once can't both pass the check.
        owners = list(
            PlatformAdmin.objects.select_for_update()
            .filter(role=PlatformAdmin.Role.OWNER, is_active=True)
            .order_by("pk")
        )
        target = get_object_or_404(PlatformAdmin.objects.select_for_update(), pk=pk)

        changes = {
            field: [getattr(target, field), value]
            for field, value in data.items()
            if getattr(target, field) != value
        }
        if not changes:
            return Response(PlatformAdminListSerializer(target).data)
        access_change = "role" in changes or "is_active" in changes
        if access_change and target.pk == request.user.pk:
            raise Conflict("You can't change your own role or deactivate yourself.")
        stays_owner = data.get("role", target.role) == PlatformAdmin.Role.OWNER and data.get(
            "is_active", target.is_active
        )
        if target in owners and not stays_owner and len(owners) <= 1:
            raise Conflict("There must always be at least one active Owner.")

        for field, (_old, new) in changes.items():
            setattr(target, field, new)
        if not target.is_active:
            # Their support visits end first (spec 0003, child 0006).
            support.end_for_admin(request, target, reason="admin_deactivated")
        target.save(update_fields=list(changes))
        if not target.is_active:
            target.tokens.all().delete()
        audit.record(
            request, "admin_updated", target_id=target.pk, email=target.email, changes=changes
        )
        return Response(PlatformAdminListSerializer(target).data)


class AdminActionView(PlatformView):
    permission_classes = [IsPlatformOwner]

    def target(self, pk):
        return get_object_or_404(PlatformAdmin, pk=pk)


class AdminResetTwoFactorView(AdminActionView):
    def post(self, request, pk):
        target = self.target(pk)
        if target.pk == request.user.pk:
            raise Conflict("You can't reset your own two factor sign in.")
        with transaction.atomic():
            totp.clear(target)
            # Signed out everywhere, school support visits included; they
            # enrol a new device at next sign in.
            support.end_for_admin(request, target, reason="admin_2fa_reset")
            target.tokens.all().delete()
            audit.record(request, "admin_2fa_reset", target_id=target.pk, email=target.email)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminUnlockView(AdminActionView):
    def post(self, request, pk):
        target = self.target(pk)
        # The lockout ignores failures for this email before this row.
        audit.record(request, lockout.UNLOCKED, target_id=target.pk, email=target.email)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminResendInviteView(AdminActionView):
    def post(self, request, pk):
        with transaction.atomic():
            target = get_object_or_404(PlatformAdmin.objects.select_for_update(), pk=pk)
            if target.has_usable_password():
                raise Conflict("This admin has already accepted their invite.")
            # A new unusable hash voids every earlier link.
            target.set_unusable_password()
            target.save(update_fields=["password"])
            admin_invites.send(target)
            audit.record(
                request, "admin_invite_resent", target_id=target.pk, email=target.email
            )
        return Response(status=status.HTTP_202_ACCEPTED)


class AcceptInviteView(SignInView):
    """Set a password from the emailed link. 2FA enrolment follows at sign in."""

    @transaction.atomic
    def post(self, request):
        serializer = AcceptInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        admin = admin_invites.read_token(serializer.validated_data["token"])
        # Re-read under a lock: two submits of the same link can't both land.
        locked = PlatformAdmin.objects.select_for_update().get(pk=admin.pk)
        if locked.password != admin.password:
            raise admin_invites.InviteGone()
        password = serializer.validated_data["password"]
        check_password_strength(password, locked)
        locked.set_password(password)
        locked.save(update_fields=["password"])
        audit.record(request, "admin_accepted", admin=locked)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AuditLogView(generics.ListAPIView):
    authentication_classes = [PlatformTokenAuthentication]
    permission_classes = [IsPlatformStaff]
    serializer_class = PlatformAuditLogSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        queryset = PlatformAuditLog.objects.select_related("admin", "school")
        params = self.request.query_params
        for param, field in (("school", "school_id"), ("admin", "admin_id")):
            value = params.get(param, "")
            if value.isdigit():
                queryset = queryset.filter(**{field: int(value)})
        if params.get("action"):
            queryset = queryset.filter(action=params["action"])
        return queryset
