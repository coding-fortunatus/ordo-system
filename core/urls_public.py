from django.urls import include, path

from tenancy.observability.views import ingest_js_error, ingest_vitals

# The public schema: console.<base> (platform console API) and api.<base>
# (the marketing site's server side calls). No school data and no media here.

urlpatterns = [
    # Browser error reports, no token needed (spec 0004).
    path("api/telemetry/errors/", ingest_js_error),
    path("api/telemetry/vitals/", ingest_vitals),
    path("api/public/", include("ems.api.public_urls")),
    path("api/platform/", include("tenancy.urls")),
]
