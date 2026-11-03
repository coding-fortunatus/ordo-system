from zoneinfo import available_timezones

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework import serializers

from tenancy.features import KEYS
from tenancy.models import (
    Plan,
    PlatformAdmin,
    PlatformAuditLog,
    School,
    SupportSession,
    UsageSnapshot,
)
from tenancy.slugs import RESERVED, SLUG_RE

DEFAULT_TIMEZONE = School._meta.get_field("timezone").default


class PlatformAdminSerializer(serializers.ModelSerializer):
    """What ``me/`` and sign in return about the signed in admin."""

    class Meta:
        model = PlatformAdmin
        fields = ["id", "email", "name", "role"]


class PlatformAdminListSerializer(serializers.ModelSerializer):
    """An admin as Owners see them on the admins page."""

    totp_enrolled = serializers.SerializerMethodField()
    invite_pending = serializers.SerializerMethodField()

    class Meta:
        model = PlatformAdmin
        fields = [
            "id",
            "email",
            "name",
            "role",
            "is_active",
            "totp_enrolled",
            "invite_pending",
            "last_login",
            "created_at",
        ]

    def get_totp_enrolled(self, obj):
        return obj.totp_confirmed_at is not None

    def get_invite_pending(self, obj):
        return not obj.has_usable_password()


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False)


class CodeSerializer(serializers.Serializer):
    challenge = serializers.CharField()
    code = serializers.CharField(max_length=10)


class InviteAdminSerializer(serializers.Serializer):
    email = serializers.EmailField()
    name = serializers.CharField(max_length=255)
    role = serializers.ChoiceField(choices=PlatformAdmin.Role.choices)

    def validate_email(self, value):
        value = value.strip().lower()
        if PlatformAdmin.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A platform admin with this email already exists.")
        return value


class UpdateAdminSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    role = serializers.ChoiceField(choices=PlatformAdmin.Role.choices, required=False)
    is_active = serializers.BooleanField(required=False)


class AcceptInviteSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False)


def check_password_strength(password, admin):
    """``validate_password`` as a DRF field error on ``password``."""
    try:
        validate_password(password, user=admin)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({"password": list(exc.messages)})


class _AdminRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlatformAdmin
        fields = ["id", "name", "email"]


class PlatformAuditLogSerializer(serializers.ModelSerializer):
    admin = _AdminRefSerializer(read_only=True)
    school = serializers.SerializerMethodField()

    class Meta:
        model = PlatformAuditLog
        fields = ["id", "admin", "school", "action", "detail", "ip", "created_at"]

    def get_school(self, obj):
        if obj.school is None:
            return None
        return {"id": obj.school.id, "name": obj.school.name, "slug": obj.school.slug}


def _feature_list(**kwargs):
    return serializers.ListField(child=serializers.ChoiceField(choices=sorted(KEYS)), **kwargs)


class PlanSerializer(serializers.ModelSerializer):
    """A plan as the console lists it, and its create and edit form.

    The code is fixed once the plan exists. ``school_count`` is annotated by
    the list view.
    """

    max_students = serializers.IntegerField(min_value=0, allow_null=True, required=False)
    max_staff_users = serializers.IntegerField(min_value=0, allow_null=True, required=False)
    features = _feature_list(required=False)
    school_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Plan
        fields = [
            "id",
            "code",
            "name",
            "max_students",
            "max_staff_users",
            "features",
            "is_active",
            "school_count",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance is not None and not isinstance(self.instance, (list, tuple)):
            self.fields["code"].read_only = True

    def validate_features(self, value):
        return sorted(set(value))


class _PlanRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = ["id", "code", "name"]


class _UsageSerializer(serializers.ModelSerializer):
    class Meta:
        model = UsageSnapshot
        fields = ["taken_on", "students", "staff_users", "jobs_last_24h", "last_activity_at"]


class SchoolListSerializer(serializers.ModelSerializer):
    """A school as the console's list shows it.

    ``usage`` is the latest nightly snapshot, or None before the first one.
    ``first_admin_joined`` comes from context (``joined``, by school id, or
    the detail view's ``first_admin``); None while the school has no live
    schema.
    """

    plan = _PlanRefSerializer(read_only=True)
    domain = serializers.SerializerMethodField()
    usage = serializers.SerializerMethodField()
    first_admin_joined = serializers.SerializerMethodField()

    class Meta:
        model = School
        fields = [
            "id",
            "name",
            "slug",
            "status",
            "plan",
            "domain",
            "first_admin_name",
            "first_admin_email",
            "first_admin_joined",
            "country",
            "provisioning_error",
            "created_at",
            "usage",
        ]

    def get_domain(self, obj):
        # Prefetched, so filter in Python rather than query per row.
        primary = [d.domain for d in obj.domains.all() if d.is_primary]
        return primary[0] if primary else None

    def get_usage(self, obj):
        if hasattr(obj, "latest_usage"):
            latest = obj.latest_usage[0] if obj.latest_usage else None
        else:
            latest = obj.usage_snapshots.first()
        return _UsageSerializer(latest).data if latest else None

    def get_first_admin_joined(self, obj):
        if "joined" in self.context:
            return self.context["joined"].get(obj.pk)
        first_admin = self.context.get("first_admin")
        return first_admin["joined"] if first_admin else None


class SchoolDetailSerializer(SchoolListSerializer):
    """One school with everything the console knows about it.

    ``first_admin`` is passed in context: whether they've joined, and when
    their open invite expires. None while the school has no live schema.
    """

    domains = serializers.SerializerMethodField()
    created_by = _AdminRefSerializer(read_only=True)
    first_admin = serializers.SerializerMethodField()

    class Meta(SchoolListSerializer.Meta):
        fields = SchoolListSerializer.Meta.fields + [
            "domains",
            "max_students_override",
            "max_staff_users_override",
            "features_override",
            "contact_name",
            "contact_email",
            "timezone",
            "notes",
            "plan_started_on",
            "renews_on",
            "provisioning_started_at",
            "was_ever_active",
            "suspended_at",
            "suspended_reason",
            "deletion_requested_at",
            "purge_after",
            "final_export_ref",
            "purged_at",
            "created_by",
            "first_admin",
        ]

    def get_domains(self, obj):
        return [
            {"domain": d.domain, "is_primary": d.is_primary}
            for d in sorted(obj.domains.all(), key=lambda d: (not d.is_primary, d.domain))
        ]

    def get_first_admin(self, obj):
        return self.context.get("first_admin")


class CreateSchoolSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    slug = serializers.CharField(max_length=30)
    plan = serializers.SlugRelatedField(slug_field="code", queryset=Plan.objects.all())
    max_students_override = serializers.IntegerField(
        min_value=0, allow_null=True, required=False
    )
    max_staff_users_override = serializers.IntegerField(
        min_value=0, allow_null=True, required=False
    )
    features_override = _feature_list(allow_null=True, required=False)
    first_admin_name = serializers.CharField(max_length=255)
    first_admin_email = serializers.EmailField()
    contact_name = serializers.CharField(max_length=255, allow_blank=True, required=False)
    contact_email = serializers.EmailField(allow_blank=True, required=False)
    country = serializers.CharField(max_length=2, allow_blank=True, required=False)
    timezone = serializers.CharField(max_length=64, required=False, default=DEFAULT_TIMEZONE)
    notes = serializers.CharField(allow_blank=True, required=False)
    plan_started_on = serializers.DateField(allow_null=True, required=False)
    renews_on = serializers.DateField(allow_null=True, required=False)

    def validate_slug(self, value):
        if not SLUG_RE.match(value):
            raise serializers.ValidationError(
                "Use 3 to 30 lowercase letters, digits or hyphens, starting and "
                "ending with a letter or digit."
            )
        # The console's own subdomain too, whatever this deploy calls it.
        if value in RESERVED or value == settings.PLATFORM_CONSOLE_SUBDOMAIN:
            raise serializers.ValidationError("This address is reserved.")
        # Any status counts, purged too: a slug is never reused.
        if School.objects.filter(slug=value).exists():
            raise serializers.ValidationError("This address is already taken.")
        return value

    def validate_plan(self, value):
        if not value.is_active:
            raise serializers.ValidationError("This plan is no longer offered.")
        return value

    def validate_features_override(self, value):
        return None if value is None else sorted(set(value))

    def validate_first_admin_email(self, value):
        return value.strip().lower()

    def validate_country(self, value):
        value = value.strip().upper()
        if value and not (len(value) == 2 and value.isalpha() and value.isascii()):
            raise serializers.ValidationError("Use a two letter country code, like NG.")
        return value

    def validate_timezone(self, value):
        if value not in available_timezones():
            raise serializers.ValidationError("Unknown time zone.")
        return value

    def validate(self, attrs):
        _check_dates(attrs.get("plan_started_on"), attrs.get("renews_on"))
        return attrs


def _check_dates(start, renews):
    if start and renews and renews < start:
        raise serializers.ValidationError(
            {"renews_on": ["The renewal date can't be before the plan starts."]}
        )


class UpdateSchoolSerializer(CreateSchoolSerializer):
    """Edit a school: any create field but the slug, which never changes.

    Validates against ``instance``: a school may stay on a plan that is no
    longer offered, but can't be moved onto one.
    """

    slug = None

    def validate_plan(self, value):
        if value.pk == self.instance.plan_id:
            return value
        return super().validate_plan(value)

    def validate(self, attrs):
        _check_dates(
            attrs.get("plan_started_on", self.instance.plan_started_on),
            attrs.get("renews_on", self.instance.renews_on),
        )
        return attrs


class SupportSessionSerializer(serializers.ModelSerializer):
    admin = _AdminRefSerializer(read_only=True)
    state = serializers.SerializerMethodField()

    class Meta:
        model = SupportSession
        fields = [
            "id", "admin", "reason", "state", "created_at", "ticket_expires_at",
            "started_at", "expires_at", "ended_at",
        ]

    def get_state(self, obj):
        """``waiting`` for the link, ``active``, ``ended``, or ``unused``."""
        from django.utils import timezone

        now = timezone.now()
        if obj.ended_at is not None or (obj.expires_at and obj.expires_at <= now):
            return "ended"
        if obj.started_at is not None:
            return "active"
        return "waiting" if obj.ticket_expires_at > now else "unused"
