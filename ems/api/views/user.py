import csv

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from ems.api.exceptions import Conflict
from ems.api.permissions import IsSuperAdmin
from ems.api.serializers.user import (
    ChangePasswordSerializer,
    UserSerializer,
)
from ems.export_files import counted_export
from ems.models import Department, User
from tenancy import limits

DEFAULT_SEED_PASSWORD = "Ems@2025"
SEED_EMAIL_DOMAIN = "ems.com"


class UserViewSet(viewsets.ModelViewSet):
    """User CRUD — admin only."""

    serializer_class = UserSerializer
    permission_classes = [IsSuperAdmin]

    def get_queryset(self):
        qs = User.objects.all().select_related("department").order_by("email")
        if query := self.request.query_params.get("query"):
            qs = (
                qs.filter(email__icontains=query)
                | qs.filter(first_name__icontains=query)
                | qs.filter(last_name__icontains=query)
            )
        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        # password is required when creating a user
        if not self.request.data.get("password"):
            raise ValidationError({"password": ["Password is required."]})
        if serializer.validated_data.get("is_active", True):
            limits.assert_can_add_staff(1)
        serializer.save()

    @transaction.atomic
    def perform_update(self, serializer):
        # Reactivating an account takes a staff seat again; a role change doesn't.
        if serializer.validated_data.get("is_active") and not serializer.instance.is_active:
            limits.assert_can_add_staff(1)
        serializer.save()

    def perform_destroy(self, instance):
        if instance.id == self.request.user.id:
            raise Conflict("You cannot delete your own account.")
        if instance.is_staff and User.objects.filter(is_staff=True).count() <= 1:
            raise Conflict("Refusing to delete the last administrator.")
        instance.delete()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["get"], url_path="export")
    def export(self, request):
        """Stream departmental (non-admin) staff as CSV.

        Honours the same ``query`` filter as the list endpoint; admin
        accounts are excluded.
        """
        users = self.get_queryset().filter(is_staff=False)
        timestamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
        filename = f"departmental-staff-{timestamp}.csv"
        response = HttpResponse(
            content_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
        writer = csv.writer(response)
        writer.writerow(
            [
                "Email",
                "First name",
                "Last name",
                "Department",
                "Department slug",
                "Is active",
                "Created at",
            ]
        )
        for u in users:
            dept = u.department
            writer.writerow(
                [
                    u.email,
                    u.first_name,
                    u.last_name,
                    dept.name if dept else "",
                    dept.slug if dept else "",
                    "yes" if u.is_active else "no",
                    timezone.localtime(u.created_at).strftime("%Y-%m-%d %H:%M:%S") if u.created_at else "",
                ]
            )
        return counted_export("users", response)

    @action(detail=False, methods=["post"], url_path="seed-departments")
    def seed_departments(self, request):
        """Create one staff user per department.

        Email is ``{department.slug}@ems.com``; password defaults to a
        shared seed value but can be overridden in the request body.
        Existing emails are skipped unless ``overwrite=true`` is sent,
        in which case the password is reset.
        """
        password = (request.data.get("password") or DEFAULT_SEED_PASSWORD).strip()
        if len(password) < 8:
            raise ValidationError(
                {"password": ["Password must be at least 8 characters."]}
            )
        overwrite = bool(request.data.get("overwrite", False))

        departments = list(Department.objects.all().order_by("name"))
        created: list[str] = []
        updated: list[str] = []
        skipped: list[str] = []

        with transaction.atomic():
            emails = {dept.pk: f"{dept.slug}@{SEED_EMAIL_DOMAIN}".lower() for dept in departments}
            users = {u.email: u for u in User.objects.filter(email__in=emails.values())}
            # New accounts take a staff seat, and so does an inactive one that
            # overwrite turns back on.
            seats = sum(
                1
                for email in emails.values()
                if email not in users or (overwrite and not users[email].is_active)
            )
            limits.assert_can_add_staff(seats)
            for dept in departments:
                email = emails[dept.pk]
                existing = users.get(email)
                first_name = (dept.name or dept.slug)[:30]
                last_name = "Staff"
                if existing:
                    if overwrite:
                        existing.first_name = first_name
                        existing.last_name = last_name
                        existing.department = dept
                        existing.is_staff = False
                        existing.is_active = True
                        existing.set_password(password)
                        existing.save()
                        Token.objects.filter(user=existing).delete()
                        updated.append(email)
                    else:
                        skipped.append(email)
                    continue

                user = User(
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    department=dept,
                    is_staff=False,
                    is_active=True,
                )
                user.set_password(password)
                user.save()
                created.append(email)

        return Response(
            {
                "detail": (
                    f"Seeded {len(created)} new user(s), "
                    f"updated {len(updated)}, skipped {len(skipped)}."
                ),
                "created": created,
                "updated": updated,
                "skipped": skipped,
                "departments_total": len(departments),
                "password": password,
            }
        )

    @action(detail=True, methods=["post"], url_path="change-password")
    def change_password(self, request, pk=None):
        user = self.get_object()
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user.set_password(serializer.validated_data["password"])
        user.save()
        # Invalidate the user's existing token, if any, so they have to log in again.
        Token.objects.filter(user=user).delete()
        Token.objects.create(user=user)
        return Response({"detail": "Password updated."})
