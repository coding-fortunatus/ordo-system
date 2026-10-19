from django.db.models import Q
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response

from ems.api.exceptions import Conflict
from ems.api.pagination import AllowUnpaginatedMixin
from ems.api.permissions import CanManageDepartmentScoped, PortalManagedViewSetMixin
from ems.api.serializers.cls import (
    ClassCourseAssignSerializer,
    ClassSerializer,
    assert_class_deletable,
)
from ems.api.serializers.course import CourseSerializer
from ems.models import Class, Course, TimeTable
from ems.portal_sync import managed


class ClassViewSet(PortalManagedViewSetMixin, AllowUnpaginatedMixin, viewsets.ModelViewSet):
    portal_type = "classes"
    serializer_class = ClassSerializer

    def get_queryset(self):
        qs = (
            Class.objects.all()
            .select_related("department")
            .prefetch_related("courses")
            .with_student_count()
            .order_by("department__name", "name")
        )
        params = self.request.query_params
        if dept := params.get("department"):
            qs = qs.filter(department__slug=dept.upper())
        if query := params.get("query"):
            # Search across the class name, its department, and any course code
            # assigned to it so one box finds classes by any visible field.
            qs = qs.filter(
                Q(name__icontains=query)
                | Q(department__name__icontains=query)
                | Q(department__slug__icontains=query)
                | Q(courses__code__icontains=query)
            ).distinct()
        # Non-admins only see their own department
        user = self.request.user
        if user.is_authenticated and not user.is_staff and user.department_id:
            qs = qs.filter(department_id=user.department_id)
        return qs

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated()]
        return [CanManageDepartmentScoped()]

    def _enforce_department(self, serializer):
        """Non-admins with a department can only write to their own department."""
        user = self.request.user
        if user.is_staff or not user.department_id:
            return
        target_dept = serializer.validated_data.get("department")
        if target_dept and target_dept.id != user.department_id:
            raise PermissionDenied(
                "You can only manage classes in your own department."
            )

    def perform_create(self, serializer):
        user = self.request.user
        if not user.is_staff and user.department_id:
            from ems.models import Department

            serializer.save(department=Department.objects.get(pk=user.department_id))
            return
        serializer.save()

    def perform_update(self, serializer):
        self._enforce_department(serializer)
        serializer.save()

    def perform_destroy(self, instance):
        assert_class_deletable(instance)
        instance.delete()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(
        detail=True,
        methods=["post"],
        url_path="courses",
        permission_classes=[CanManageDepartmentScoped],
    )
    def add_course(self, request, pk=None):
        cls = self.get_object()
        managed.assert_links_editable(cls)
        serializer = ClassCourseAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course = serializer.resolve_course()
        if cls.courses.filter(pk=course.pk).exists():
            raise Conflict(f"Course '{course.code}' is already in this class.")
        cls.courses.add(course)
        return Response(CourseSerializer(course).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"courses/(?P<course_id>[^/.]+)",
        permission_classes=[CanManageDepartmentScoped],
    )
    def manage_course(self, request, pk=None, course_id=None):
        cls = self.get_object()
        try:
            course = cls.courses.get(pk=course_id)
        except Course.DoesNotExist as exc:
            raise NotFound("This course is not assigned to the class.") from exc

        if request.method == "DELETE":
            managed.assert_links_editable(cls)
            if TimeTable.objects.filter(course=course).exists():
                raise Conflict(
                    "Cannot remove this course: timetable entries reference it. "
                    "Clear the timetable first."
                )
            cls.courses.remove(course)
            return Response(status=status.HTTP_204_NO_CONTENT)

        # PATCH: edit course in class context (same constraints as global edit)
        serializer = CourseSerializer(course, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
