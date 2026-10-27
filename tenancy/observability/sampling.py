"""Segments and the keep rule (spec 0006, Segment collection and the keep rule).

A segment is the spans of one request, one job or one WebSocket message in
one process. Its spans are held here until its local root (the span with no
parent in this process) ends; then the whole segment is kept or discarded.
Errors and slow segments are always kept; the rest by an HMAC of the trace
id, so every process makes the same choice for one trace and a client's
sampled flag counts for nothing.
"""
import hashlib
import hmac
import threading
import time
from dataclasses import dataclass, field

from django.conf import settings
from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.trace import SpanKind, StatusCode

from tenancy.observability import context

# Spans kept per segment; the rest are counted out with ``truncated``.
MAX_SPANS = 2000
# How long a segment that never ends is held: jobs get the hard time limit.
HOLD_SECONDS = 15 * 60
SWEEP_EVERY_SECONDS = 30

# Root span kinds that start a segment. Any other local root (a query at
# startup, say) is not stored.
SEGMENT_KINDS = {SpanKind.SERVER: "http", SpanKind.CONSUMER: "job"}
# Set at span start to make a root start a segment of that kind ("ws").
SEGMENT_KIND_ATTRIBUTE = "ordo.segment_kind"
# Set by a root's own hook when its outcome is not in its status (jobs).
ERROR_ATTRIBUTE = "ordo.error"
# The Celery instrumentor's name for an observability task's root: these
# never start a segment, so the flush and its friends are never stored.
IGNORED_ROOT_PREFIX = "run/tenancy.observability.tasks."


def sampled(trace_id_hex):
    """True when this trace falls in the ``OBS_TRACE_SAMPLE_PERCENT`` bucket."""
    digest = hmac.new(settings.SECRET_KEY.encode(), trace_id_hex.encode(), hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big") % 100 < settings.OBS_TRACE_SAMPLE_PERCENT


def segment_kind(span):
    """``http``, ``job`` or ``ws`` for a span that starts a segment, else None."""
    kind = (span.attributes or {}).get(SEGMENT_KIND_ATTRIBUTE)
    return kind or SEGMENT_KINDS.get(span.kind)


def duration_ms(span):
    return (span.end_time - span.start_time) / 1e6


def is_error(root, kind):
    attributes = root.attributes or {}
    if ERROR_ATTRIBUTE in attributes:
        return bool(attributes[ERROR_ATTRIBUTE])
    if kind == "http":
        status = attributes.get("ordo.status_code") or attributes.get("http.status_code") or 0
        if int(status) >= 500:
            return True
    return root.status.status_code == StatusCode.ERROR


def slow_ms(root, kind):
    if kind == "job":
        return settings.OBS_SLOW_TASK_OVERRIDES.get(root.name, settings.OBS_SLOW_JOB_MS)
    return settings.OBS_SLOW_HTTP_MS


def hold_seconds(kind):
    if kind == "job":
        return settings.CELERY_TASK_TIME_LIMIT + 60
    return HOLD_SECONDS


@dataclass
class Decision:
    error: bool
    slow: bool
    sampled: bool

    @property
    def keep(self):
        return self.error or self.slow or self.sampled


def decide(root, kind):
    from opentelemetry.trace import format_trace_id

    return Decision(
        error=is_error(root, kind),
        slow=duration_ms(root) > slow_ms(root, kind),
        sampled=sampled(format_trace_id(root.context.trace_id)),
    )


@dataclass
class _Held:
    kind: str
    started: float
    spans: list = field(default_factory=list)
    open_ids: set = field(default_factory=set)
    truncated: bool = False


class SegmentProcessor(SpanProcessor):
    """Holds each segment's spans until its root ends, then hands a kept
    segment to ``exporter.export(root, spans, kind, decision, truncated)``.
    Runs on the request thread, so it only sorts spans; the exporter does
    the rest on its own thread."""

    def __init__(self, exporter, clock=time.monotonic):
        self._exporter = exporter
        self._clock = clock
        self._lock = threading.Lock()
        # Open span id -> its segment's root span id.
        self._root_of = {}
        # Root span id -> the held segment.
        self._segments = {}
        self._swept_at = clock()

    def on_start(self, span, parent_context=None):
        span_id = span.context.span_id
        parent = span.parent
        if parent is None or parent.is_remote:
            kind = segment_kind(span)
            if kind is None or span.name.startswith(IGNORED_ROOT_PREFIX):
                return
            with self._lock:
                self._root_of[span_id] = span_id
                self._segments[span_id] = _Held(kind=kind, started=self._clock(), open_ids={span_id})
            return
        with self._lock:
            root = self._root_of.get(parent.span_id)
            if root is None:
                return
            self._root_of[span_id] = root
            self._segments[root].open_ids.add(span_id)
        # A root learns its school when it ends (the school isn't resolved
        # yet when an HTTP root starts); children know it now.
        school = context.school_id()
        if school is not None:
            span.set_attribute("ordo.school_id", school)

    def on_end(self, span):
        span_id = span.context.span_id
        with self._lock:
            root = self._root_of.pop(span_id, None)
            held = self._segments.get(root) if root is not None else None
            if held is None:
                return
            held.open_ids.discard(span_id)
            # The root is always stored, so a truncated segment still has one.
            if len(held.spans) < MAX_SPANS - 1 or span_id == root:
                held.spans.append(span)
            else:
                held.truncated = True
            if span_id != root:
                return
            del self._segments[root]
            for open_id in held.open_ids:
                self._root_of.pop(open_id, None)
        try:
            decision = decide(span, held.kind)
            if decision.keep:
                self._exporter.export(span, held.spans, held.kind, decision, held.truncated)
        finally:
            self._sweep()

    def _sweep(self):
        """Forget segments whose root never ended (a killed job)."""
        now = self._clock()
        if now - self._swept_at < SWEEP_EVERY_SECONDS:
            return
        self._swept_at = now
        with self._lock:
            expired = [
                root
                for root, held in self._segments.items()
                if now - held.started > hold_seconds(held.kind)
            ]
            for root in expired:
                held = self._segments.pop(root)
                for open_id in held.open_ids:
                    self._root_of.pop(open_id, None)

    def held_count(self):
        with self._lock:
            return len(self._segments)

    def shutdown(self):
        self._exporter.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self._exporter.flush()
