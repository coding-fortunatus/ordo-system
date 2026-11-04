"""Token authentication for WebSocket connections.

Browsers cannot set arbitrary headers on the WebSocket constructor, so the
token is passed as a query-string parameter (``?token=<key>``). This
middleware parses that parameter and attaches the corresponding user to
``scope`` before the consumer accepts the connection. The token is looked
up in the socket's own school (``scope["school"]``, set by
``tenancy.ws.SchoolScopeMiddleware``), so a token from another school
finds nobody. A support user past its ``support_expires_at`` is nobody
too, as in ``ems.api.auth.SchoolTokenAuthentication``.
"""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser

from tenancy.ws import in_school


def _user_for(token_key: str):
    # Imported lazily so AppRegistryNotReady is not a concern.
    from rest_framework.authtoken.models import Token

    from ems.api.auth import support_expired

    try:
        user = Token.objects.select_related("user").get(key=token_key).user
    except Token.DoesNotExist:
        return AnonymousUser()
    if not user.is_active or support_expired(user):
        return AnonymousUser()
    return user


def still_valid(scope, *, staff_or_owner_id=None) -> bool:
    """Whether the socket's token still resolves to the user it opened as
    (not logged out, password unchanged, support session not ended, still
    active). With ``staff_or_owner_id``, the user must also still be staff
    or that user. Runs in the socket's school; call it from a sync thread."""
    user = scope.get("user")
    token_key = scope.get("token_key")
    if user is None or not user.is_authenticated or not token_key:
        return False
    current = in_school(scope, _user_for, token_key)
    if not current.is_authenticated or current.pk != user.pk:
        return False
    if staff_or_owner_id is not None:
        return bool(current.is_staff or current.pk == staff_or_owner_id)
    return True


@database_sync_to_async
def _resolve(scope, token_key: str | None):
    if not token_key:
        return AnonymousUser()
    return in_school(scope, _user_for, token_key)


class TokenAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query_string = scope.get("query_string", b"").decode()
        params = parse_qs(query_string)
        token_key = (params.get("token") or [None])[0]
        scope["token_key"] = token_key
        scope["user"] = await _resolve(scope, token_key)
        return await super().__call__(scope, receive, send)
