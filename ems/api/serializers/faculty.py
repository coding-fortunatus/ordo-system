from rest_framework import serializers

from ems.api.exceptions import Conflict
from ems.api.serializers.portal_owned import PortalOwnedMixin
from ems.models import Department, Faculty
from ems.portal_sync import managed


class FacultyDepartmentBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "name", "slug"]


class FacultySerializer(PortalOwnedMixin, serializers.ModelSerializer):
    department_count = serializers.SerializerMethodField()
    departments = FacultyDepartmentBriefSerializer(many=True, read_only=True)
    department_ids = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        many=True,
        write_only=True,
        required=False,
    )

    class Meta:
        model = Faculty
        fields = [
            "id",
            "name",
            "slug",
            "department_count",
            "departments",
            "department_ids",
            "from_portal",
            "portal_locked",
        ]
        read_only_fields = ["id"]
        extra_kwargs = {
            "slug": {"validators": []},
            "name": {"validators": []},
        }

    def get_department_count(self, obj):
        return getattr(obj, "_department_count", obj.departments.count())

    def validate_name(self, value):
        name = value.strip()
        if not name:
            raise serializers.ValidationError("Faculty name is required.")
        qs = Faculty.objects.filter(name__iexact=name)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                f"Faculty '{name}' already exists."
            )
        return name

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if "department_ids" in attrs:
            managed.assert_faculty_departments_editable(
                self.instance, attrs["department_ids"], self.context
            )
        return attrs

    def validate_slug(self, value):
        slug = value.strip().upper()
        if not slug:
            raise serializers.ValidationError("Faculty code is required.")
        qs = Faculty.objects.filter(slug=slug)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                f"Faculty code '{slug}' is already in use."
            )
        return slug

    def create(self, validated_data):
        departments = validated_data.pop("department_ids", [])
        faculty = Faculty.objects.create(**validated_data)
        if departments:
            Department.objects.filter(
                pk__in=[d.pk for d in departments]
            ).update(faculty=faculty)
        return faculty

    def update(self, instance, validated_data):
        departments = validated_data.pop("department_ids", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if departments is not None:
            instance.departments.exclude(
                pk__in=[d.pk for d in departments]
            ).update(faculty=None)
            Department.objects.filter(
                pk__in=[d.pk for d in departments]
            ).update(faculty=instance)
        return instance


def assert_faculty_deletable(faculty: Faculty) -> None:
    if faculty.departments.exists():
        raise Conflict(
            "Cannot delete this faculty: departments are still attached. "
            "Detach the departments first."
        )
