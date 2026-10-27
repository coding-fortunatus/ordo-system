"""Which school, request and user a span, log or metric belongs to (spec 0006,
School attribution).

One helper, ``school_id()``, is used by every signal. A request sets its
school once ``SchoolMiddleware`` has resolved it; a WebSocket handler sets
the socket's; a Celery task has neither and is mapped from its schema.
"""
import contextvars
import threading
import time
from contextlib import contextmanager

from django.utils.functional import empty

request_school_id = contextvars.ContextVar("ordo_request_school_id", default=None)
ws_school_id = contextvars.ContextVar("ordo_ws_school_id", default=None)
request_id = contextvars.ContextVar("ordo_request_id", default="")
# Set for a WebSocket handler or a job; a request's user is read from it.
user_id = contextvars.ContextVar("ordo_user_id", default=None)
# The request being served, so a log line can name its user once the view
# has signed them in.
request = contextvars.ContextVar("ordo_request", default=None)
# Set by code that knows the school better than the connection's schema: a
# job's metrics recorded after its schema is switched back, or a platform
# task sampling each school in turn.
school_override = contextvars.ContextVar("ordo_school_override", default=None)

# The per process {schema_name: school_id} map for Celery tasks.
SCHEMA_REFRESH_SECONDS = 5 * 60
SCHEMA_MISS_SECONDS = 30

_schemas = {}
_loaded_at = 0.0
_missed_at = 0.0
_lock = threading.Lock()


def school_id():
    """The current school's id, or None for the platform."""
    value = request_school_id.get()
    if value is not None:
        return value
    value = ws_school_id.get()
    if value is not None:
        return value
    value = school_override.get()
    if value is not None:
        return value
    return _task_school_id()


@contextmanager
def for_school(school_id):
    """Attribute what's recorded inside the block to ``school_id``."""
    token = school_override.set(school_id)
    try:
        yield
    finally:
        school_override.reset(token)


def signed_in_user(req):
    """``req``'s user if one was already loaded, else None. A lazy user
    nobody looked at stays unloaded, so this adds no query."""
    user = getattr(req, "user", None)
    if user is None or getattr(user, "_wrapped", None) is empty:
        return None
    return user if getattr(user, "is_authenticated", False) else None


def user_ids(req=None):
    """``(user_id, platform_admin_id)`` for the current work: on a school
    host the school's user, on the console host the admin (as the umbrella
    value sourcing table says), else the user a socket or job set."""
    req = req if req is not None else request.get()
    if req is None:
        return user_id.get(), None
    user = signed_in_user(req)
    if user is None:
        return None, None
    from tenancy.models import PlatformAdmin

    if isinstance(user, PlatformAdmin):
        return None, user.pk
    return (user.pk, None) if request_school_id.get() is not None else (None, None)


def _task_school_id():
    global _missed_at
    from django.db import connection
    from django_tenants.utils import get_public_schema_name

    schema = getattr(connection, "schema_name", None)
    if not schema or schema == get_public_schema_name():
        return None
    now = time.monotonic()
    if now - _loaded_at > SCHEMA_REFRESH_SECONDS:
        _load_schemas(now)
    elif schema not in _schemas and now - _missed_at > SCHEMA_MISS_SECONDS:
        _missed_at = now
        _load_schemas(now)
    return _schemas.get(schema)


def _load_schemas(now):
    """Reload the map, never traced, and never raising into the caller."""
    global _schemas, _loaded_at
    with _lock:
        try:
            from opentelemetry.instrumentation.utils import suppress_instrumentation

            from tenancy.models import School

            with suppress_instrumentation():
                _schemas = dict(School.objects.values_list("schema_name", "pk"))
        except Exception:
            pass
        _loaded_at = now


def reset_schema_cache():
    """Forget the map (tests)."""
    global _schemas, _loaded_at, _missed_at
    _schemas, _loaded_at, _missed_at = {}, 0.0, 0.0
