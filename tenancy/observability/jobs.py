"""Jobs in a trace (spec 0006, value sourcing for job segments and logs).

Publishing a task adds the request id and the time it was sent to its
headers; the Celery instrumentor adds the trace context to the same headers.
Running a task sets the request and user context its logs read. When the
worker is traced, the task's root span is also named after the task and
given its school, which tenant-schemas-celery has switched to by then (and
switches back before the span ends). A final failure is an error unless it
is an expected one; a retry never is.
"""
import inspect
import logging
import time

from celery.signals import (
    before_task_publish,
    task_failure,
    task_postrun,
    task_prerun,
    task_retry,
)
from celery.utils.serialization import UnpickleableExceptionWrapper
from django.conf import settings
from opentelemetry import trace

from tenancy.observability import context, fingerprint
from tenancy.observability.capture import EXPECTED_JOB_FAILURES
from tenancy.observability.sampling import ERROR_ATTRIBUTE

logger = logging.getLogger("ordo.observability")

REQUEST_ID_HEADER = "ordo_request_id"
SENT_AT_HEADER = "ordo_sent_at"
# Never traced and never given a request context of their own.
OBSERVABILITY_TASKS = "tenancy.observability.tasks."

# Task id -> the context tokens its postrun resets. An eager ``.apply()``
# inside a task nests, so each run keeps its own.
_tokens = {}
_signatures = {}


def header(task, name):
    """A custom header of the running task. Protocol 2 copies headers onto
    the request; ``.apply()`` keeps them under ``headers``."""
    request = task.request
    headers = getattr(request, "headers", None)
    if isinstance(headers, dict) and headers.get(name) is not None:
        return headers[name]
    return getattr(request, name, None)


def task_user_id(task, args, kwargs):
    """The task's ``user_id`` argument when it has one (the generation tasks)."""
    try:
        signature = _signatures.get(task.name)
        if signature is None:
            signature = _signatures[task.name] = inspect.signature(task.run)
        value = signature.bind_partial(*(args or ()), **(kwargs or {})).arguments.get("user_id")
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def is_observability_task(task):
    return getattr(task, "name", "").startswith(OBSERVABILITY_TASKS)


@before_task_publish.connect(dispatch_uid="ordo_observability_job_published")
def job_published(headers=None, **kwargs):
    if headers is None or not settings.OBSERVABILITY_ENABLED:
        return
    try:
        request_id = context.request_id.get()
        if request_id:
            headers.setdefault(REQUEST_ID_HEADER, request_id)
        headers.setdefault(SENT_AT_HEADER, int(time.time() * 1000))
    except Exception:
        logger.exception("Adding telemetry headers to a task failed")


@task_prerun.connect(dispatch_uid="ordo_observability_job_started")
def job_started(task_id=None, task=None, args=None, kwargs=None, **_):
    if task is None or task_id is None or is_observability_task(task):
        return
    try:
        # An eager inner run has no headers of its own: it keeps the outer's.
        request_id = str(header(task, REQUEST_ID_HEADER) or context.request_id.get() or "")[:32]
        _tokens[task_id] = (
            context.request_id.set(request_id),
            context.user_id.set(task_user_id(task, args, kwargs)),
        )
    except Exception:
        logger.exception("Setting a task's telemetry context failed")


@task_postrun.connect(dispatch_uid="ordo_observability_job_finished")
def job_finished(task_id=None, **_):
    tokens = _tokens.pop(task_id, None)
    if tokens is None:
        return
    try:
        context.request_id.reset(tokens[0])
        context.user_id.reset(tokens[1])
    except ValueError:
        # Reset from another context: leave it, the next task sets its own.
        pass


def name_span(task_id=None, task=None, **_):
    """Connected after the Celery instrumentor's prerun, whose span is the
    current one here."""
    if task is None or is_observability_task(task):
        return
    span = trace.get_current_span()
    if not span.is_recording():
        return
    try:
        span.update_name(task.name)
        span.set_attribute("celery.task_name", task.name)
        span.set_attribute("celery.retries", int(task.request.retries or 0))
        span.set_attribute("ordo.request_id", context.request_id.get())
        school_id = context.school_id()
        if school_id is not None:
            span.set_attribute("ordo.school_id", school_id)
        user_id = context.user_id.get()
        if user_id is not None:
            span.set_attribute("ordo.user_id", user_id)
    except Exception:
        logger.exception("Recording a task's trace fields failed")


def exception_class_path(exception):
    # Celery wraps an exception it can't rebuild from its args (``NotReady``
    # takes a report), keeping the class's name.
    if isinstance(exception, UnpickleableExceptionWrapper):
        return f"{exception.exc_module}.{exception.exc_cls_name}"
    return fingerprint.class_path(exception)


def mark_failed(exception=None, **_):
    span = trace.get_current_span()
    if span.is_recording():
        expected = exception is not None and exception_class_path(exception) in EXPECTED_JOB_FAILURES
        span.set_attribute(ERROR_ATTRIBUTE, not expected)


def mark_retried(**_):
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute(ERROR_ATTRIBUTE, False)


_SPAN_HOOKS = (
    (task_prerun, name_span),
    (task_failure, mark_failed),
    (task_retry, mark_retried),
)


def connect_span_hooks():
    for signal, receiver in _SPAN_HOOKS:
        signal.connect(receiver, weak=False, dispatch_uid=f"ordo_observability_{receiver.__name__}")


def disconnect_span_hooks():
    for signal, receiver in _SPAN_HOOKS:
        signal.disconnect(receiver, dispatch_uid=f"ordo_observability_{receiver.__name__}")
