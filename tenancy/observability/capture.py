"""Recording an occurrence (spec 0004). The only writer of ``ErrorGroup`` and
``ErrorEvent``, apart from status changes made in the console.

The school is always ``connection.tenant``: never a parameter. Writes go
through the ``observability`` connection, so an occurrence is kept even when
the failing request's transaction rolls back (AC-9). Nothing here raises
into its caller.
"""
import logging
import re
import threading
import traceback
from dataclasses import dataclass, field

from django.conf import settings
from django.db import connection, connections
from django.utils import timezone

from tenancy.observability import counters, fingerprint, scrub

logger = logging.getLogger("ordo.observability")

# The only context keys ever stored (AC-8). The payload is built from this
# list, never by filtering a larger dict.
CONTEXT_FIELDS = (
    "method",
    "route",
    "status",
    "user_id",
    "role",
    "support",
    "request_id",
    "job_id",
    "task_name",
    "page_path",
    "user_agent",
)
CONTEXT_STR_MAX = 255

# Job failures a user causes and can fix (bad or missing data). Recorded as
# ``kind=expected``: never counted toward status, never alerted.
EXPECTED_JOB_FAILURES = ("ems.readiness.NotReady",)

# Store a full event for the first 10 occurrences of a group in each clock
# hour, then every 100th (AC-10).
SAMPLE_FIRST = 10
SAMPLE_EVERY = 100

OBSERVED = "__ordo_observed__"

_UPSERT = """
INSERT INTO tenancy_errorgroup (
    school_id, fingerprint, source, kind, exc_type, title, culprit, status,
    count, first_seen, last_seen, last_opened_at, last_release
) VALUES (%s, %s, %s, %s, %s, %s, %s, 'open', 1, %s, %s, %s, %s)
ON CONFLICT ON CONSTRAINT error_group_per_school DO UPDATE SET
    count = tenancy_errorgroup.count + 1,
    last_seen = EXCLUDED.last_seen,
    last_release = EXCLUDED.last_release,
    status = CASE WHEN tenancy_errorgroup.status = 'resolved'
        THEN 'open' ELSE tenancy_errorgroup.status END,
    last_opened_at = CASE WHEN tenancy_errorgroup.status = 'resolved'
        THEN EXCLUDED.last_seen ELSE tenancy_errorgroup.last_opened_at END,
    resolved_at = CASE WHEN tenancy_errorgroup.status = 'resolved'
        THEN NULL ELSE tenancy_errorgroup.resolved_at END,
    resolved_by_id = CASE WHEN tenancy_errorgroup.status = 'resolved'
        THEN NULL ELSE tenancy_errorgroup.resolved_by_id END
RETURNING id, (xmax = 0) AS created, status, count
"""

_busy = threading.local()


@dataclass
class Occurrence:
    source: str
    exc_type: str
    message: str
    fingerprint: str
    culprit: str
    kind: str = "error"
    stack: list = field(default_factory=list)
    context: dict = field(default_factory=dict)
    title: str = ""
    release: str = ""


def enabled():
    return settings.OBSERVABILITY_ENABLED


def is_observed(exc):
    return bool(exc is not None and getattr(exc, OBSERVED, False))


def mark_observed(exc):
    try:
        setattr(exc, OBSERVED, True)
    except Exception:
        pass  # A few builtin exception types refuse new attributes.


def current_school_id():
    """The school of the schema this thread is in; None for the public schema."""
    from django_tenants.utils import get_public_schema_name

    schema = getattr(connection, "schema_name", None)
    if not schema or schema == get_public_schema_name():
        return None
    tenant = getattr(connection, "tenant", None)
    pk = getattr(tenant, "pk", None)
    if pk is not None:
        return pk
    # ``schema_context`` leaves a stand in tenant with no fields on it.
    from tenancy.models import School

    return (
        School.objects.using(_alias())
        .filter(schema_name=schema)
        .values_list("pk", flat=True)
        .first()
    )


def _alias():
    return settings.OBSERVABILITY_DB_ALIAS


def current_trace_id():
    """The trace of the request or job this runs in (spec 0006), or None."""
    from opentelemetry import trace

    span_context = trace.get_current_span().get_span_context()
    return trace.format_trace_id(span_context.trace_id) if span_context.is_valid else None


def build_context(**values):
    """The stored context: allowlisted keys only, strings cut short."""
    context = {}
    for key in CONTEXT_FIELDS:
        value = values.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, str):
            value = value[:CONTEXT_STR_MAX]
        elif not isinstance(value, (bool, int)):
            value = str(value)[:CONTEXT_STR_MAX]
        context[key] = value
    return context


def from_exception(exc, source, *, culprit="", kind="error", fingerprint_value=None, **context):
    """An ``Occurrence`` for an exception with its traceback."""
    exc_type = fingerprint.class_path(exc)
    stack = scrub.frames(traceback.extract_tb(exc.__traceback__)) if exc.__traceback__ else []
    if not culprit:
        top = (fingerprint.project_frames(stack) or stack)[-1:] or None
        culprit = f"{top[0]['file']} in {top[0]['function']}" if top else exc_type
    message = scrub.scrub(str(exc))
    short = type(exc).__name__
    return Occurrence(
        source=source,
        kind=kind,
        exc_type=exc_type,
        message=message,
        title=scrub.title(f"{short}: {message}" if message else short),
        stack=stack,
        culprit=culprit[: scrub.TITLE_MAX],
        fingerprint=fingerprint_value or fingerprint.for_exception(exc_type, stack),
        context=build_context(**context),
    )


def capture_exception(exc, source, *, culprit="", **context):
    """Record ``exc`` once. Skipped if another capture point already did."""
    if not enabled() or exc is None or is_observed(exc):
        return
    mark_observed(exc)
    _safely(lambda: record(from_exception(exc, source, culprit=culprit, **context)))


def capture_message(logger_name, msg_format, message, **context):
    """Record a log line that carries no exception."""
    if not enabled():
        return

    def build():
        text = scrub.scrub(message)
        return Occurrence(
            source="log",
            exc_type="LogMessage",
            message=text,
            title=scrub.title(text) or "LogMessage",
            culprit=logger_name[: scrub.TITLE_MAX],
            fingerprint=fingerprint.for_log(logger_name, msg_format),
            context=build_context(**context),
        )

    _safely(lambda: record(build()))


def job_failed(job, exc=None, expected=False):
    """Record a ``BackgroundJob`` ending in ``failed`` (AC-3).

    Call it before ``job.save()``: it marks the instance so the ``post_save``
    safety net knows it was seen. With no exception, the job's
    ``error_message`` is the message and there is no stack.
    """
    try:
        job._ordo_observed = True
    except Exception:
        pass
    if not enabled():
        return
    # No skip for an exception already seen: AC-3 counts jobs, and a bulk
    # job re-raises its failed slot's exception as its own failure.

    def build():
        exc_type = fingerprint.class_path(exc) if exc is not None else "JobFailed"
        is_expected = expected or exc_type in EXPECTED_JOB_FAILURES
        culprit = f"job:{job.job_type}"
        context = {"job_id": str(job.job_id)}
        if exc is not None and not is_expected:
            return from_exception(exc, "job", culprit=culprit, **context)
        message = scrub.scrub(str(exc) if exc is not None else job.error_message)
        short = exc_type.rsplit(".", 1)[-1]
        return Occurrence(
            source="job",
            kind="expected" if is_expected else "error",
            exc_type=exc_type,
            message=message,
            title=scrub.title(f"{short}: {message}" if message else short),
            culprit=culprit,
            fingerprint=fingerprint.for_job(job.job_type, exc_type),
            context=build_context(**context),
        )

    if exc is not None:
        mark_observed(exc)
    _safely(lambda: record(build()))


def _safely(fn):
    # A capture that fails while capturing (a log line from inside) stops here.
    if getattr(_busy, "active", False):
        return
    _busy.active = True
    try:
        fn()
    except Exception:
        logger.exception("Capturing an error failed")
    finally:
        _busy.active = False


def record(occ):
    """Upsert the group, store a sampled event, and count it."""
    school_id = current_school_id()
    now = timezone.now()
    # The browser says which build it runs; everything else is this server's.
    release = (occ.release if occ.source == "js" else settings.ORDO_RELEASE or "")[:64]
    with connections[_alias()].cursor() as cursor:
        cursor.execute(
            _UPSERT,
            [
                school_id,
                occ.fingerprint,
                occ.source,
                occ.kind,
                occ.exc_type[:255],
                occ.title[:255] or occ.exc_type[:255],
                occ.culprit[:255],
                now,
                now,
                now,
                release,
            ],
        )
        group_id, _created, status, count = cursor.fetchone()

    if _should_store(group_id, count):
        from tenancy.models import ErrorEvent

        ErrorEvent.objects.using(_alias()).create(
            group_id=group_id,
            school_id=school_id,
            occurred_at=now,
            message=occ.message[: scrub.MESSAGE_MAX],
            stack=occ.stack,
            context=occ.context,
            release=release,
            trace_id=current_trace_id(),
        )

    tallies = {}
    # An ignored group keeps counting, but never moves the school's status.
    counts_toward_status = occ.kind == "error" and status != "ignored"
    if counts_toward_status and occ.source != "js":
        tallies["server_errors"] = 1
    if counts_toward_status and occ.source == "js":
        tallies["js_errors"] = 1
    if occ.source == "job":
        tallies["expected_job_failures" if occ.kind == "expected" else "job_failures"] = 1
    counters.incr(school_id, **tallies)
    return group_id


def _should_store(group_id, count):
    n = counters.sample(group_id)
    if n is None:  # Redis is down: the group's own count decides.
        n = count
    return n <= SAMPLE_FIRST or n % SAMPLE_EVERY == 0


# "    at fn (https://host/assets/app-3f9a.js:1:2345)" (Chrome, Edge) and
# "fn@https://host/assets/app-3f9a.js:1:2345" (Firefox, Safari).
_JS_FRAME = re.compile(
    r"^\s*(?:at\s+(?:(?P<fn>[^\s(]+)\s+\()?(?P<url>[^\s()]+?):(?P<line>\d+)(?::\d+)?\)?"
    r"|(?P<fn2>[^@\s]*)@(?P<url2>[^\s]+?):(?P<line2>\d+)(?::\d+)?)\s*$"
)


def js_frames(stack):
    """Parsed browser stack frames, innermost last, file names only."""
    rows = []
    for line in (stack or "").splitlines():
        match = _JS_FRAME.match(line)
        if not match:
            continue
        url = match.group("url") or match.group("url2") or ""
        rows.append(
            {
                "file": url.split("?")[0].split("#")[0].rsplit("/", 1)[-1][: scrub.TITLE_MAX],
                "line": int(match.group("line") or match.group("line2") or 0),
                "function": (match.group("fn") or match.group("fn2") or "<anonymous>")[: scrub.TITLE_MAX],
                "code": "",
            }
        )
    # Browsers list the innermost frame first.
    return list(reversed(rows))[-scrub.STACK_MAX :]


def capture_js(name, message, stack, page_path, release, **context):
    """Record a browser error reported to the ingest endpoint (AC-5)."""
    if not enabled():
        return

    def build():
        frames = js_frames(stack)
        first = frames[-1] if frames else {"file": "", "function": ""}
        text = scrub.scrub(message)
        exc_type = (name or "Error")[:255]
        return Occurrence(
            source="js",
            exc_type=exc_type,
            message=text,
            title=scrub.title(f"{exc_type}: {text}" if text else exc_type),
            culprit=page_path[: scrub.TITLE_MAX] or "<unknown page>",
            stack=frames,
            fingerprint=fingerprint.for_js(exc_type, message, first["file"], first["function"]),
            context=build_context(page_path=page_path, **context),
            release=(release or "")[:64],
        )

    _safely(lambda: record(build()))
