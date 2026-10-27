"""Request side capture: the request id, per school counters (AC-7), and
unhandled exceptions in views (AC-1). Also the request's telemetry context,
the ``X-Trace-ID`` reference header, the HTTP metrics and daily actives
(spec 0006)."""
import re
import sys
import time
import uuid

from django.core.signals import got_request_exception
from django.dispatch import receiver
from opentelemetry import trace
from opentelemetry.trace import format_trace_id

from tenancy.observability import builtin, capture, context, counters, metrics, otel

REQUEST_ID_HEADER = "X-Request-ID"
TRACE_ID_HEADER = "X-Trace-ID"
_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
# Not measured: probes, files, and the telemetry ingest (as for traces).
UNMEASURED = tuple(otel.EXCLUDED_URLS.split(","))


def _request_id(request):
    incoming = request.META.get("HTTP_X_REQUEST_ID", "").lower()
    return incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex


def _school_id(request):
    from django_tenants.utils import get_public_schema_name

    tenant = getattr(request, "tenant", None)
    if tenant is None or tenant.schema_name == get_public_schema_name():
        return None
    return tenant.pk


class RequestObservabilityMiddleware:
    """Placed right after ``SchoolMiddleware``, so the school is known."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.ordo_request_id = _request_id(request)
        school_id = _school_id(request)
        measured = metrics.metrics_enabled() and not request.path.startswith(UNMEASURED)
        queries = [0] if measured else None
        tokens = (
            context.request_school_id.set(school_id),
            context.request_id.set(request.ordo_request_id),
            context.request.set(request),
            builtin.request_queries.set(queries),
        )
        started = time.perf_counter()
        try:
            response = self.get_response(request)
            if measured:
                self._measure(request, response, school_id, started, queries)
        finally:
            context.request_school_id.reset(tokens[0])
            context.request_id.reset(tokens[1])
            context.request.reset(tokens[2])
            builtin.request_queries.reset(tokens[3])
        response[REQUEST_ID_HEADER] = request.ordo_request_id
        # The root span's trace id, kept or not: the reference users quote.
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid and otel.tracing_enabled():
            response[TRACE_ID_HEADER] = format_trace_id(span_context.trace_id)
        try:
            if capture.enabled() and request.path.startswith("/api/"):
                counts = {"requests": 1}
                if 400 <= response.status_code < 500:
                    counts["client_errors"] = 1
                counters.incr(_school_id(request), **counts)
        except Exception:
            capture.logger.exception("Counting a request failed")
        return response


    @staticmethod
    def _measure(request, response, school_id, started, queries):
        try:
            # Query counts come from the DB spans: none without them.
            counted = queries[0] if otel.instrumented() else None
            builtin.record_request(request, response, (time.perf_counter() - started) * 1000, counted)
            if school_id is not None and request.path.startswith("/api/"):
                user = context.signed_in_user(request)
                from tenancy.models import PlatformAdmin

                if user is not None and not isinstance(user, PlatformAdmin):
                    metrics.mark_active(school_id, user.pk)
        except Exception:
            capture.logger.exception("Measuring a request failed")


def request_context(request):
    """The AC-8 request fields: method, route, user, role, support, id."""
    user = getattr(request, "user", None)
    signed_in = bool(user is not None and getattr(user, "is_authenticated", False))
    match = getattr(request, "resolver_match", None)
    return {
        "method": request.method,
        "route": (match.route if match and match.route else "<unresolved>"),
        "status": 500,
        "user_id": user.pk if signed_in else None,
        "role": getattr(user, "role", None) if signed_in else None,
        "support": (getattr(user, "support_expires_at", None) is not None) if signed_in else None,
        "request_id": getattr(request, "ordo_request_id", None),
    }


@receiver(got_request_exception, dispatch_uid="ordo_observability_request_exception")
def request_exception(sender, request=None, **kwargs):
    try:
        exc = sys.exc_info()[1]
        if exc is None or request is None:
            return
        context = request_context(request)
        capture.capture_exception(exc, "api", culprit=context["route"], **context)
    except Exception:
        capture.logger.exception("Capturing a request exception failed")
