"""Built in metrics recorded where requests, queries and jobs happen (spec
0006, child 0002, Built in metrics).

DB and Redis timings come from the instrumentors' spans, through
``MetricsSpanProcessor``: the instrumentation is installed when tracing or
metrics is on, so turning tracing off keeps these numbers (AC-7). Requests
are recorded by ``RequestObservabilityMiddleware`` and jobs by the Celery
signals here. Observability tasks record nothing.
"""
import contextvars
import logging
import time

from celery.signals import task_failure, task_postrun, task_prerun, task_retry
from opentelemetry.sdk.trace import SpanProcessor

from tenancy.observability import context, jobs, metrics
from tenancy.observability.capture import EXPECTED_JOB_FAILURES

logger = logging.getLogger("ordo.observability")

SLOW_QUERY_MS = 500
# Blocking reads wait for work by design (the Celery broker, Channels): their
# time is idle, not latency.
BLOCKING_COMMANDS = frozenset({"BLPOP", "BRPOP", "BRPOPLPUSH", "BLMOVE", "BZPOPMIN", "BZPOPMAX", "XREAD", "XREADGROUP"})
OPERATIONS = {"SELECT": "select", "INSERT": "insert", "UPDATE": "update", "DELETE": "delete"}

# Queries run by the request being served: a one item list, so a view run in
# another thread (``sync_to_async`` copies the context) adds to the same one.
request_queries = contextvars.ContextVar("ordo_request_queries", default=None)


def _first_word(text):
    words = str(text or "").split(None, 1)
    return words[0].upper() if words else ""


class MetricsSpanProcessor(SpanProcessor):
    """Times every DB query and Redis call from its span."""

    def on_end(self, span):
        try:
            attributes = span.attributes or {}
            system = attributes.get("db.system")
            if not system:
                return
            ms = (span.end_time - span.start_time) / 1e6
            if system == "redis":
                command = _first_word(span.name)
                if command and command not in BLOCKING_COMMANDS:
                    metrics.REDIS_DURATION.record(ms, command=command)
                return
            statement = attributes.get("db.statement") or attributes.get("db.query.text") or span.name
            operation = OPERATIONS.get(_first_word(statement), "other")
            metrics.DB_QUERY_DURATION.record(ms, operation=operation)
            if ms > SLOW_QUERY_MS:
                metrics.DB_SLOW_QUERIES.add(1, operation=operation)
            counted = request_queries.get()
            if counted is not None:
                counted[0] += 1
        except Exception:
            logger.exception("Recording a span's metrics failed")


def record_request(request, response, ms, queries):
    """The request's HTTP metrics and query count (AC-8)."""
    match = getattr(request, "resolver_match", None)
    route = match.route if match and match.route else "unmatched"
    labels = {
        "route": route,
        "method": metrics.http_method(request.method),
        "status_class": metrics.status_class(response.status_code),
    }
    metrics.HTTP_REQUESTS.add(1, **labels)
    metrics.HTTP_DURATION.record(ms, **labels)
    if queries is not None:
        metrics.DB_QUERIES.record(queries, route=route)


# Task id -> what its postrun needs: start, school, outcome.
_runs = {}


def _queue(task):
    info = getattr(task.request, "delivery_info", None) or {}
    return info.get("routing_key") or "celery"


@task_prerun.connect(dispatch_uid="ordo_observability_task_metrics_start")
def task_started(task_id=None, task=None, **_):
    if task is None or task_id is None or jobs.is_observability_task(task) or not metrics.metrics_enabled():
        return
    try:
        school_id = context.school_id()
        _runs[task_id] = [time.monotonic(), school_id, "succeeded"]
        sent_at = jobs.header(task, jobs.SENT_AT_HEADER)
        if sent_at and not task.request.is_eager:
            with context.for_school(school_id):
                metrics.QUEUE_WAIT.record(
                    max(0.0, time.time() * 1000 - float(sent_at)), queue=_queue(task), task=task.name
                )
    except Exception:
        logger.exception("Recording a task's start failed")


@task_failure.connect(dispatch_uid="ordo_observability_task_metrics_failed")
def task_failed(task_id=None, exception=None, **_):
    run = _runs.get(task_id)
    if run is not None:
        expected = exception is not None and jobs.exception_class_path(exception) in EXPECTED_JOB_FAILURES
        run[2] = "expected" if expected else "failed"


@task_retry.connect(dispatch_uid="ordo_observability_task_metrics_retried")
def task_retried(request=None, **_):
    run = _runs.get(getattr(request, "id", None))
    if run is not None:
        run[2] = "retried"


@task_postrun.connect(dispatch_uid="ordo_observability_task_metrics_finished")
def task_finished(task_id=None, task=None, **_):
    run = _runs.pop(task_id, None)
    if run is None:
        return
    started, school_id, outcome = run
    try:
        # The schema is switched back by now: the school comes from prerun.
        with context.for_school(school_id):
            metrics.TASK_DURATION.record((time.monotonic() - started) * 1000, task=task.name, outcome=outcome)
            if outcome == "failed":
                metrics.TASK_FAILURES.add(1, task=task.name)
    except Exception:
        logger.exception("Recording a task's end failed")
