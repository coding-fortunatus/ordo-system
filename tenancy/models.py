"""Platform data in the public schema: plans, schools and their hostnames.

Each ``School`` is a Postgres schema holding its own ``ems`` tables, picked
per request from the Host header (``tenancy.middleware.SchoolMiddleware``).
The public schema is a ``School`` row too, ``schema_name="public"``, serving
``console.<base>`` and ``api.<base>`` (spec 0003). Platform admins, their
tokens and the console audit log live here as well.
"""
from django.contrib.auth.hashers import (
    check_password,
    is_password_usable,
    make_password,
)
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import connection, models, transaction
from django.db.models.functions import Coalesce
from django_tenants.models import DomainMixin, TenantMixin
from django_tenants.utils import get_public_schema_name

from tenancy.features import KEYS


def validate_feature_keys(value):
    if value is None:
        return
    if not isinstance(value, list) or not all(isinstance(k, str) for k in value):
        raise ValidationError("Features must be a list of feature keys.")
    unknown = sorted(set(value) - KEYS)
    if unknown:
        raise ValidationError(f"Unknown feature keys: {', '.join(unknown)}.")


class Plan(models.Model):
    """A tier of limits and features. Deactivated, never deleted."""

    code = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    # Null means unlimited.
    max_students = models.PositiveIntegerField(null=True, blank=True)
    max_staff_users = models.PositiveIntegerField(null=True, blank=True)
    features = models.JSONField(default=list, validators=[validate_feature_keys])
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class School(TenantMixin):
    """One school, living in its own schema ``school_<slug>``.

    Rows are never deleted, so a slug stays reserved even after a purge.
    """

    class Status(models.TextChoices):
        PROVISIONING = "provisioning", "Provisioning"
        ACTIVE = "active", "Active"
        FAILED = "failed", "Failed"
        SUSPENDED = "suspended", "Suspended"
        PENDING_DELETION = "pending_deletion", "Pending deletion"
        PURGED = "purged", "Purged"

    # Schools whose schema is in use and must follow every deploy's
    # migrations. Provisioning and failed schemas are rebuilt from scratch.
    LIVE_STATUSES = (Status.ACTIVE, Status.SUSPENDED, Status.PENDING_DELETION)

    # The schema is created by provisioning, never as a side effect of save().
    auto_create_schema = False

    slug = models.SlugField(max_length=30, unique=True)
    name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PROVISIONING
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="schools")
    # Null means "use the plan's value" (tenancy.limits.effective).
    max_students_override = models.PositiveIntegerField(null=True, blank=True)
    max_staff_users_override = models.PositiveIntegerField(null=True, blank=True)
    features_override = models.JSONField(
        null=True, blank=True, validators=[validate_feature_keys]
    )
    first_admin_name = models.CharField(max_length=255)
    first_admin_email = models.EmailField()
    contact_name = models.CharField(max_length=255, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    country = models.CharField(max_length=2, blank=True, default="")
    timezone = models.CharField(max_length=64, default="Africa/Lagos")
    notes = models.TextField(blank=True, default="")
    plan_started_on = models.DateField(null=True, blank=True)
    renews_on = models.DateField(null=True, blank=True)
    provisioning_error = models.TextField(blank=True, default="")
    provisioning_started_at = models.DateTimeField(null=True, blank=True)
    was_ever_active = models.BooleanField(default=False)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspended_reason = models.TextField(blank=True, default="")
    deletion_requested_at = models.DateTimeField(null=True, blank=True)
    purge_after = models.DateTimeField(null=True, blank=True)
    # Bucket key of the final export, or "never_active".
    final_export_ref = models.CharField(max_length=255, blank=True, default="")
    purged_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        "PlatformAdmin",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schools_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def drop_schema(self, batch_size=None):
        """Drop this school's schema and every table in it, if it exists.

        With ``batch_size``, tables go first in batches, each in its own
        transaction: one ``DROP SCHEMA ... CASCADE`` takes a lock per object
        and runs out of ``max_locks_per_transaction`` on a big schema. Call
        it that way only outside ``atomic()``.
        """
        if self.schema_name == get_public_schema_name():
            raise ValueError("The public schema is never dropped.")
        quote = connection.ops.quote_name
        schema = quote(self.schema_name)
        if batch_size:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = %s",
                    [self.schema_name],
                )
                tables = [row[0] for row in cursor.fetchall()]
            for start in range(0, len(tables), batch_size):
                names = ", ".join(
                    f"{schema}.{quote(t)}" for t in tables[start : start + batch_size]
                )
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(f"DROP TABLE IF EXISTS {names} CASCADE")
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


class Domain(DomainMixin):
    """A hostname that resolves to a school, e.g. ``unilag.ordo.app``."""


class PlatformAdmin(models.Model):
    """A member of the Ordo team, signing in at ``console.<base>``.

    Deliberately not ``AUTH_USER_MODEL``: it only exists in the public schema,
    so a platform account can never turn up as a user inside a school.
    """

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        SUPPORT = "support", "Support"

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255)
    # Unusable until the invite is accepted (tenancy.invites).
    password = models.CharField(max_length=128)
    role = models.CharField(max_length=10, choices=Role.choices)
    # Fernet encrypted (tenancy.totp). Replaced on every login until confirmed.
    totp_secret = models.TextField(blank=True, default="")
    totp_confirmed_at = models.DateTimeField(null=True, blank=True)
    # The last accepted timestep: a code at or before it is a replay.
    totp_last_step = models.BigIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    last_login = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # DRF treats whatever authentication returns as request.user.
    is_authenticated = True
    is_anonymous = False

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        # Matched case insensitively at login, so stored in one case.
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    @property
    def is_owner(self):
        return self.role == self.Role.OWNER

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        def upgrade(raw):
            self.set_password(raw)
            self.save(update_fields=["password"])

        return check_password(raw_password, self.password, upgrade)

    def set_unusable_password(self):
        # A fresh random value each time, which also voids any invite link
        # signed over the old one.
        self.password = make_password(None)

    def has_usable_password(self):
        return is_password_usable(self.password)


class PlatformToken(models.Model):
    """A console session. Only the SHA-256 of the key is stored."""

    key_hash = models.CharField(max_length=64, primary_key=True)
    admin = models.ForeignKey(
        PlatformAdmin, on_delete=models.CASCADE, related_name="tokens"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    # Shown on the admin's account page so they can tell their sessions apart.
    user_agent = models.CharField(max_length=255, blank=True, default="")
    ip = models.GenericIPAddressField(null=True, blank=True)
    # Refreshed at most every few minutes (tenancy.auth.TOUCH_EVERY).
    last_used_at = models.DateTimeField(null=True, blank=True)

    @property
    def public_id(self):
        """Names the session in the API. A prefix of the hash, never the key."""
        return self.key_hash[:16]


class PlatformAuditLog(models.Model):
    """Every console write, sign in and failed attempt.

    Failed attempts double as the lockout counter (tenancy.lockout).
    """

    admin = models.ForeignKey(
        PlatformAdmin,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
    )
    school = models.ForeignKey(
        School,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_audit_entries",
    )
    action = models.CharField(max_length=64)
    detail = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["school", "-created_at"]),
            models.Index(fields=["action", "-created_at"]),
        ]


class SupportSession(models.Model):
    """A platform admin's visit to a school as its Ordo support user.

    The ticket is the one time credential in the link the console opens;
    only its SHA-256 is stored. Redeeming it starts the session, which
    lasts at most an hour (spec 0003, child 0006). ``tenant_user_id`` is the
    shadow user's id in the school's schema; no foreign key crosses schemas.
    """

    admin = models.ForeignKey(
        PlatformAdmin, on_delete=models.PROTECT, related_name="support_sessions"
    )
    school = models.ForeignKey(
        School, on_delete=models.PROTECT, related_name="support_sessions"
    )
    reason = models.TextField()
    ticket_hash = models.CharField(max_length=64, unique=True)
    ticket_expires_at = models.DateTimeField()
    ticket_used_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    tenant_user_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["school", "-created_at"])]


class UsageSnapshot(models.Model):
    """One day's usage of a school, taken nightly by ``snapshot_usage``.

    The console's school list shows the latest one, so listing schools never
    has to enter every schema. Kept 400 days (spec 0003, child 0005).
    """

    school = models.ForeignKey(
        School, on_delete=models.CASCADE, related_name="usage_snapshots"
    )
    taken_on = models.DateField()
    students = models.PositiveIntegerField()
    staff_users = models.PositiveIntegerField()
    jobs_last_24h = models.PositiveIntegerField()
    last_activity_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-taken_on"]
        constraints = [
            models.UniqueConstraint(fields=["school", "taken_on"], name="usage_snapshot_per_day")
        ]


class BackupRun(models.Model):
    """One attempt of the nightly whole database backup (spec 0003, child 0007).

    Written by the backup service so the console can show backup health.
    Recording never fails a backup (tenancy.backups.attempt).
    """

    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()
    ok = models.BooleanField()
    key = models.CharField(max_length=255, blank=True, default="")
    size_bytes = models.BigIntegerField(null=True, blank=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-started_at", "-id"]


class StaffDirectoryEntry(models.Model):
    """A school's staff account, copied nightly so support can find which
    school an email belongs to without entering every schema.

    Rebuilt whole per school by ``tenancy.tasks.index_staff``. Students are
    never copied, and support shadow users are excluded.
    """

    school = models.ForeignKey(
        School, on_delete=models.CASCADE, related_name="staff_directory"
    )
    email = models.EmailField(db_index=True)
    name = models.CharField(max_length=255, blank=True, default="")
    role = models.CharField(max_length=50, blank=True, default="")
    is_active = models.BooleanField(default=True)
    last_login = models.DateTimeField(null=True, blank=True)
    indexed_at = models.DateTimeField()

    class Meta:
        ordering = ["email"]


class Announcement(models.Model):
    """A message every school's dashboard shows as a banner, e.g. planned
    maintenance. Shown between ``starts_at`` and ``ends_at`` (open ended when
    null) unless withdrawn."""

    class Level(models.TextChoices):
        INFO = "info", "Information"
        WARNING = "warning", "Warning"

    message = models.TextField(max_length=500)
    level = models.CharField(max_length=10, choices=Level.choices, default=Level.INFO)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        PlatformAdmin, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="announcements",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starts_at", "-id"]

    @classmethod
    def current(cls, now):
        """Announcements showing at ``now``, newest first."""
        return cls.objects.filter(
            models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now),
            starts_at__lte=now,
            withdrawn_at__isnull=True,
        )


class ErrorGroup(models.Model):
    """Repeats of one bug in one school (spec 0004). Null school is Platform.

    Only ``tenancy.observability.capture`` writes these, through the
    ``observability`` connection, except status changes from the console.
    """

    class Source(models.TextChoices):
        API = "api", "API request"
        TASK = "task", "Task"
        JOB = "job", "Job"
        LOG = "log", "Log"
        JS = "js", "Browser"
        WS = "ws", "WebSocket"

    class Kind(models.TextChoices):
        ERROR = "error", "Error"
        EXPECTED = "expected", "Expected"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"
        IGNORED = "ignored", "Ignored"

    school = models.ForeignKey(
        School, on_delete=models.CASCADE, null=True, blank=True, related_name="error_groups"
    )
    fingerprint = models.CharField(max_length=64)
    # The source of the first occurrence.
    source = models.CharField(max_length=8, choices=Source.choices)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    exc_type = models.CharField(max_length=255)
    title = models.CharField(max_length=255)
    culprit = models.CharField(max_length=255)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)
    count = models.BigIntegerField(default=1)
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()
    # Set when created and when it regresses; alerts compare it to alerted_at.
    last_opened_at = models.DateTimeField()
    last_release = models.CharField(max_length=64, blank=True, default="")
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        PlatformAdmin, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    alerted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "fingerprint"],
                name="error_group_per_school",
                nulls_distinct=False,
            )
        ]
        indexes = [
            models.Index(fields=["school", "status", "-last_seen"]),
            models.Index(fields=["status", "kind", "last_opened_at"]),
        ]


class ErrorEvent(models.Model):
    """One stored occurrence of an ``ErrorGroup``, sampled (AC-10).

    ``context`` holds only the AC-8 allowlist; nothing else is ever stored.
    """

    group = models.ForeignKey(ErrorGroup, on_delete=models.CASCADE, related_name="events")
    school = models.ForeignKey(
        School, on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    occurred_at = models.DateTimeField()
    message = models.CharField(max_length=1000)
    # At most 50 {file, line, function, code}, innermost last.
    stack = models.JSONField(default=list)
    context = models.JSONField(default=dict)
    release = models.CharField(max_length=64, blank=True, default="")
    # The request's or job's trace (spec 0006); null when tracing was off.
    trace_id = models.CharField(max_length=32, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["group", "-occurred_at"]),
            models.Index(fields=["occurred_at"]),
            models.Index(fields=["trace_id"], name="error_event_trace"),
        ]


class HealthBucket(models.Model):
    """One school's counts for one UTC hour, copied from Redis (AC-13)."""

    school = models.ForeignKey(
        School, on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    hour = models.DateTimeField()
    requests = models.IntegerField(default=0)
    server_errors = models.IntegerField(default=0)
    client_errors = models.IntegerField(default=0)
    ws_errors = models.IntegerField(default=0)
    job_failures = models.IntegerField(default=0)
    expected_job_failures = models.IntegerField(default=0)
    js_errors = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "hour"],
                name="health_bucket_per_hour",
                nulls_distinct=False,
            )
        ]


class SchoolHealth(models.Model):
    """A school's status over the last 60 minutes, from ``observe_health``."""

    class Status(models.TextChoices):
        HEALTHY = "healthy", "Healthy"
        DEGRADED = "degraded", "Degraded"
        FAILING = "failing", "Failing"

    school = models.OneToOneField(
        School, on_delete=models.CASCADE, null=True, blank=True, related_name="health"
    )
    status = models.CharField(max_length=8, choices=Status.choices)
    reason = models.CharField(max_length=255, blank=True, default="")
    server_errors = models.IntegerField(default=0)
    client_errors = models.IntegerField(default=0)
    ws_errors = models.IntegerField(default=0)
    js_errors = models.IntegerField(default=0)
    job_failures = models.IntegerField(default=0)
    requests = models.IntegerField(default=0)
    computed_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school"], name="school_health_once", nulls_distinct=False
            )
        ]


# Traces (spec 0006). Daily partitioned tables made by raw SQL migrations:
# Django 5.0 can declare neither a partitioned table nor its composite key,
# so these are unmanaged, and the id field stands in as the ORM's key while
# the real one includes the partition column. Only the flush writes them.


class ObsSegment(models.Model):
    """The spans of one request, job or WebSocket message in one process."""

    root_span_id = models.CharField(max_length=16, primary_key=True)
    trace_id = models.CharField(max_length=32)
    started_at = models.DateTimeField()
    school = models.ForeignKey(
        School, on_delete=models.DO_NOTHING, null=True, db_constraint=False, related_name="+"
    )
    kind = models.CharField(max_length=8)
    root_name = models.CharField(max_length=255)
    route = models.CharField(max_length=255, blank=True)
    method = models.CharField(max_length=8, blank=True)
    status_code = models.SmallIntegerField(null=True)
    duration_ms = models.IntegerField()
    is_error = models.BooleanField()
    is_slow = models.BooleanField()
    span_count = models.IntegerField()
    truncated = models.BooleanField()
    user_id = models.IntegerField(null=True)
    request_id = models.CharField(max_length=32, blank=True)
    release = models.CharField(max_length=64)
    process_role = models.CharField(max_length=24)

    class Meta:
        managed = False
        db_table = "obs_segment"


class ObsSpan(models.Model):
    span_id = models.CharField(max_length=16, primary_key=True)
    trace_id = models.CharField(max_length=32)
    parent_span_id = models.CharField(max_length=16, null=True)
    root_span_id = models.CharField(max_length=16)
    started_at = models.DateTimeField()
    school = models.ForeignKey(
        School, on_delete=models.DO_NOTHING, null=True, db_constraint=False, related_name="+"
    )
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=8)
    duration_ms = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=5)
    attributes = models.JSONField(default=dict)

    class Meta:
        managed = False
        db_table = "obs_span"


class ObsLog(models.Model):
    """One stored log line with its trace (spec 0006). Not sampled: a line
    keeps its trace id even when that trace wasn't kept."""

    id = models.BigIntegerField(primary_key=True)
    logged_at = models.DateTimeField()
    school = models.ForeignKey(
        School, on_delete=models.DO_NOTHING, null=True, db_constraint=False, related_name="+"
    )
    level = models.SmallIntegerField()
    logger = models.CharField(max_length=100)
    message = models.TextField()
    trace_id = models.CharField(max_length=32, null=True)
    span_id = models.CharField(max_length=16, null=True)
    request_id = models.CharField(max_length=32, blank=True)
    user_id = models.IntegerField(null=True)
    attributes = models.JSONField(default=dict)

    class Meta:
        managed = False
        db_table = "obs_log"


# Metrics (spec 0006, child 0002). Series and rollups are normal tables; the
# minute points are a daily partitioned table like the traces.


class MetricSeries(models.Model):
    """One metric with one school and one set of label values. The metric's
    definition is code (``observability.metrics``), not a row."""

    id = models.BigAutoField(primary_key=True)
    metric_name = models.CharField(max_length=100)
    school = models.ForeignKey(School, on_delete=models.CASCADE, null=True, related_name="+")
    labels = models.JSONField(default=dict)
    # sha1 of the canonical label JSON.
    labels_hash = models.CharField(max_length=40)
    last_seen = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["metric_name", "school", "labels_hash"],
                name="metric_series_once",
                nulls_distinct=False,
            )
        ]


class ObsMetricPoint(models.Model):
    """One series' values in one UTC minute."""

    series_id = models.BigIntegerField(primary_key=True)
    minute = models.DateTimeField()
    count = models.BigIntegerField()
    sum = models.FloatField()
    min = models.FloatField(null=True)
    max = models.FloatField(null=True)
    last = models.FloatField(null=True)
    buckets = ArrayField(models.IntegerField(), null=True)

    class Meta:
        managed = False
        db_table = "obs_metric_point"


class MetricRollup(models.Model):
    """One series' values in one UTC hour, kept ``OBS_ROLLUP_KEEP_DAYS``."""

    id = models.BigAutoField(primary_key=True)
    series = models.ForeignKey(MetricSeries, on_delete=models.CASCADE, related_name="rollups")
    hour = models.DateTimeField()
    count = models.BigIntegerField()
    sum = models.FloatField()
    min = models.FloatField(null=True)
    max = models.FloatField(null=True)
    last = models.FloatField(null=True)
    buckets = ArrayField(models.IntegerField(), null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["series", "hour"], name="metric_rollup_once")
        ]


class MetricWatermark(models.Model):
    """How far a metric store is complete. ``metrics``: the last minute the
    flush has fully written, the line between 0 and missing (AC-11).
    ``rollups``: the end of the last hour rolled up."""

    name = models.CharField(max_length=32, primary_key=True)
    minute = models.DateTimeField()


# Dashboards and alert rules (spec 0006, children 0004 and 0005). Metric
# names, aggregations, labels and filters are checked against the registry
# when saved (``observability.query.validate``), not by the database.


class Dashboard(models.Model):
    """A shared set of saved explorer charts. Any admin may create and edit
    one; only an Owner may delete it."""

    name = models.CharField(max_length=100)
    created_by = models.ForeignKey(
        PlatformAdmin, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    updated_by = models.ForeignKey(
        PlatformAdmin, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]


class Chart(models.Model):
    """One saved explorer query. Charts always use the dashboard page's time
    range; ``school`` null follows the page's school picker."""

    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE, related_name="charts")
    title = models.CharField(max_length=100)
    metric_name = models.CharField(max_length=100)
    aggregation = models.CharField(max_length=8)
    group_by = models.CharField(max_length=50, blank=True, default="")
    filters = models.JSONField(default=dict, blank=True)
    school = models.ForeignKey(School, on_delete=models.CASCADE, null=True, related_name="+")
    position = models.SmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]


class AlertRule(models.Model):
    """A threshold on a metric, per school or for the platform total."""

    class Aggregation(models.TextChoices):
        RATE = "rate", "Rate"
        SUM = "sum", "Sum"
        AVG = "avg", "Average"
        P95 = "p95", "p95"
        LAST = "last", "Last"
        MAX = "max", "Max"

    class Comparison(models.TextChoices):
        GT = "gt", "Above"
        LT = "lt", "Below"

    class Scope(models.TextChoices):
        PER_SCHOOL = "per_school", "Each school"
        PLATFORM = "platform", "Platform total"

    name = models.CharField(max_length=100)
    metric_name = models.CharField(max_length=100)
    aggregation = models.CharField(max_length=4, choices=Aggregation.choices)
    filters = models.JSONField(default=dict, blank=True)
    comparison = models.CharField(max_length=2, choices=Comparison.choices)
    threshold = models.FloatField()
    for_minutes = models.SmallIntegerField()
    scope = models.CharField(max_length=10, choices=Scope.choices)
    enabled = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        PlatformAdmin, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]


class AlertState(models.Model):
    """A rule's latest evaluation for one target: a school, or the platform
    (``school`` null). Rebuilt by the next evaluation after a rule change."""

    class State(models.TextChoices):
        OK = "ok", "OK"
        FIRING = "firing", "Firing"
        NO_DATA = "no_data", "No data"

    rule = models.ForeignKey(AlertRule, on_delete=models.CASCADE, related_name="states")
    school = models.ForeignKey(School, on_delete=models.CASCADE, null=True, related_name="+")
    state = models.CharField(max_length=8, choices=State.choices)
    # Evaluations in a row whose latest minute was clear, while an incident
    # is open: at 5 it resolves.
    clear_streak = models.SmallIntegerField(default=0)
    last_value = models.FloatField(null=True)
    evaluated_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["rule", "school"], name="alert_state_once", nulls_distinct=False
            )
        ]


class AlertIncident(models.Model):
    """One stretch of a rule holding for a target, from opened to resolved."""

    rule = models.ForeignKey(AlertRule, on_delete=models.CASCADE, related_name="incidents")
    school = models.ForeignKey(School, on_delete=models.CASCADE, null=True, related_name="+")
    started_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True)
    # The worst value in the rule's direction while open.
    peak_value = models.FloatField()
    last_value = models.FloatField()
    notified_at = models.DateTimeField(null=True)
    resolved_notified_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            # Stops a duplicate if two evaluations ever overlap. COALESCE
            # makes the platform (null) one target: Django 5.0 writes
            # NULLS NOT DISTINCT after the WHERE of a partial index, which
            # Postgres refuses.
            models.UniqueConstraint(
                "rule",
                Coalesce("school", models.Value(0)),
                condition=models.Q(resolved_at__isnull=True),
                name="alert_incident_one_open",
            )
        ]
        indexes = [models.Index(fields=["-started_at"])]
