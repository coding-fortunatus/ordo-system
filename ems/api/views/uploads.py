from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from ems import upload_handlers
from ems.api.permissions import IsDataOfficer, NotPortalManaged, UploadsUnlocked
from ems.models import Class, Department
from tenancy.observability import metrics


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
    # The ``kind`` label of the upload metrics (spec 0006).
    metric_kind: str = ""

    def portal_data_type(self, request):
        return self.data_type

    def finalize_response(self, request, response, *args, **kwargs):
        # A successful upload is counted with its file's size (AC-9).
        f = request.FILES.get("file") if response.status_code < 300 else None
        if f is not None:
            metrics.UPLOADS.add(1, kind=self.metric_kind)
            metrics.UPLOAD_BYTES.add(f.size, kind=self.metric_kind)
        return super().finalize_response(request, response, *args, **kwargs)

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
    metric_kind = "department"

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
    metric_kind = "hall"

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
    metric_kind = "course"

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
    metric_kind = "classes"
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
    metric_kind = "class_courses"
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
    metric_kind = "class_students"
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

    metric_kind = "bulk"

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
