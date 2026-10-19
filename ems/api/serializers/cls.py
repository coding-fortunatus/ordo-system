from rest_framework import serializers

from ems.api.exceptions import Conflict
from ems.api.serializers.course import CourseSerializer
from ems.api.serializers.department import DepartmentSerializer
from ems.api.serializers.portal_owned import PortalOwnedMixin
from ems.models import Class, Course, Department, Student, TimeTable


class ClassSerializer(PortalOwnedMixin, serializers.ModelSerializer):
    department = DepartmentSerializer(read_only=True)
    department_id = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        source="department",
        write_only=True,
    )
    courses = CourseSerializer(many=True, read_only=True)
    student_count = serializers.SerializerMethodField()
    # Read-only resolved VISA code (the override if set, else auto-derived).
    visa_label = serializers.CharField(read_only=True)
    # Read-only "DEPT name" label (e.g. "AC ND II").
    full_label = serializers.CharField(read_only=True)

    class Meta:
        model = Class
        fields = [
            "id",
            "name",
            "size",
            "department",
            "department_id",
            "courses",
            "student_count",
            "is_active",
            "visa_code",
            "visa_label",
            "full_label",
            "from_portal",
            "portal_locked",
        ]
        # ``size`` is a display copy of the student count, kept in step on
        # every student write; a written value is ignored.
        read_only_fields = ["id", "size"]
        extra_kwargs = {"visa_code": {"required": False, "allow_blank": True}}

    def get_student_count(self, obj):
        return obj.student_count

    def validate_name(self, value):
        if value is None:
            return value
        name = value.strip()
        if not name:
            raise serializers.ValidationError("Class name is required.")
        return name

    def validate(self, attrs):
        attrs = super().validate(attrs)
        name = attrs.get("name") or (self.instance.name if self.instance else None)
        department = attrs.get("department") or (
            self.instance.department if self.instance else None
        )
        if name and department:
            qs = Class.objects.filter(name__iexact=name, department=department)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {
                        "name": (
                            f"Class '{name}' already exists in {department.name}."
                        )
                    }
                )
        return attrs


def assert_class_deletable(cls: Class) -> None:
    if Student.objects.filter(level=cls).exists():
        raise Conflict(
            "Cannot delete this class: students are still enrolled. "
            "Delete the students first."
        )
    if TimeTable.objects.filter(class_obj=cls).exists():
        raise Conflict(
            "Cannot delete this class: timetable entries reference it. "
            "Clear the timetable first."
        )


class ClassCourseAssignSerializer(serializers.Serializer):
    """For POST /classes/{id}/courses/ — accepts either an existing course id
    or a new course payload (name + code [+ exam_type])."""

    course_id = serializers.IntegerField(required=False)
    name = serializers.CharField(required=False, allow_blank=True)
    code = serializers.CharField(required=False, allow_blank=True)
    exam_type = serializers.ChoiceField(
        choices=("PBE", "CBE"), required=False, default="PBE"
    )

    def validate(self, attrs):
        course_id = attrs.get("course_id")
        code = (attrs.get("code") or "").strip().upper()
        name = (attrs.get("name") or "").strip()
        if not course_id and not (code and name):
            raise serializers.ValidationError(
                "Either course_id or both name and code must be provided."
            )
        attrs["code"] = code
        attrs["name"] = name
        return attrs

    def resolve_course(self) -> Course:
        data = self.validated_data
        if data.get("course_id"):
            try:
                return Course.objects.get(pk=data["course_id"])
            except Course.DoesNotExist as exc:
                raise serializers.ValidationError(
                    {"course_id": "Course not found."}
                ) from exc
        existing = Course.objects.filter(code__iexact=data["code"]).first()
        if existing:
            return existing
        return Course.objects.create(
            name=data["name"],
            code=data["code"],
            exam_type=data.get("exam_type") or "PBE",
        )
