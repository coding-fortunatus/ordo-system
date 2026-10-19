from django.db.models import Q
from rest_framework import permissions, status, viewsets
from rest_framework.response import Response

from ems.api.permissions import IsDataOfficer, PortalManagedViewSetMixin
from ems.api.serializers.course import (
    CourseSerializer,
    assert_course_deletable,
)
from ems.models import Course


class CourseViewSet(PortalManagedViewSetMixin, viewsets.ModelViewSet):
    portal_type = "courses"
    serializer_class = CourseSerializer

    def get_queryset(self):
        qs = Course.objects.all().order_by("code")
        params = self.request.query_params
        query = params.get("query")
        if query:
            qs = qs.filter(Q(name__icontains=query) | Q(code__icontains=query))
        exam_type = params.get("exam_type")
        if exam_type:
            qs = qs.filter(exam_type=exam_type)
        # Courses link to departments only through their classes, so filter on
        # the reverse M2M (Course.courses -> Class.department).
        if department := params.get("department"):
            qs = qs.filter(courses__department__slug=department.upper())
        user = self.request.user
        if user.is_authenticated and not user.is_staff and user.department_id:
            qs = qs.filter(courses__department_id=user.department_id)
        return qs.distinct()

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [permissions.IsAuthenticated()]
        return [IsDataOfficer()]

    def perform_destroy(self, instance):
        assert_course_deletable(instance)
        instance.delete()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)
