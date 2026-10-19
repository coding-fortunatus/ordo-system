from django.db.models import Count
from rest_framework import status, viewsets
from rest_framework.response import Response

from ems.api.pagination import AllowUnpaginatedMixin
from ems.api.permissions import IsFacultyOfficer, PortalManagedViewSetMixin
from ems.api.serializers.faculty import (
    FacultySerializer,
    assert_faculty_deletable,
)
from ems.models import Faculty


class FacultyViewSet(PortalManagedViewSetMixin, AllowUnpaginatedMixin, viewsets.ModelViewSet):
    """CRUD for Faculty. Admin-only. Lookup by slug."""

    portal_type = "faculties"
    serializer_class = FacultySerializer
    lookup_field = "slug"
    lookup_value_regex = "[-A-Za-z0-9_]+"

    def get_queryset(self):
        qs = Faculty.objects.all().order_by("name").prefetch_related("departments")
        qs = qs.annotate(_department_count=Count("departments", distinct=True))
        query = self.request.query_params.get("query")
        if query:
            qs = qs.filter(name__icontains=query) | qs.filter(slug__icontains=query)
        return qs

    def get_permissions(self):
        return [IsFacultyOfficer()]

    def perform_destroy(self, instance):
        assert_faculty_deletable(instance)
        instance.delete()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)
