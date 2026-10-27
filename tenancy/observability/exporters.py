"""Kept segments to the telemetry Redis (spec 0006, Segment row).

``SegmentExporter`` turns a kept segment into one ``segment`` item and its
``span`` items and pushes them to the buffers. It works on its own thread,
never the request's: ``export`` only queues the spans. Span attributes are
an allowlist (child 0003), so an instrumentor that starts recording a new
attribute after an upgrade stores nothing new.
"""
import logging
import os
import queue
import threading
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from opentelemetry.trace import SpanKind, StatusCode, format_span_id, format_trace_id

from tenancy.observability import buffer, scrub

logger = logging.getLogger("ordo.observability")

# Segments waiting for the export thread; past this they are dropped.
QUEUE_MAX = 2000
BATCH_SEGMENTS = 200
STATEMENT_MAX = 2048

ALLOWED_ATTRIBUTES = frozenset(
    {
        "http.route",
        "http.method",
        "http.status_code",
        "db.operation",
        "db.statement",
        "db.rows",
        "redis.command",
        "http.url",
        "celery.task_name",
        "celery.retries",
        "exception.type",
        "exception.message",
        "ordo.consumer",
        "ordo.message_type",
        "ordo.ws_accepted",
        "ordo.ws_close_code",
        "ordo.platform_admin_id",
    }
)
# Newer semantic convention names, stored under the allowlisted ones.
RENAMED = {"db.query.text": "db.statement", "url.full": "http.url"}


def _iso(ns):
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat()


def _span_id(span_context):
    return format_span_id(span_context.span_id) if span_context else None


def span_kind(span):
    """The stored kind: server, db, redis, job, ws, http_out or internal."""
    attributes = span.attributes or {}
    if attributes.get("ordo.segment_kind") == "ws":
        return "ws"
    system = attributes.get("db.system")
    if system:
        return "redis" if system == "redis" else "db"
    if span.kind == SpanKind.SERVER:
        return "server"
    if span.kind == SpanKind.CONSUMER:
        return "job"
    if span.kind == SpanKind.CLIENT and ("http.url" in attributes or "url.full" in attributes):
        return "http_out"
    return "internal"


def _first_word(text):
    words = str(text).split(None, 1)
    return words[0].upper()[:32] if words else ""


def _without_query(url):
    """Scheme, host and path only: a query string can carry anything."""
    parts = urlsplit(str(url))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def span_attributes(span, kind):
    """The allowlisted attributes, scrubbed, plus the span's exception."""
    kept = {}
    for key, value in (span.attributes or {}).items():
        key = RENAMED.get(key, key)
        if key not in ALLOWED_ATTRIBUTES:
            continue
        if key == "http.url":
            # Only an outgoing call's address is kept; an incoming request
            # has its route instead.
            if kind != "http_out":
                continue
            value = _without_query(value)
        elif key == "db.statement":
            # Redis keeps its command alone (below); SQL keeps the statement
            # as the driver got it, with ``%s`` where the values go.
            if kind != "db":
                continue
            value = str(value)[:STATEMENT_MAX]
        elif key == "exception.message":
            value = scrub.scrub(str(value))
        elif isinstance(value, (tuple, list)):
            value = list(value)
        kept[key] = value
    if kind == "db" and "db.statement" in kept:
        kept.setdefault("db.operation", _first_word(kept["db.statement"]))
    elif kind == "redis":
        kept["redis.command"] = span.name[:100]
    for event in span.events or ():
        if event.name == "exception":
            kept.setdefault("exception.type", str(event.attributes.get("exception.type", ""))[:255])
            kept.setdefault("exception.message", scrub.scrub(str(event.attributes.get("exception.message", ""))))
            break
    return kept


def segment_items(root, spans, kind, decision, truncated):
    """One ``segment`` item and its ``span`` items, ready to buffer."""
    attributes = root.attributes or {}
    trace_id = format_trace_id(root.context.trace_id)
    root_span_id = format_span_id(root.context.span_id)
    school_id = attributes.get("ordo.school_id")
    status_code = attributes.get("ordo.status_code")
    segment = {
        "root_span_id": root_span_id,
        "trace_id": trace_id,
        "started_at": _iso(root.start_time),
        "school_id": school_id,
        "kind": kind,
        "root_name": root.name[:255],
        "route": str(attributes.get("ordo.route", ""))[:255],
        "method": str(attributes.get("ordo.method", ""))[:8],
        "status_code": int(status_code) if status_code is not None else None,
        "duration_ms": round((root.end_time - root.start_time) / 1e6),
        "is_error": decision.error,
        "is_slow": decision.slow,
        "span_count": len(spans),
        "truncated": truncated,
        "user_id": attributes.get("ordo.user_id"),
        "request_id": str(attributes.get("ordo.request_id", ""))[:32],
        "release": settings.ORDO_RELEASE[:64],
        "process_role": settings.OBS_PROCESS_ROLE[:24],
    }
    items = []
    for span in spans:
        span_school = (span.attributes or {}).get("ordo.school_id", school_id)
        kind_of_span = span_kind(span)
        items.append(
            {
                "span_id": format_span_id(span.context.span_id),
                "trace_id": trace_id,
                "parent_span_id": _span_id(span.parent),
                "root_span_id": root_span_id,
                "started_at": _iso(span.start_time),
                "school_id": span_school,
                "name": span.name[:255],
                "kind": kind_of_span,
                "duration_ms": round((span.end_time - span.start_time) / 1e6, 2),
                "status": "error" if span.status.status_code == StatusCode.ERROR else "ok",
                "attributes": span_attributes(span, kind_of_span),
            }
        )
    return segment, items


class SegmentExporter:
    """Queues kept segments and pushes them from a background thread.

    ``sync=True`` pushes on the caller's thread instead (tests)."""

    def __init__(self, sync=False):
        self._sync = sync
        self._queue = queue.Queue(maxsize=QUEUE_MAX)
        self._thread = None
        self._pid = None
        self._lock = threading.Lock()
        self._stopped = False

    def export(self, root, spans, kind, decision, truncated):
        job = (root, spans, kind, decision, truncated)
        if self._sync:
            self._push([job])
            return
        self._ensure_thread()
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            buffer.count_drop("segment", "buffer_full")
            buffer.count_drop("span", "buffer_full", len(spans))

    def _ensure_thread(self):
        # A forked child gets a new thread; the parent's never crosses a fork.
        if self._thread is not None and self._pid == os.getpid():
            return
        with self._lock:
            if self._thread is None or self._pid != os.getpid():
                self._pid = os.getpid()
                self._queue = queue.Queue(maxsize=QUEUE_MAX)
                self._thread = threading.Thread(target=self._run, name="ordo-telemetry-export", daemon=True)
                self._thread.start()

    def _run(self):
        while not self._stopped:
            try:
                jobs = [self._queue.get(timeout=1)]
            except queue.Empty:
                continue
            while len(jobs) < BATCH_SEGMENTS:
                try:
                    jobs.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            self._push(jobs)

    def _push(self, jobs):
        segments, spans = [], []
        for job in jobs:
            try:
                segment, items = segment_items(*job)
            except Exception:
                logger.exception("Building a trace segment failed")
                continue
            segments.append(segment)
            spans.extend(items)
        buffer.push("segment", segments)
        buffer.push("span", spans)

    def flush(self):
        """Push everything queued now, on the caller's thread."""
        jobs = []
        while True:
            try:
                jobs.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if jobs:
            self._push(jobs)
        return True

    def shutdown(self):
        self.flush()
        self._stopped = True
