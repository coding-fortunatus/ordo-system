"""OpenTelemetry setup per process (spec 0006, Process roles and setup).

``OBS_PROCESS_ROLE`` decides what a process sets up. ``web`` sets up in
``TenancyConfig.ready()``; workers set up in each prefork child on
``worker_process_init``, so no exporter thread ever crosses a fork. Beat,
management commands and shells set up nothing.

Every span is recorded (``ALWAYS_ON``); the segment processor decides what
is stored, so an inbound ``traceparent`` can neither force nor prevent it.
The instrumentation is installed when tracing or metrics is on: DB and Redis
timings come from the same spans (``builtin.MetricsSpanProcessor``), so
with tracing off spans are still made but none are stored.

Instrumented: Django (the request root), psycopg2 (parameterised SQL only:
bound values are never recorded), Redis (the command name only), httpx and
Celery (trace context in the task headers). WebSocket spans are written by
hand in ``ObservedConsumerMixin``.
"""
import logging

from celery.signals import worker_process_init
from django.conf import settings
from opentelemetry import trace
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from tenancy.observability import context, jobs, metrics
from tenancy.observability.builtin import MetricsSpanProcessor
from tenancy.observability.exporters import SegmentExporter
from tenancy.observability.sampling import SegmentProcessor

logger = logging.getLogger("ordo.observability")

WEB_ROLES = ("web",)
WORKER_ROLES = ("worker", "worker-observability")
# Never traced: probes, files, and the telemetry ingest itself.
EXCLUDED_URLS = "/healthz,/static/,/media/,/api/telemetry/"

_provider = None
# The provider the instrumentors were given, for spans written by hand.
_installed = None


def tracing_enabled():
    return settings.OBSERVABILITY_ENABLED and settings.OBSERVABILITY_TRACING_ENABLED


def setup(role=None):
    """Set up this process for its role. Safe to call more than once."""
    global _provider
    role = role or settings.OBS_PROCESS_ROLE
    if _provider is not None or not (tracing_enabled() or metrics.metrics_enabled()):
        return
    # worker-observability records metrics only (child 0002), never spans.
    if role not in ("web", "worker"):
        return
    try:
        provider = build_provider(role)
        trace.set_tracer_provider(provider)
        set_global_textmap(TraceContextTextMapPropagator())
        install(provider)
        _provider = provider
    except Exception:
        logger.exception("Setting up tracing failed; this process runs untraced")


def build_provider(role, exporter=None):
    resource = Resource.create(
        {"service.name": f"ordo-{role}", "service.version": settings.ORDO_RELEASE}
    )
    provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)
    if metrics.metrics_enabled():
        provider.add_span_processor(MetricsSpanProcessor())
    if tracing_enabled():
        provider.add_span_processor(SegmentProcessor(exporter or SegmentExporter()))
    return provider


def _instrumentors():
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor

    return {
        DjangoInstrumentor: {"excluded_urls": EXCLUDED_URLS, "response_hook": response_hook},
        # No commenter: it would add trace ids to the SQL Postgres logs.
        Psycopg2Instrumentor: {"enable_commenter": False},
        RedisInstrumentor: {},
        HTTPXClientInstrumentor: {},
        CeleryInstrumentor: {},
    }


def install(provider):
    """Instrument everything with ``provider``. ``uninstall()`` undoes it."""
    global _installed
    for instrumentor, options in _instrumentors().items():
        instrumentor().instrument(tracer_provider=provider, **options)
    # After the Celery instrumentor's own, so its span is the current one.
    jobs.connect_span_hooks()
    _installed = provider


def uninstall():
    global _installed
    jobs.disconnect_span_hooks()
    for instrumentor in _instrumentors():
        instrumentor().uninstrument()
    _installed = None


def instrumented():
    """True when this process's DB and Redis calls make spans."""
    return _installed is not None


def tracer(name):
    """A tracer on the installed provider; spans from it are not recorded
    when this process isn't traced."""
    if _installed is None:
        return trace.NoOpTracer()
    return _installed.get_tracer(name)


def response_hook(span, request, response):
    """Name the root after its route, and record the school, user and
    request id (the umbrella value sourcing table). Runs after the view."""
    if not span.is_recording():
        return
    try:
        from tenancy.models import PlatformAdmin
        from tenancy.observability.middleware import _school_id

        match = getattr(request, "resolver_match", None)
        route = match.route if match and match.route else "unmatched"
        span.update_name(f"{request.method} {route}")
        span.set_attribute("http.route", route)
        span.set_attribute("ordo.route", route)
        span.set_attribute("ordo.method", request.method)
        span.set_attribute("ordo.status_code", response.status_code)
        span.set_attribute("ordo.request_id", getattr(request, "ordo_request_id", "") or "")
        school_id = _school_id(request)
        if school_id is not None:
            span.set_attribute("ordo.school_id", school_id)
        user = context.signed_in_user(request)
        if user is not None:
            if isinstance(user, PlatformAdmin):
                span.set_attribute("ordo.platform_admin_id", user.pk)
            elif school_id is not None:
                span.set_attribute("ordo.user_id", user.pk)
    except Exception:
        logger.exception("Recording a request's trace fields failed")


@worker_process_init.connect(dispatch_uid="ordo_observability_worker_tracing")
def worker_ready(**kwargs):
    if settings.OBS_PROCESS_ROLE in WORKER_ROLES:
        setup()
