"""Thin wrappers around the existing CSV/Excel/Word export functions.

DRF allows ``APIView.get`` to return a plain ``HttpResponse`` directly —
the renderer is bypassed, so the existing functions in ``ems.csv_gen`` and
``ems.broadsheet`` (which already build correct ``Content-Type`` and
``Content-Disposition`` headers) can be used as-is.
"""

import zipfile
from datetime import datetime
from itertools import groupby

from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.views import APIView

from ems import csv_gen
from ems.branding import load_logo
from ems.export_files import ZIP, SpooledFileResponse, counted_export, spool
from ems.models import SeatArrangement, SystemSettings
from ems.utils import slot_relaxed_course_id
from ems.views import (
    generate_attendance_sheets as legacy_generate_attendance_sheets,
    generate_broadsheet as legacy_generate_broadsheet,
    write_hall_attendance_sheets,
)
from tenancy.permissions import requires_feature


def _slot_params(request):
    """``(date, period)`` from the query string, validated."""
    date = request.query_params.get("date")
    period = request.query_params.get("period")
    if not date or period not in ("AM", "PM"):
        raise ValidationError({"detail": "date and period (AM or PM) are required."})
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValidationError({"detail": "date must be YYYY-MM-DD."}) from exc
    return date, period


def _zip_folder(name: str) -> str:
    """A hall name usable as one folder in a zip path."""
    return "".join("_" if c in '/\\:*?"<>|' else c for c in name).strip() + "/"


class TimetableExportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return counted_export("timetable", csv_gen.export_department_timetable(request))


class DistributionExportView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return counted_export("distribution", csv_gen.export_distribution(request))


class ArrangementExportView(APIView):
    permission_classes = [IsAdminUser, requires_feature("seat_allocation")]

    def get(self, request):
        return counted_export("arrangement", csv_gen.export_arrangements(request))


class AttendanceSheetsView(APIView):
    # DRF's IsAdminUser checks ``is_staff``, which the user serializer sets to
    # ``bool(role)`` — i.e. every admin-side role (super admin, data officer,
    # faculty officer, exam committee) but not department officers, who have a
    # null role. That is exactly the intended audience for attendance sheets.
    permission_classes = [IsAdminUser, requires_feature("seat_allocation")]

    def get(self, request):
        params = request.query_params
        date = params.get("date")
        period = params.get("period")
        hall_id = params.get("hall_id")
        if not all([date, period, hall_id]):
            raise ValidationError(
                {"detail": "date, period, and hall_id are required."}
            )
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValidationError(
                {"detail": "date must be YYYY-MM-DD."}
            ) from exc

        # The legacy view answers every failure with messages.error() plus a
        # redirect to a template route. Over the API that 302 is followed by
        # the caller's blob request, so the HTML landing page was saved as
        # "attendance-<date>.zip" and the UI reported a successful download.
        # Do the same checks up front and fail loudly instead, so the legacy
        # function is only ever entered on a path that returns the real zip.
        placed = SeatArrangement.objects.filter(
            date=date, period=period, hall_id=hall_id, seat_number__isnull=False
        )
        if not placed.exists():
            if SeatArrangement.objects.filter(
                date=date, period=period, hall_id=hall_id
            ).exists():
                raise NotFound(
                    {"detail": "No students are seated in this hall for that slot."}
                )
            raise NotFound(
                {
                    "detail": "No seat allocation found for that hall, date, "
                    "and period. Generate the allocation first."
                }
            )
        return legacy_generate_attendance_sheets(request)


class BulkAttendanceSheetsView(APIView):
    """Every hall's attendance sheets for one slot in a single zip, one
    folder per hall. Same sheets, and the same audience, as the per hall
    export (:class:`AttendanceSheetsView`)."""

    permission_classes = [IsAdminUser, requires_feature("seat_allocation"), requires_feature("exports")]

    def get(self, request):
        date, period = _slot_params(request)
        placed = (
            SeatArrangement.objects.filter(
                date=date, period=period, seat_number__isnull=False
            )
            .select_related("student", "course", "cls", "hall")
            .order_by("hall__name", "hall_id", "course__name", "cls_id", "student__matric_no")
        )
        if not placed.exists():
            raise NotFound(
                {
                    "detail": "No seat allocation found for that date and period. "
                    "Generate the allocation first."
                }
            )
        settings_obj = SystemSettings.objects.first() or SystemSettings.objects.create(
            session="2024/2025", semester="1st Semester"
        )
        # Looked up once for the whole export, not per hall or per sheet.
        logo = load_logo(settings_obj)
        relaxed_course_id = slot_relaxed_course_id(date, period)

        buffer = spool()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for _hall_id, rows in groupby(placed.iterator(chunk_size=2000), key=lambda a: a.hall_id):
                rows = list(rows)
                hall = rows[0].hall
                write_hall_attendance_sheets(
                    zip_file,
                    date=date,
                    period=period,
                    hall=hall,
                    arrangements=rows,
                    settings_obj=settings_obj,
                    relaxed_course_id=relaxed_course_id,
                    logo=logo,
                    folder=_zip_folder(hall.name),
                )
        return SpooledFileResponse(
            buffer, f"Attendance_Sheets_All_Halls_{date}_{period}.zip", ZIP
        )


class BroadsheetView(APIView):
    permission_classes = [IsAdminUser, requires_feature("exports")]

    def get(self, request):
        return legacy_generate_broadsheet(request)
