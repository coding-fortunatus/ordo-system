from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from ems import upload_handlers
from ems.api.permissions import IsDataOfficer, NotPortalManaged, UploadsUnlocked
from ems.models import Class, Department


def _require_dept_access(request, department: Department) -> None:
    """Allow data managers, or department officers who belong to the target
    department. Anyone else is denied."""
    user = request.user
    if user.can_manage_data:
        return
    if user.department_id and user.department_id == department.id:
        return
    raise PermissionDenied(
        "You can only upload data for your own department."
    )


class _BaseUploadView(APIView):
    permission_classes = [IsDataOfficer, UploadsUnlocked, NotPortalManaged]
    parser_classes = [MultiPartParser, FormParser]

    handler_attr: str = ""
    # The data type the upload writes; the portal may own it (spec 0005).
    data_type: str | None = None

    def portal_data_type(self, request):
        return self.data_type

    def _file(self, request):
        f = request.FILES.get("file")
        if not f:
            return Response(
                {"detail": "No file uploaded — attach a CSV under the 'file' field."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return f


class DepartmentUploadView(_BaseUploadView):
    data_type = "departments"

    def post(self, request):
        f = self._file(request)
        if isinstance(f, Response):
            return f
        try:
            result = upload_handlers.upload_departments(f)
        except upload_handlers.UploadError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class HallUploadView(_BaseUploadView):
    def post(self, request):
        f = self._file(request)
        if isinstance(f, Response):
            return f
        try:
            result = upload_handlers.upload_halls(f)
        except upload_handlers.UploadError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class CourseUploadView(_BaseUploadView):
    data_type = "courses"

    def post(self, request):
        f = self._file(request)
        if isinstance(f, Response):
            return f
        try:
            result = upload_handlers.upload_courses(f)
        except upload_handlers.UploadError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class ClassesForDepartmentUploadView(_BaseUploadView):
    data_type = "classes"
    permission_classes = [permissions.IsAuthenticated, UploadsUnlocked, NotPortalManaged]

    def post(self, request, dept_slug):
        department = get_object_or_404(Department, slug=dept_slug)
        _require_dept_access(request, department)
        f = self._file(request)
        if isinstance(f, Response):
            return f
        try:
            result = upload_handlers.upload_classes_for_department(f, department)
        except upload_handlers.UploadError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class ClassCoursesUploadView(_BaseUploadView):
    data_type = "courses"
    permission_classes = [permissions.IsAuthenticated, UploadsUnlocked, NotPortalManaged]

    def post(self, request, class_id):
        cls = get_object_or_404(Class, pk=class_id)
        _require_dept_access(request, cls.department)
        f = self._file(request)
        if isinstance(f, Response):
            return f
        try:
            result = upload_handlers.upload_class_courses(f, cls)
        except upload_handlers.UploadError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class ClassStudentsUploadView(_BaseUploadView):
    data_type = "students"
    permission_classes = [permissions.IsAuthenticated, UploadsUnlocked, NotPortalManaged]

    def post(self, request, class_id):
        cls = get_object_or_404(Class, pk=class_id)
        _require_dept_access(request, cls.department)
        f = self._file(request)
        if isinstance(f, Response):
            return f
        try:
            result = upload_handlers.upload_class_students(f, cls)
        except upload_handlers.UploadError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class BulkUploadView(_BaseUploadView):
    """POST /api/uploads/bulk/ with `upload_type` field selecting the dispatcher."""

    def portal_data_type(self, request):
        upload_type = request.data.get("upload_type", "").strip()
        return upload_type if upload_type in ("departments", "courses") else None

    def post(self, request):
        f = self._file(request)
        if isinstance(f, Response):
            return f
        upload_type = request.data.get("upload_type", "").strip()
        handler = upload_handlers.BULK_HANDLERS.get(upload_type)
        if not handler:
            return Response(
                {
                    "detail": (
                        "upload_type must be one of: "
                        + ", ".join(sorted(upload_handlers.BULK_HANDLERS))
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = handler(f)
        except upload_handlers.UploadError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"upload_type": upload_type, **result})
