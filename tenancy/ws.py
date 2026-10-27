"""WebSocket side of tenancy: which school a socket belongs to.

Async consumer code runs on the event loop thread, where the connection is
on no school's schema. So the school is resolved once, at connect, into
``scope["school"]``, and every database call goes through :func:`in_school`
(spec 0003, child 0001).
"""
import json
import re
from contextlib import contextmanager

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.exceptions import StopConsumer
from channels.security.websocket import OriginValidator
from django.conf import settings
from django.http.request import split_domain_port
from django_tenants.utils import get_public_schema_name, schema_context
from opentelemetry.trace import SpanKind, Status, StatusCode

from tenancy.models import Domain, School
from tenancy.observability import capture, context, counters, otel
from tenancy.observability.sampling import SEGMENT_KIND_ATTRIBUTE

# Close code for an unknown or purged school, mirroring the HTTP 404.
CLOSE_SCHOOL_NOT_FOUND = 4404
# Close code for a school that isn't active, mirroring the API's refusal
# (403 school_suspended, 503 school_not_ready).
CLOSE_SCHOOL_CLOSED = 4403


def _host(scope):
    for name, value in scope.get("headers", []):
        if name == b"host":
            domain, _port = split_domain_port(value.decode("latin1").lower())
            return domain
    return ""


@database_sync_to_async
def _resolve_school(hostname):
    with schema_context(get_public_schema_name()):
        domain = (
            Domain.objects.select_related("tenant").filter(domain=hostname).first()
        )
    school = domain.tenant if domain else None
    if school is None or school.status == School.Status.PURGED:
        return None
    if school.schema_name == get_public_schema_name():
        return None
    return school


async def count_ws_error(school):
    """One more refused or error closed socket for ``school`` (AC-6)."""
    if capture.enabled():
        await sync_to_async(counters.incr)(getattr(school, "pk", None), ws_errors=1)


def _capture(exc, path):
    capture.capture_exception(exc, "ws", culprit=path)


async def capture_ws(scope, exc):
    """Record an exception escaping socket code, in the socket's school."""
    try:
        path = scope.get("path", "") or "<websocket>"
        if scope.get("school") is not None:
            await database_sync_to_async(in_school)(scope, _capture, exc, path)
        else:
            await database_sync_to_async(_capture)(exc, path)
    except Exception:
        capture.logger.exception("Capturing a WebSocket error failed")


class SchoolScopeMiddleware:
    """Set ``scope["school"]`` from the Host header, or refuse the socket."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        try:
            school = await _resolve_school(_host(scope))
        except Exception as exc:
            await capture_ws(scope, exc)
            raise
        refusal = None
        if school is None:
            refusal = CLOSE_SCHOOL_NOT_FOUND
        elif school.status != School.Status.ACTIVE:
            refusal = CLOSE_SCHOOL_CLOSED
        if refusal:
            await count_ws_error(school)
            # The handshake has to be read before it can be refused.
            await receive()
            await send({"type": "websocket.close", "code": refusal})
            return
        scope = dict(scope, school=school)
        return await self.app(scope, receive, send)


# A plain close; anything else a consumer closes with is a refusal or error.
CLOSE_NORMAL = 1000
# A message's ``type`` names its span; bigger bodies aren't parsed for it.
MESSAGE_TYPE_MAX = 50
PARSE_MAX_BYTES = 64 * 1024


def message_type(message):
    """The ``type`` key of a JSON text frame, cut short, or ""."""
    text = message.get("text")
    if not text or len(text) > PARSE_MAX_BYTES:
        return ""
    try:
        body = json.loads(text)
    except ValueError:
        return ""
    value = body.get("type") if isinstance(body, dict) else None
    return str(value)[:MESSAGE_TYPE_MAX] if value is not None else ""


class ObservedConsumerMixin:
    """Capture exceptions escaping a consumer's connect and receive, and
    count every refusal or error close (spec 0004, AC-6).

    Connect, each received message and disconnect are also each a segment of
    their own, kind ``ws``, in the socket's school (spec 0006, AC-16)."""

    _ordo_accepted = False

    @contextmanager
    def _traced(self, event, detail=""):
        consumer = type(self).__name__
        school = self.scope.get("school")
        user = self.scope.get("user")
        user_id = user.pk if user is not None and getattr(user, "is_authenticated", False) else None
        tokens = (
            context.ws_school_id.set(getattr(school, "pk", None)),
            context.user_id.set(user_id),
        )
        attributes = {SEGMENT_KIND_ATTRIBUTE: "ws", "ordo.route": consumer, "ordo.consumer": consumer}
        if school is not None:
            attributes["ordo.school_id"] = school.pk
        if user_id is not None:
            attributes["ordo.user_id"] = user_id
        name = f"ws.{event} {consumer}" + (f".{detail}" if detail else "")
        try:
            with otel.tracer("ordo.ws").start_as_current_span(
                name,
                kind=SpanKind.SERVER,
                attributes=attributes,
                record_exception=False,
                set_status_on_exception=False,
            ) as span:
                try:
                    yield span
                except StopConsumer:
                    raise
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                    raise
        finally:
            context.ws_school_id.reset(tokens[0])
            context.user_id.reset(tokens[1])

    async def websocket_connect(self, message):
        with self._traced("connect") as span:
            try:
                return await super().websocket_connect(message)
            except Exception as exc:
                await capture_ws(self.scope, exc)
                await count_ws_error(self.scope.get("school"))
                raise
            finally:
                span.set_attribute("ordo.ws_accepted", self._ordo_accepted)

    async def websocket_receive(self, message):
        kind = message_type(message)
        with self._traced("receive", kind) as span:
            if kind:
                span.set_attribute("ordo.message_type", kind)
            try:
                return await super().websocket_receive(message)
            except Exception as exc:
                await capture_ws(self.scope, exc)
                await count_ws_error(self.scope.get("school"))
                raise

    async def websocket_disconnect(self, message):
        with self._traced("disconnect") as span:
            code = message.get("code")
            if isinstance(code, int):
                span.set_attribute("ordo.ws_close_code", code)
            return await super().websocket_disconnect(message)

    async def accept(self, subprotocol=None, headers=None):
        self._ordo_accepted = True
        await super().accept(subprotocol=subprotocol, headers=headers)

    async def close(self, code=None, reason=None):
        if code is not None and code is not True and code != CLOSE_NORMAL:
            await count_ws_error(self.scope.get("school"))
        await super().close(code=code, reason=reason)


def in_school(scope, fn, *args, **kwargs):
    """Run ``fn`` in the socket's school schema, inside a sync function.

    Connections are per thread, so the switch happens in the same thread as
    the queries. Use it as ``await database_sync_to_async(in_school)(scope,
    fn)``.
    """
    with schema_context(scope["school"].schema_name):
        return fn(*args, **kwargs)


class SchoolOriginValidator(OriginValidator):
    """Accept any ``<slug>.<base>`` origin (``localhost`` ones in debug).

    Channels' own validator treats a leading dot as a wildcard only for bare
    hosts, not for full origins with a scheme and port.
    """

    def __init__(self, application):
        super().__init__(application, [])
        bases = [re.escape(settings.PLATFORM_BASE_DOMAIN)]
        if settings.DEBUG:
            bases.append("localhost")
        self.pattern = re.compile(
            rf"^https?://[a-z0-9-]+\.(?:{'|'.join(bases)})(?::\d+)?$"
        )

    def valid_origin(self, parsed_origin):
        if parsed_origin is None or not parsed_origin.hostname:
            return False
        origin = f"{parsed_origin.scheme}://{parsed_origin.netloc}".lower()
        return bool(self.pattern.match(origin))
