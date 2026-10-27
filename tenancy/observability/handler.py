"""Log handlers.

``ObservabilityLogHandler``: ERROR records on Ordo's own loggers (spec 0004,
AC-4). Attached to ``ems``, ``tenancy`` and ``core`` only, never
``django.request`` (the request receiver covers those) or
``ordo.observability``.

``TelemetryLogHandler``: log lines with their school, trace and request
(spec 0006, logs), next to the one above. A record is captured on the
caller's thread into an in process queue and a background thread drains it
into the telemetry Redis every second, so logging never waits on Redis.
"""
import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone

from django.conf import settings
from opentelemetry import trace
from opentelemetry.instrumentation.utils import is_instrumentation_enabled
from opentelemetry.trace import format_span_id, format_trace_id

MESSAGE_MAX = 4000
ATTRIBUTES_MAX_BYTES = 2048
QUEUE_MAX = 10000
DRAIN_SECONDS = 1.0
OBSERVABILITY_TASKS = "tenancy.observability.tasks."

# Everything a ``LogRecord`` has of its own; any other key came in ``extra``.
_STANDARD = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


def _is_observability(name):
    return name == "ordo.observability" or name.startswith("ordo.observability.")


class ObservabilityLogHandler(logging.Handler):
    def emit(self, record):
        if _is_observability(record.name):
            return
        try:
            # Imported late: logging is configured before Django apps load.
            from tenancy.observability import capture

            exc = record.exc_info[1] if record.exc_info else None
            if exc is not None:
                capture.capture_exception(exc, "log", culprit=record.name)
            else:
                capture.capture_message(record.name, str(record.msg), record.getMessage())
        except Exception:
            logging.getLogger("ordo.observability").exception("Capturing a log record failed")


def wanted(record):
    """False for telemetry's own records: its logger, anything logged inside
    an observability task (instrumentation is suppressed there), and
    Celery's lines about those tasks."""
    if not (settings.OBSERVABILITY_ENABLED and settings.OBSERVABILITY_LOGS_ENABLED):
        return False
    if _is_observability(record.name) or not is_instrumentation_enabled():
        return False
    data = getattr(record, "data", None)
    if record.name.startswith("celery") and isinstance(data, dict):
        return not str(data.get("name", "")).startswith(OBSERVABILITY_TASKS)
    return True


def _extra(record):
    """The JSON safe values passed in ``extra``, at most 2 KB in all."""
    kept, size = {}, 2
    for key, value in vars(record).items():
        if key in _STANDARD or key.startswith("_"):
            continue
        if value is not None and not isinstance(value, (str, int, float, bool)):
            continue
        size += len(json.dumps({key: value}, default=str))
        if size > ATTRIBUTES_MAX_BYTES:
            break
        kept[key] = value
    return kept


def record_item(record):
    """A buffered log item. The message is scrubbed later, on the drain
    thread."""
    from tenancy.observability import context

    span_context = trace.get_current_span().get_span_context()
    traced = span_context.is_valid
    user_id, platform_admin_id = context.user_ids()
    message = record.getMessage()
    if record.exc_info and record.exc_info[1] is not None:
        # The type and message only; the stack is in spec 0004's event.
        exc = record.exc_info[1]
        message = f"{message}\n{type(exc).__name__}: {exc}"
    attributes = _extra(record)
    if platform_admin_id is not None:
        attributes["platform_admin_id"] = platform_admin_id
    return {
        "logged_at": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
        "school_id": context.school_id(),
        "level": record.levelno,
        "logger": record.name[:100],
        "message": message,
        "trace_id": format_trace_id(span_context.trace_id) if traced else None,
        "span_id": format_span_id(span_context.span_id) if traced else None,
        "request_id": (context.request_id.get() or "")[:32],
        "user_id": user_id,
        "attributes": attributes,
    }


_queue = queue.SimpleQueue()
_thread = None
_pid = None
_lock = threading.Lock()


def _ensure_thread():
    # A forked child starts its own queue and thread; nothing crosses a fork.
    global _queue, _thread, _pid
    if _thread is not None and _pid == os.getpid():
        return
    with _lock:
        if _thread is None or _pid != os.getpid():
            _pid = os.getpid()
            _queue = queue.SimpleQueue()
            _thread = threading.Thread(target=_run, name="ordo-telemetry-logs", daemon=True)
            _thread.start()


def _run():
    while True:
        time.sleep(DRAIN_SECONDS)
        try:
            drain()
        except Exception:
            logging.getLogger("ordo.observability").exception("Pushing log lines failed")


def drain():
    """Scrub what's queued and push it to the telemetry Redis, now."""
    from tenancy.observability import buffer, scrub

    items = []
    while True:
        try:
            items.append(_queue.get_nowait())
        except queue.Empty:
            break
    for item in items:
        item["message"] = scrub.scrub(item["message"], MESSAGE_MAX)
        item["attributes"] = {
            key: scrub.scrub(value) if isinstance(value, str) else value
            for key, value in item["attributes"].items()
        }
    buffer.push("log", items)


class TelemetryLogHandler(logging.Handler):
    def emit(self, record):
        try:
            if not wanted(record):
                return
            item = record_item(record)
            _ensure_thread()
            if _queue.qsize() >= QUEUE_MAX:
                from tenancy.observability import buffer

                buffer.count_drop("log", "buffer_full")
                return
            _queue.put(item)
        except Exception:
            # Never into the caller, and never through logging again.
            pass
