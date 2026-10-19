"""System-wide audit trail.

``AuditLogMiddleware`` records one ``AuditLog`` row for every authenticated,
state-changing API request (POST/PUT/PATCH/DELETE under ``/api/``), every
authenticated file export (any GET that streams a download), plus a few
explicit auth events (login success/failure, logout). Plain read requests are
never logged. The row is written *after* the view runs so the captured status
code reflects the real outcome.

DRF's authentication runs inside the view and writes the resolved user back
onto the underlying Django request, so ``request.user`` is reliable here even
though token auth has no session middleware behind it.
"""
import json
import re

from django.db import connection
from django_tenants.utils import get_public_schema_name

from ems.models import AuditLog, User
from tenancy.audit import client_ip

# Trailing path segments that name a custom action rather than an object id.
_ACTION_VERBS = {
    "change-password": "Changed password",
    "seed-departments": "Seeded department users",
    "generate": "Generated",
    "generate-all": "Generated (all slots)",
    "clear": "Cleared",
    "retry": "Retried",
    "manual-assign": "Manually assigned seat",
    "reset": "Reset system",
    "enable-bulk-upload": "Enabled bulk upload",
    "publish": "Published",
    "unpublish": "Withdrew",
    "revoke": "Revoked",
    "resend": "Resent",
    "end": "Ended",
}

# A user's own inbox housekeeping (marking notifications read) is not
# system activity.
_UNAUDITED_RESOURCES = frozenset({"notifications"})

# Map URL resource segment -> singular human label.
_RESOURCE_LABELS = {
    "users": "user",
    "invites": "invite",
    "departments": "department",
    "faculties": "faculty",
    "courses": "course",
    "classes": "class",
    "halls": "hall",
    "students": "student",
    "jobs": "job",
    "timetable": "timetable",
    "distribution": "distribution",
    "allocation": "allocation",
    "uploads": "upload",
    "settings": "system settings",
    "branding": "branding",
    "constraints": "generation constraints",
    "support": "support session",
    "portal-sync": "portal sync",
}

_METHOD_VERBS = {
    "POST": "Created",
    "PUT": "Updated",
    "PATCH": "Updated",
    "DELETE": "Deleted",
}

_AUDITED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Friendly labels for the known file-download routes (matched on the first two
# path segments below ``/api/``). Any other attachment download falls back to a
# path-derived label, so export endpoints added later are audited automatically.
_EXPORT_LABELS = {
    ("exports", "timetable"): "Exported timetable",
    ("exports", "distribution"): "Exported distribution",
    ("exports", "arrangement"): "Exported seat arrangements",
    ("exports", "attendance-sheets"): "Exported attendance sheets",
    ("exports", "broadsheet"): "Exported broadsheet",
    ("directory", "export"): "Exported directory",
    ("users", "export"): "Exported departmental staff",
    ("audit-logs", "export"): "Exported audit log",
}

_FILENAME_RE = re.compile(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", re.IGNORECASE)


def set_audit(request, action, *, object_type, object_id="", **metadata):
    """Name the audit row for this request instead of the path derived
    description. For a view whose actions the path can't tell apart (a
    connection change vs a secret change). ``metadata`` must hold no
    secrets and no row content."""
    target = getattr(request, "_request", request)
    target._ordo_audit = (action, object_type, str(object_id or ""), metadata)


def _segments(path):
    """Path segments below ``/api/`` — ['users', '5', 'change-password']."""
    parts = [p for p in path.strip("/").split("/") if p]
    if parts and parts[0] == "api":
        parts = parts[1:]
    return parts


def _content_disposition(response):
    try:
        return response.get("Content-Disposition", "") or ""
    except Exception:
        return ""


def _is_download(response):
    """True when the response streams a file attachment (i.e. an export)."""
    return "attachment" in _content_disposition(response).lower()


def _export_action(segments):
    """Human label for an export GET, by path; falls back for unknown routes."""
    label = _EXPORT_LABELS.get(tuple(segments[:2]))
    if label:
        return label
    resource = _RESOURCE_LABELS.get(segments[0], segments[0]) if segments else "data"
    return f"Exported {resource}"


def _export_metadata(request, response):
    """Capture the export's filters (query params) and downloaded filename."""
    meta = {}
    params = {k: v[:200] for k, v in request.GET.items()}
    if params:
        meta["params"] = params
    match = _FILENAME_RE.search(_content_disposition(response))
    if match:
        meta["filename"] = match.group(1).strip().strip('"')
    return meta


def _describe(request, segments):
    """Return (action_text, object_type, object_id) for a mutating request."""
    method = request.method
    if not segments:
        return (f"{method} {request.path}", "", "")

    # Auth events are special-cased by the caller; this handles entity routes.
    resource = segments[0]
    label = _RESOURCE_LABELS.get(resource, resource)

    object_id = ""
    last = segments[-1]
    # A trailing numeric segment is an object id; a known verb is a sub-action.
    if last in _ACTION_VERBS:
        verb = _ACTION_VERBS[last]
        # Pull an object id from the segment just before the verb if present.
        if len(segments) >= 3 and segments[-2].isdigit():
            object_id = segments[-2]
        suffix = f" for {label} #{object_id}" if object_id else f" {label}"
        if last in ("generate", "generate-all", "clear"):
            return (f"{verb} {label}", label, object_id)
        if last in (
            "reset",
            "enable-bulk-upload",
            "seed-departments",
            "manual-assign",
        ):
            return (verb, label, object_id)
        return (f"{verb}{suffix}", label, object_id)

    if last.isdigit():
        object_id = last

    verb = _METHOD_VERBS.get(method, method)
    if object_id:
        return (f"{verb} {label} #{object_id}", label, object_id)
    return (f"{verb} {label}", label, object_id)


class AuditLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Grab the attempted email up front for login (the body is consumed by
        # the view); Django caches request.body so the view still reads it.
        login_email = None
        is_login = request.path == "/api/auth/login/" and request.method == "POST"
        if is_login:
            try:
                login_email = (json.loads(request.body or b"{}") or {}).get("email")
            except (ValueError, TypeError):
                login_email = None

        response = self.get_response(request)

        try:
            self._record(request, response, is_login, login_email)
        except Exception:
            # Auditing must never break the request it is observing.
            pass

        return response

    def _record(self, request, response, is_login, login_email):
        path = request.path or ""
        if not path.startswith("/api/"):
            return
        # console.<base> and api.<base> have no school, so no AuditLog table.
        if connection.schema_name == get_public_schema_name():
            return

        method = (request.method or "").upper()
        status_code = getattr(response, "status_code", None)
        segments = _segments(path)
        user = getattr(request, "user", None)
        authed = bool(user and getattr(user, "is_authenticated", False))

        # --- Auth events (logged regardless of the generic method filter) ---
        # The login view authenticates with no DRF auth class, so request.user
        # stays anonymous even on success — judge the outcome by status code and
        # resolve the user from the submitted email.
        if is_login:
            success = bool(status_code and 200 <= status_code < 300)
            email = login_email or ""
            matched = (
                User.objects.filter(email__iexact=email).first()
                if (success and email)
                else None
            )
            if matched:
                email = matched.email
            self._write(
                user=matched,
                email=email,
                action="Signed in" if success else "Failed sign-in attempt",
                request=request,
                response=response,
                object_type="auth",
                object_id="",
            )
            return

        if path == "/api/auth/logout/" and method == "POST":
            if authed:
                self._write(
                    user=user,
                    email=user.email,
                    action="Signed out",
                    request=request,
                    response=response,
                    object_type="auth",
                    object_id="",
                )
            return

        # --- Exports: any authenticated GET that streams a file download ---
        # Detected by the attachment header so every export route (current and
        # future) is captured without enumerating them. Error responses (e.g.
        # a 400 with no attachment) carry no disposition and are skipped.
        if method == "GET" and authed and _is_download(response):
            self._write(
                user=user,
                email=user.email,
                action=_export_action(segments),
                request=request,
                response=response,
                object_type="export",
                object_id="",
                metadata=_export_metadata(request, response),
            )
            return

        # --- Generic mutations: only for authenticated, state-changing calls ---
        if method not in _AUDITED_METHODS or not authed:
            return
        if segments and segments[0] in _UNAUDITED_RESOURCES:
            return

        metadata = None
        if named := getattr(request, "_ordo_audit", None):
            action, object_type, object_id, metadata = named
        else:
            action, object_type, object_id = _describe(request, segments)
        self._write(
            user=user,
            email=user.email,
            action=action,
            request=request,
            response=response,
            object_type=object_type,
            object_id=object_id,
            metadata=metadata,
        )

    def _write(
        self,
        *,
        user,
        email,
        action,
        request,
        response,
        object_type,
        object_id,
        metadata=None,
    ):
        status_code = getattr(response, "status_code", None)
        # Everything Ordo support does is marked (spec 0003, child 0006).
        if getattr(user, "support_expires_at", None) is not None:
            action = f"[Ordo support] {action}"
        AuditLog.objects.create(
            user=user,
            user_email=(email or "")[:254],
            action=action[:255],
            method=(request.method or "")[:10],
            path=(request.path or "")[:512],
            object_type=(object_type or "")[:100],
            object_id=(str(object_id) if object_id else "")[:64],
            status_code=status_code,
            ip_address=client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:512],
            metadata=metadata or {},
        )
