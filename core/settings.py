import environ
from pathlib import Path
import os

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


env = environ.Env()

# Reading .env file
environ.Env.read_env(BASE_DIR / ".env")


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env(
    "SECRET_KEY", default="g*@kqr0q625831b)^msptwh#na3wkjbj5@rk9ac7=r+6613k^m")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env.bool("DEBUG", default=True)

# Every hostname derives from this: <slug>.<base> for schools, <console>.<base>
# for the console and api.<base> for the marketing site (spec 0003).
# `ordo.app` in production, `staging.ordo.app` on Render, `localhost` locally.
PLATFORM_BASE_DOMAIN = env("PLATFORM_BASE_DOMAIN", default="localhost")
# The console's subdomain. Not `admin`, which is the first name anyone tries;
# set something less obvious per deploy if wanted. The dashboard server reads
# the same variable, so set it on both.
PLATFORM_CONSOLE_SUBDOMAIN = env("PLATFORM_CONSOLE_SUBDOMAIN", default="console").lower()
# Scheme for links Ordo emails out (platform admin and school invites).
PLATFORM_URL_SCHEME = env("PLATFORM_URL_SCHEME", default="https")
# Who gets platform alerts (failed setups and backups, renewals). Empty means
# every active Owner.
PLATFORM_ALERT_EMAILS = env.list("PLATFORM_ALERT_EMAILS", default=[])
# Fernet key encrypting platform admins' TOTP secrets. Generate one with
# `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
# Losing it locks every admin out of 2FA: re-enrol them with
# `manage.py create_platform_admin --reset-2fa --email <e>`.
PLATFORM_TOTP_KEY = env("PLATFORM_TOTP_KEY", default="")

# Fernet key encrypting schools' portal sync secrets (spec 0005). Separate
# from PLATFORM_TOTP_KEY so rotating one doesn't break the other. Generate it
# the same way.
PORTAL_SECRET_KEY = env("PORTAL_SECRET_KEY", default="")
# A sync holds a data type when it would delete more than this percentage of
# that type's portal owned records (spec 0005, AC-11).
PORTAL_SYNC_MASS_DELETE_PCT = env.int("PORTAL_SYNC_MASS_DELETE_PCT", default=10)

# The leading dot admits every subdomain. Any other host gets Django's 400,
# which says nothing about which schools exist. USE_X_FORWARDED_HOST stays
# off: the school always comes from the real Host header.
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[f".{PLATFORM_BASE_DOMAIN}"])
if DEBUG or env.bool("ORDO_TESTING", default=False):
    ALLOWED_HOSTS.append(".localhost")

# Django only ever sits behind Traefik (or the dashboard server's own calls),
# which says whether the browser used https. Absolute URLs (the logo) follow.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# CSRF Configuration for production
CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=['http://localhost:8000']
)


# Security settings for production
if not DEBUG:
    SECURE_SSL_REDIRECT = False  # Traefik (VPS) or Render terminates TLS
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True


# Application definition

# One Postgres schema per school (django-tenants). Shared apps live in the
# public schema; tenant apps get their own tables in every school's schema,
# so school data never shares a table with another school's.
SHARED_APPS = [
    "django_tenants",
    "daphne",
    "tenancy",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "channels",
    "rest_framework",
    "corsheaders",
    "django_celery_results",
]

TENANT_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "rest_framework.authtoken",
    "ems",
]

INSTALLED_APPS = SHARED_APPS + [a for a in TENANT_APPS if a not in SHARED_APPS]

TENANT_MODEL = "tenancy.School"
TENANT_DOMAIN_MODEL = "tenancy.Domain"
# Sequential, and only schools in a live status (tenancy.migration_executor).
GET_EXECUTOR_FUNCTION = "tenancy.migration_executor.get_executor"

MIDDLEWARE = [
    # /healthz first: probes may send any Host header.
    "tenancy.middleware.HealthzMiddleware",
    "tenancy.middleware.SchoolMiddleware",
    # Request id and per school counters (spec 0004).
    "tenancy.observability.middleware.RequestObservabilityMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "ems.middleware.AuditLogMiddleware",
]

AUTH_USER_MODEL = "ems.User"


# School hosts get core.urls; console.<base> and api.<base> get the public one.
ROOT_URLCONF = "core.urls"
PUBLIC_SCHEMA_URLCONF = "core.urls_public"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"


# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases

# Database configuration
# Postgres is required everywhere, including tests and local dev: each school
# will live in its own Postgres schema (spec 0003), which SQLite can't do.
import dj_database_url
DATABASES = {
    'default': dj_database_url.parse(env("DATABASE_URL"))
}
DATABASES["default"]["ENGINE"] = "django_tenants.postgresql_backend"
# Error capture writes through its own connection (spec 0004), so a captured
# error is stored even when the failing request's transaction rolls back. It
# is never switched to a school schema, so it always writes the public tables.
DATABASES["observability"] = {
    **DATABASES["default"],
    "ENGINE": "django.db.backends.postgresql",
    "AUTOCOMMIT": True,
    "ATOMIC_REQUESTS": False,
    "OPTIONS": {
        **DATABASES["default"].get("OPTIONS", {}),
        "options": "-c statement_timeout=2000",
    },
    "TEST": {"MIRROR": "default"},
}
DATABASE_ROUTERS = [
    "tenancy.observability.router.ObservabilityRouter",
    "django_tenants.routers.TenantSyncRouter",
]

# Final exports of deleted schools (spec 0003, child 0004): a private S3
# compatible bucket (Cloudflare R2), never Cloudinary or the app's disk.
# pg_dump needs a direct connection, never a transaction pooler.
EXPORT_BUCKET = env("EXPORT_BUCKET", default="")
EXPORT_S3_ENDPOINT = env("EXPORT_S3_ENDPOINT", default="")
EXPORT_S3_ACCESS_KEY_ID = env("EXPORT_S3_ACCESS_KEY_ID", default="")
EXPORT_S3_SECRET_ACCESS_KEY = env("EXPORT_S3_SECRET_ACCESS_KEY", default="")
# R2 signs with "auto"; set a real region for AWS S3.
EXPORT_S3_REGION = env("EXPORT_S3_REGION", default="auto")
# Sent as x-amz-server-side-encryption. Blank leaves it to the bucket (R2
# encrypts every object at rest).
EXPORT_S3_SSE = env("EXPORT_S3_SSE", default="AES256")
EXPORT_DATABASE_URL = env("EXPORT_DATABASE_URL", default=env("DATABASE_URL"))

# Nightly backup of the whole database (spec 0003, child 0007): its own bucket,
# since EXPORT_BUCKET only holds a school's export until purge or restore.
# Endpoint and keys default to the export ones (same R2 account).
BACKUP_BUCKET = env("BACKUP_BUCKET", default="")
BACKUP_S3_ENDPOINT = env("BACKUP_S3_ENDPOINT", default=EXPORT_S3_ENDPOINT)
BACKUP_S3_ACCESS_KEY_ID = env("BACKUP_S3_ACCESS_KEY_ID", default=EXPORT_S3_ACCESS_KEY_ID)
BACKUP_S3_SECRET_ACCESS_KEY = env(
    "BACKUP_S3_SECRET_ACCESS_KEY", default=EXPORT_S3_SECRET_ACCESS_KEY
)
BACKUP_S3_REGION = env("BACKUP_S3_REGION", default=EXPORT_S3_REGION)
# Blank by default: R2 encrypts at rest and may reject the header.
BACKUP_S3_SSE = env("BACKUP_S3_SSE", default="")
BACKUP_DATABASE_URL = env("BACKUP_DATABASE_URL", default=EXPORT_DATABASE_URL)
BACKUP_TIME_UTC = env("BACKUP_TIME_UTC", default="01:00")
# The outside monitor (a dead man's switch). Blank sends no pings.
BACKUP_HEARTBEAT_URL = env("BACKUP_HEARTBEAT_URL", default="")
BACKUP_HEARTBEAT_FILE = env("BACKUP_HEARTBEAT_FILE", default="/tmp/ordo-backup-ok")

# Error observability (spec 0004). The master switch turns off all capture,
# counting and alerting.
OBSERVABILITY_ENABLED = env.bool("OBSERVABILITY_ENABLED", default=True)
# The release id (git short sha), stamped on every captured error.
ORDO_RELEASE = env("ORDO_RELEASE", default="dev")
# Which connection capture writes through. Tests set "default" so captures
# join the test's transaction (a mirror is a separate connection).
OBSERVABILITY_DB_ALIAS = "observability"
# Counters, sampling and alert throttles. Same Redis as Celery by default.
OBSERVABILITY_REDIS_URL = env(
    "OBSERVABILITY_REDIS_URL",
    default=env("REDIS_URL", default="redis://localhost:6379/0"),
)
# Health status thresholds (AC-12).
OBS_FAILING_ERRORS = env.int("OBS_FAILING_ERRORS", default=20)
OBS_JS_DEGRADED = env.int("OBS_JS_DEGRADED", default=10)
OBS_WS_SPIKE = env.int("OBS_WS_SPIKE", default=20)
OBS_4XX_SPIKE_MIN = env.int("OBS_4XX_SPIKE_MIN", default=100)
OBS_4XX_SPIKE_FACTOR = env.float("OBS_4XX_SPIKE_FACTOR", default=3)
# Browser error ingest limits, per minute (AC-5).
OBS_JS_IP_PER_MIN = env.int("OBS_JS_IP_PER_MIN", default=20)
OBS_JS_SCHOOL_PER_MIN = env.int("OBS_JS_SCHOOL_PER_MIN", default=200)
# Retention, in days (AC-20).
OBS_EVENT_KEEP_DAYS = env.int("OBS_EVENT_KEEP_DAYS", default=30)
OBS_BUCKET_KEEP_DAYS = env.int("OBS_BUCKET_KEEP_DAYS", default=90)
OBS_GROUP_KEEP_DAYS = env.int("OBS_GROUP_KEEP_DAYS", default=90)

# Stdout looks as it did before; ERROR records on Ordo's own loggers are also
# captured (AC-4). ``ordo.observability`` reports capture failures and is never
# captured itself.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler", "level": "INFO"},
        "observability": {
            "class": "tenancy.observability.handler.ObservabilityLogHandler",
            "level": "ERROR",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "ems": {"handlers": ["observability"], "level": "INFO"},
        "tenancy": {"handlers": ["observability"], "level": "INFO"},
        "core": {"handlers": ["observability"], "level": "INFO"},
        "ordo.observability": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

# Cache keys carry the schema name, so one school never reads another's.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "KEY_FUNCTION": "django_tenants.cache.make_key",
    },
}


# Password validation
# https://docs.djangoproject.com/en/5.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.0/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/

STATIC_URL = "/static/"

# Static files collection directory
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# Directory where source static files are located (admin/DRF static only —
# the frontend is its own service). The empty `static/` dir keeps
# DEBUG=True local runs working.
STATICFILES_DIRS = [
    BASE_DIR / "static",
]

# Django 5.0+ uses STORAGES instead of deprecated STATICFILES_STORAGE
# Use standard FileSystemStorage - WhiteNoise middleware will handle serving and compression
STORAGES = {
    "default": {
        # MEDIA_ROOT/<schema_name>/, resolved on every call.
        "BACKEND": "ems.cloudinary_storage.SchoolFileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# WhiteNoise configuration for better static file serving
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True if DEBUG else False

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Uploaded institution logo. Set in production: the container's own disk is
# wiped on every deploy. Format: cloudinary://<api_key>:<api_secret>@<cloud_name>
CLOUDINARY_URL = env("CLOUDINARY_URL", default="")
# Opt out of the ems.E001 deploy check: only for one instance whose
# MEDIA_ROOT is on a persistent volume.
LOCAL_MEDIA_PERSISTENT = env.bool("LOCAL_MEDIA_PERSISTENT", default=False)

# Default primary key field type
# https://docs.djangoproject.com/en/5.0/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Celery Configuration. Falls back to REDIS_URL so platforms that expose a
# single Redis URL (Railway, Render, Fly, etc.) work without extra config.
CELERY_BROKER_URL = env(
    "CELERY_BROKER_URL",
    default=env("REDIS_URL", default="redis://localhost:6379/0"),
)
CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "default"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
# Beat schedules in UTC; school tasks activate the school's own zone.
CELERY_TIMEZONE = "UTC"
CELERY_TASK_TRACK_STARTED = True
# django_celery_results would store every school's task arguments in the
# public schema, outside isolation. BackgroundJob already records outcomes.
CELERY_TASK_IGNORE_RESULT = True
CELERY_RESULT_EXPIRES = 24 * 60 * 60  # cleans up anything that opts back in
# One message per worker process, acked when done: jobs from different
# schools start in arrival order instead of queueing behind a prefetch.
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes (hard: worker SIGKILL, uncatchable)
# Soft limit fires ~5 min earlier as a catchable SoftTimeLimitExceeded so a
# task can mark its BackgroundJob 'failed' with a useful message instead of
# being SIGKILL'd mid-run and leaving the job stuck in 'running' forever.
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60  # 25 minutes
CELERY_RESULT_EXTENDED = True

# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "ems.api.auth.SchoolTokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "ems.api.pagination.DefaultPagination",
    "PAGE_SIZE": 15,
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ],
    "EXCEPTION_HANDLER": "ems.api.exceptions.handler",
}

# The dashboard (frontend/) runs on another origin and calls this API from
# the browser with a token header — no cookies, so no credentials. In
# production set this to the dashboard origin, e.g. https://ordo.app. The
# marketing site (separate repo) only calls the API server-side, so it needs
# no entry. WebSocket handshakes are checked against the same list
# (core/asgi.py).
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:3000"],
)
CORS_ALLOW_CREDENTIALS = False
CORS_EXPOSE_HEADERS = ["Content-Disposition", "X-Request-ID"]

# Channels — falls back to REDIS_URL if CHANNEL_REDIS_URL isn't set, so
# managed-Redis providers that hand you one URL just work. Channel keys
# are namespaced under "asgi:" and don't collide with Celery's keys.
_CHANNEL_REDIS = env(
    "CHANNEL_REDIS_URL",
    default=env("REDIS_URL", default="redis://localhost:6379/0"),
)
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [_CHANNEL_REDIS]},
    },
}

# Outgoing email (invites), always sent from a Celery task so no request
# waits on SMTP. Locally, with no EMAIL_HOST, messages print to the console.
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_TIMEOUT = 30
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="Ordo <no-reply@ordo.app>")
if DEBUG and not EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# The test suite runs without Redis (CI has none), so job-progress and
# notification pushes go through an in-process layer there. A fast hasher
# keeps the many test users cheap. ORDO_TESTING is set by whatever runs the
# tests (CI, or `ORDO_TESTING=1 python manage.py test`), so it holds under
# any runner rather than depending on argv.
TESTING = env.bool("ORDO_TESTING", default=False)
if TESTING:
    CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    }
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    PLATFORM_TOTP_KEY = PLATFORM_TOTP_KEY or "nA0rJ8bFrYBqf6ZYpAgTEmCgwQqP_3ZD2Pz0F2Gm1Wc="
    EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    OBSERVABILITY_DB_ALIAS = "default"
    OBSERVABILITY_REDIS_URL = "memory://"
