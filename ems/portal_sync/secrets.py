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


def store(connection, secret):
    """Encrypt ``secret`` onto ``connection`` (not saved)."""
    connection.secret = _fernet().encrypt(secret.encode()).decode()
    connection.secret_last4 = secret[-4:]


def reveal(connection):
    return _fernet().decrypt(connection.secret.encode()).decode()
