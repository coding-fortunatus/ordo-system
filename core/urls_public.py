from django.urls import include, path

from tenancy.demo_requests import PublicDemoRequestView
from tenancy.observability.views import ingest_js_error, ingest_vitals

# The public schema: console.<base> (platform console API) and api.<base>
# (the marketing site's server side calls). No school data and no media here.

urlpatterns = [
    # Browser error reports, no token needed (spec 0004).
    path("api/telemetry/errors/", ingest_js_error),
    path("api/telemetry/vitals/", ingest_vitals),
    # The marketing site's demo form, posted from its server.
    path("api/public/demo-requests/", PublicDemoRequestView.as_view()),
    path("api/public/", include("ems.api.public_urls")),
    path("api/platform/", include("tenancy.urls")),
]
