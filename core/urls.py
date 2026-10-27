from pathlib import Path

from django.conf import settings
from django.urls import include, path, re_path
from django.views.static import serve as serve_media

from tenancy.observability.views import ingest_js_error, ingest_vitals

# School hosts (<slug>.<base>). API-only: the dashboard is a separate
# TanStack Start service (frontend/). console.<base> and api.<base> use
# core.urls_public instead.

# Serve uploaded media (e.g. the institution logo). Served regardless of
# DEBUG so the logo also works in production (offload to nginx/WhiteNoise
# later if desired). Each school sees only its own folder,
# MEDIA_ROOT/<schema_name>/; ``serve`` rejects path traversal.
_media_prefix = settings.MEDIA_URL.lstrip("/")


def school_media(request, path):
    root = Path(settings.MEDIA_ROOT) / request.tenant.schema_name
    return serve_media(request, path, document_root=root)


urlpatterns = [
    # Browser error reports, no token needed (spec 0004).
    path("api/telemetry/errors/", ingest_js_error),
    path("api/telemetry/vitals/", ingest_vitals),
    path("api/", include("ems.api.urls")),
    re_path(rf"^{_media_prefix}(?P<path>.*)$", school_media),
]
