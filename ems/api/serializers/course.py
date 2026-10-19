from rest_framework import serializers

from ems.api.exceptions import Conflict
from ems.api.serializers.portal_owned import PortalOwnedMixin
from ems.models import Class, Course, TimeTable


class CourseSerializer(PortalOwnedMixin, serializers.ModelSerializer):
    class Meta:
        model = Course
        fields = ["id", "name", "code", "exam_type", "from_portal", "portal_locked"]
        read_only_fields = ["id"]

    def validate_name(self, value):
        name = value.strip()
        if not name:
            raise serializers.ValidationError("Course name is required.")
        return name

    def validate_code(self, value):
        code = value.strip().upper()
        if not code:
            raise serializers.ValidationError("Course code is required.")
        qs = Course.objects.filter(code__iexact=code)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                f"Course with code '{code}' already exists."
            )
        return code

    def validate_exam_type(self, value):
        if value not in ("PBE", "CBE"):
            raise serializers.ValidationError(
                "Invalid exam type. Must be PBE or CBE."
            )
        return value


def assert_course_deletable(course: Course) -> None:
    if TimeTable.objects.filter(course=course).exists():
        raise Conflict(
            "Cannot delete this course: timetable entries reference it. "
            "Clear the timetable first."
        )
    if Class.objects.filter(courses=course).exists():
        raise Conflict(
            "Cannot delete this course: it is assigned to classes. "
            "Remove it from each class first."
        )
