from django.apps import AppConfig
from django.conf import settings


class TenancyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tenancy"

    def ready(self):
        # Capture points that hang off signals (spec 0004).
        from tenancy.observability import builtin, middleware, otel, signals, tasks  # noqa: F401

        signals.connect()
        # Only the web process traces from here (spec 0006): ready() also
        # runs in Celery parents, beat and management commands, which must
        # not start exporter threads. Workers set up after the fork.
        if settings.OBS_PROCESS_ROLE in otel.WEB_ROLES:
            otel.setup()
