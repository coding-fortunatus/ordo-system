"""Hall Directory + VISA preview (JSON) and export (PDF) endpoints."""

from datetime import datetime

from django.http import HttpResponse
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ems import directory, directory_export, pdf_gen
from ems.api.views.system import _get_or_create_settings
from ems.export_files import counted_export

_DOC_CHOICES = {"hall", "visa"}
_SCOPE_CHOICES = {"slot", "week", "duration"}
# fmt -> (extension, content type)
_FORMATS = {
    "pdf": ("pdf", "application/pdf"),
    "docx": (
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "csv": ("csv", "text/csv"),
}


def _parse_params(request):
    doc = (request.GET.get("doc") or "hall").lower()
    scope = (request.GET.get("scope") or "slot").lower()
    period = (request.GET.get("period") or "AM").upper()
    if doc not in _DOC_CHOICES:
        raise ValidationError({"detail": "doc must be 'hall' or 'visa'."})
    if scope not in _SCOPE_CHOICES:
        raise ValidationError(
            {"detail": "scope must be 'slot', 'week', or 'duration'."}
        )
    if period not in ("AM", "PM"):
        raise ValidationError({"detail": "period must be 'AM' or 'PM'."})

    date_obj = None
    date_str = request.GET.get("date")
    if date_str and date_str != "None":
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValidationError({"detail": "date must be YYYY-MM-DD."}) from exc
    if scope in ("slot", "week") and not date_obj:
        raise ValidationError(
            {"detail": "date is required for 'slot' and 'week' scope."}
        )
    return doc, scope, date_obj, period


class DirectoryPreviewView(APIView):
    """JSON preview of the Hall Directory / VISA for the chosen scope."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        doc, scope, date_obj, period = _parse_params(request)
        slots = directory.build_payload(doc, scope, date_obj, period)
        return Response({"doc": doc, "scope": scope, "slots": slots})


class DirectoryExportView(APIView):
    """Export the Hall Directory / VISA as PDF, DOCX, or CSV."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        doc, scope, date_obj, period = _parse_params(request)
        # Deliberately "fmt", not "format": DRF reserves "format" as its
        # renderer-negotiation query parameter (URL_FORMAT_OVERRIDE), so
        # ?format=pdf makes it hunt for a renderer named "pdf" and 404 before
        # this view ever runs.
        fmt = (request.GET.get("fmt") or "pdf").lower()
        if fmt not in _FORMATS:
            raise ValidationError(
                {"detail": f"fmt must be one of: {', '.join(sorted(_FORMATS))}."}
            )
        payload = directory.build_payload(doc, scope, date_obj, period)

        if fmt == "csv":
            content = directory_export.build_csv(doc, payload)
        elif fmt == "docx":
            content = directory_export.build_docx(
                doc, payload, _get_or_create_settings()
            )
        else:
            content = pdf_gen.build_pdf(doc, payload, _get_or_create_settings())

        extension, content_type = _FORMATS[fmt]
        label = "hall-directory" if doc == "hall" else "visa"
        filename = f"{label}-{scope}.{extension}"
        response = HttpResponse(content, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return counted_export("directory", response)
