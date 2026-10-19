from rest_framework import serializers

from ems.api.exceptions import Conflict
from ems.api.serializers.portal_owned import PortalOwnedMixin
from ems.models import Class, Department, Faculty, User


class DepartmentSerializer(PortalOwnedMixin, serializers.ModelSerializer):
    class_count = serializers.SerializerMethodField()
    student_count = serializers.SerializerMethodField()
    faculty = serializers.PrimaryKeyRelatedField(
        queryset=Faculty.objects.all(),
        required=False,
        allow_null=True,
    )
    faculty_name = serializers.CharField(
        source="faculty.name", read_only=True, default=None
    )
    faculty_slug = serializers.CharField(
        source="faculty.slug", read_only=True, default=None
    )

    class Meta:
        model = Department
        fields = [
            "id",
            "name",
            "slug",
            "class_count",
            "student_count",
            "faculty",
            "faculty_name",
            "faculty_slug",
            "from_portal",
            "portal_locked",
        ]
        read_only_fields = ["id"]
        extra_kwargs = {
            "slug": {"validators": []},  # we enforce uniqueness explicitly below
        }

    def get_class_count(self, obj):
        return getattr(obj, "_class_count", obj.class_dep.count())

    def get_student_count(self, obj):
        return getattr(obj, "_student_count", obj.student_set.count())

    def validate_name(self, value):
        name = value.strip()
        if not name:
            raise serializers.ValidationError("Department name is required.")
        qs = Department.objects.filter(name__iexact=name)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                f"Department '{name}' already exists."
            )
        return name

    def validate_slug(self, value):
        slug = value.strip().upper()
        if not slug:
            raise serializers.ValidationError("Department code is required.")
        qs = Department.objects.filter(slug=slug)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                f"Department code '{slug}' is already in use."
            )
        return slug


def assert_department_deletable(department: Department) -> None:
    """Mirror the existing delete_department guards."""
    if Class.objects.filter(department=department).exists():
        raise Conflict(
            "Cannot delete this department: classes are still attached. "
            "Delete the classes first."
        )
    if User.objects.filter(department=department).exists():
        raise Conflict(
            "Cannot delete this department: users are still assigned. "
            "Reassign the users first."
        )
