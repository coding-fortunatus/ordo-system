"""The portal secret, Fernet encrypted under ``PORTAL_SECRET_KEY``.

Only ``fetch`` decrypts it, inside a request. Nothing else may read the
plain value: no serializer, log line or exception message carries it.
"""
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet():
    if not settings.PORTAL_SECRET_KEY:
        raise ImproperlyConfigured("PORTAL_SECRET_KEY must be set.")
    return Fernet(settings.PORTAL_SECRET_KEY)


def origin(url):
    """``scheme://host:port`` of ``url``, lower cased, default port filled."""
    import httpx

    parsed = httpx.URL(str(url))
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{parsed.scheme}://{parsed.host}:{port}".lower()


def store(connection, secret):
    """Encrypt ``secret`` onto ``connection`` (not saved) and bind it to the
    origins of the connection's current endpoints."""
    connection.secret = _fernet().encrypt(secret.encode()).decode()
    connection.secret_last4 = secret[-4:]
    connection.secret_origins = (
        sorted({origin(e.url) for e in connection.endpoints.all()})
        if connection.pk
        else []
    )


def clear(connection):
    """Forget the secret (not saved): it has to be entered again."""
    connection.secret = ""
    connection.secret_last4 = ""
    connection.secret_origins = []


def endpoint_url_set(connection, url) -> bool:
    """Called after an endpoint is created at, or moved to, ``url``.

    A secret entered before any endpoint existed binds to the first one's
    origin. An endpoint on an origin the secret was not entered for clears
    the secret, so whoever moves it must also know the key. Saves the
    connection when it changes; True when the secret was cleared."""
    if not connection.secret:
        return False
    target = origin(url)
    bound = list(connection.secret_origins or [])
    if target in bound:
        return False
    if not bound:
        connection.secret_origins = [target]
        connection.save(update_fields=["secret_origins", "updated_at"])
        return False
    clear(connection)
    connection.save(update_fields=["secret", "secret_last4", "secret_origins", "updated_at"])
    return True


def allowed_for(connection, url) -> bool:
    return bool(connection.secret) and origin(url) in (connection.secret_origins or [])


def reveal(connection):
    return _fernet().decrypt(connection.secret.encode()).decode()
