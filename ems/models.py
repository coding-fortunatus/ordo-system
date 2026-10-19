# Create your models here.
from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.db import models
from django.db.models import Count, F, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce

from .cloudinary_storage import select_logo_storage
from .identifiers import clean_number_text
from .seating_rules import DEFAULT_SEAT_ORDER, RULE_CHOICES, SEAT_ORDERS, STRICT


class Faculty(models.Model):
    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=50, unique=True)
    # Set while the school's portal owns this record (spec 0005). Null means
    # Ordo owns it; Postgres treats nulls as distinct, so they never collide.
    external_id = models.CharField(max_length=255, null=True, blank=True, unique=True)

    class Meta:
        verbose_name_plural = "Faculties"
        ordering = ["name"]

    def __str__(self) -> str:
        return str(self.name)


class Department(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=50, unique=True)
    faculty = models.ForeignKey(
        Faculty,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="departments",
    )
    # Set while the school's portal owns this record (spec 0005). Null means
    # Ordo owns it; Postgres treats nulls as distinct, so they never collide.
    external_id = models.CharField(max_length=255, null=True, blank=True, unique=True)

    def __str__(self) -> str:
        return str(self.name)


class SystemSettings(models.Model):
    session = models.CharField(
        max_length=200, default="2024/2025", unique=True)
    semester = models.CharField(max_length=100, default="1st Semester")
    has_timetable = models.BooleanField(default=False)

    # Institution branding — shown in the UI beside the app logo and printed
    # on exported documents.
    institution_name = models.CharField(max_length=255, blank=True, default="")
    institution_short_name = models.CharField(
        max_length=50, blank=True, default="")
    institution_address = models.TextField(blank=True, default="")
    exam_heading = models.CharField(max_length=255, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    contact_phone = models.CharField(max_length=50, blank=True, default="")
    # On Cloudinary when CLOUDINARY_URL is set: the app's own disk does not
    # survive a redeploy, and the attendance sheets print this image.
    logo = models.ImageField(
        upload_to="branding/",
        storage=select_logo_storage,
        null=True,
        blank=True,
    )
    # Primary brand colour as a hex string (e.g. "#7C3AED"); blank = default theme.
    brand_color = models.CharField(max_length=9, blank=True, default="")

    # Publication is separate from generation: department officers see the
    # timetable only once a super admin publishes it, and publishing is what
    # notifies them. NULL = draft. A new timetable or a reset clears it.
    timetable_published_at = models.DateTimeField(null=True, blank=True)
    timetable_published_by = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    @property
    def timetable_published(self) -> bool:
        return self.timetable_published_at is not None

    def __str__(self):
        return str(f"{self.session} ' - ' {self.semester}")


def _default_excluded_weekdays():
    return [6]


def _default_class_period_overrides():
    return {}


def _default_cbe_faculty_groups():
    return {}


def _default_hall_course_limits():
    from .halls import DEFAULT_COURSE_LIMITS

    return [dict(t) for t in DEFAULT_COURSE_LIMITS]


def _default_hall_group_order():
    return []


# Kept only so historical migrations can import them by name.
def _default_seating_patterns():
    return ["checkerboard", "diagonal", "sequential"]


SEAT_PATTERN_CHOICES = (
    ("checkerboard", "Checkerboard (strict spacing, ~50% capacity)"),
    ("sequential", "Sequential (full capacity, adjacency-only spacing)"),
)


class GenerationConstraints(models.Model):
    """Singleton row holding admin-tunable generation knobs."""

    cbe_autosplit_threshold = models.PositiveIntegerField(default=9000)
    cbe_fullday_threshold = models.PositiveIntegerField(default=4500)
    cbe_daily_cap_per_period = models.PositiveIntegerField(default=4500)

    # Number of buckets a large CBE course splits into, and the
    # faculty-slug → group-number mapping that drives the split.
    cbe_group_count = models.PositiveIntegerField(default=2)
    cbe_faculty_groups = models.JSONField(default=_default_cbe_faculty_groups)
    # Hard cap on how full any hall may get, as a fraction of its seats for
    # the seat pattern. Timetable, distribution, allocation, reconcile and
    # manual assignment all stop at it.
    pbe_hall_utilization = models.DecimalField(
        max_digits=3, decimal_places=2, default=0.90
    )
    # Most different courses one hall may hold, by hall size (see halls.py):
    # [{"max_seats": 120, "courses": 4}, ..., {"max_seats": null, ...}].
    hall_course_limits = models.JSONField(default=_default_hall_course_limits)
    # Hall groups in the order they stand on the ground, so a course that
    # outgrows one group spills into its neighbour (see halls.py).
    hall_group_order = models.JSONField(default=_default_hall_group_order)
    # Seat pattern drives effective hall capacity at every stage (timetable
    # seat-budgeting, distribution packing, allocation placement).
    #   * checkerboard → ~50% of grid, students never within 1 cell of each other
    #   * sequential   → full grid, only direct adjacency is blocked
    seat_pattern = models.CharField(
        max_length=20, choices=SEAT_PATTERN_CHOICES, default="checkerboard"
    )
    excluded_weekdays = models.JSONField(default=_default_excluded_weekdays)

    # Map of class-name → "AM" or "PM". Applies to every department that uses
    # that name (e.g. "Level 100" sets AM/PM for every department's Level 100).
    # Missing/blank names default to AM. CBE courses always go AM regardless.
    class_period_overrides = models.JSONField(default=_default_class_period_overrides)

    remainder_merge_threshold = models.PositiveIntegerField(default=5)
    # A course of at most this many students in a slot never opens a hall
    # of its own: distribution folds it into a hall already in use, which
    # may go one course over its course limit for it. 0 turns this off.
    small_course_threshold = models.PositiveIntegerField(default=10)

    placement_success_threshold_pct = models.PositiveIntegerField(default=60)

    configured_at = models.DateTimeField(null=True, blank=True)
    configured_by = models.ForeignKey(
        "User", on_delete=models.SET_NULL, null=True, blank=True
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Generation constraints"
        verbose_name_plural = "Generation constraints"

    def __str__(self):
        return (
            f"GenerationConstraints (configured: "
            f"{'yes' if self.configured_at else 'no'})"
        )


class AllUsersManager(BaseUserManager):
    """Every user, Ordo support's shadow users included (``User.all_objects``)."""

    # Historical models in migrations get a plain manager; this one's
    # filtered sibling would break on tables older than its column.
    use_in_migrations = False

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("The Email field must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.SUPER_ADMIN)

        return self.create_user(email, password, **extra_fields)


class UserManager(AllUsersManager):
    """The school's own users. Support shadow users are hidden from every
    query that doesn't ask for them by name (spec 0003, child 0006)."""

    def get_queryset(self):
        return super().get_queryset().filter(support_expires_at__isnull=True)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        SUPER_ADMIN = "SA", "Super Admin"
        DATA_OFFICER = "DO", "Data Officer"
        FACULTY_OFFICER = "FO", "Faculty Officer"
        EXAM_COMMITTEE = "ECM", "Exam Committee Member"

    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=30, blank=True)
    last_name = models.CharField(max_length=30, blank=True)
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, null=True, blank=True
    )
    # Admin-side role. Null means a department officer (scoped to ``department``).
    role = models.CharField(
        max_length=4, choices=Role.choices, null=True, blank=True
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # Set only on an Ordo support shadow user: its access ends at this time.
    support_expires_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    objects = UserManager()
    all_objects = AllUsersManager()

    class Meta:
        # Foreign keys (an AuditLog row, a job) still reach a support user.
        base_manager_name = "all_objects"

    def __str__(self):
        return self.email

    @property
    def is_support(self) -> bool:
        return self.support_expires_at is not None

    @property
    def is_super_admin(self) -> bool:
        return bool(self.is_superuser or self.role == self.Role.SUPER_ADMIN)

    @property
    def can_manage_data(self) -> bool:
        """Super admins and data officers manage uploaded data entities."""
        return bool(self.is_super_admin or self.role == self.Role.DATA_OFFICER)

    @property
    def can_manage_faculties(self) -> bool:
        return bool(self.is_super_admin or self.role == self.Role.FACULTY_OFFICER)

    @property
    def is_committee(self) -> bool:
        """Any admin-side role — the exam-committee baseline (dashboard + export)."""
        return self.role in {
            self.Role.SUPER_ADMIN,
            self.Role.DATA_OFFICER,
            self.Role.FACULTY_OFFICER,
            self.Role.EXAM_COMMITTEE,
        }


class Invite(models.Model):
    """An emailed link that lets someone join this school and set a password.

    Only the SHA-256 of the token is stored; the raw token is in the email
    alone (spec 0003, child 0004). Open means neither accepted nor revoked,
    and only one open invite may exist per email.
    """

    email = models.EmailField()
    # Same meaning as ``User.role``: null is a department officer.
    role = models.CharField(
        max_length=4, choices=User.Role.choices, null=True, blank=True
    )
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, null=True, blank=True
    )
    token_hash = models.CharField(max_length=64, unique=True)
    # Null when Ordo sent it (the first admin, made at provisioning).
    invited_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invites_sent",
    )
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["email"],
                condition=models.Q(accepted_at__isnull=True, revoked_at__isnull=True),
                name="one_open_invite_per_email",
            )
        ]

    def __str__(self):
        return self.email


class Course(models.Model):
    COURSE_TYPE = (("PBE", "PBE"), ("CBE", "CBE"))
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50)
    exam_type = models.CharField(
        max_length=50, default="PBE", choices=COURSE_TYPE)
    # Set while the school's portal owns this record (spec 0005). Null means
    # Ordo owns it; Postgres treats nulls as distinct, so they never collide.
    external_id = models.CharField(max_length=255, null=True, blank=True, unique=True)

    def __str__(self) -> str:
        return f"{self.name} - {self.code}"


def _student_count_filter():
    """A class's student list: students whose ``level`` is the class **and**
    whose ``department`` is the class's department. This is the same set
    allocation seats, so planning and seating always agree (spec 0001)."""
    return Q(student__department=F("department"))


class ClassQuerySet(models.QuerySet):
    """Student count helpers. Every planning stage reads the live student
    count through these. Nothing that plans seats reads ``Class.size``."""

    def active(self):
        return self.filter(is_active=True)

    def with_student_count(self):
        """Annotate ``_student_count`` = live number of students in the class.

        Classes loaded through this carry the count, so the ``student_count``
        property resolves without an extra query.
        """
        return self.annotate(
            _student_count=Count(
                "student", filter=_student_count_filter(), distinct=True
            )
        )

    def student_count_map(self) -> dict:
        """Map of ``class id -> student count``. No fallback: a class with no
        students maps to 0."""
        return dict(self.with_student_count().values_list("id", "_student_count"))

    def total_student_count(self) -> int:
        """Sum of student counts over the active classes in the queryset."""
        return sum(self.active().student_count_map().values())

    def sync_student_counts(self, class_ids) -> int:
        """Set ``size`` to the live student count for ``class_ids`` in one
        ``UPDATE``. ``size`` is a display copy only; planning never reads it.
        Call after every write that adds, removes or moves students (bulk
        writes skip model signals)."""
        ids = {cid for cid in class_ids if cid}
        if not ids:
            return 0
        count_sq = (
            Student.objects.filter(
                level_id=OuterRef("pk"), department_id=OuterRef("department_id")
            )
            .order_by()
            .values("level_id")
            .annotate(n=Count("id"))
            .values("n")
        )
        return self.filter(pk__in=ids).update(
            size=Coalesce(Subquery(count_sq), Value(0))
        )


class Class(models.Model):
    name = models.CharField(max_length=25, null=True, blank=True)
    courses = models.ManyToManyField(Course, related_name="courses")
    department = models.ForeignKey(
        Department, related_name="class_dep", on_delete=models.CASCADE
    )
    # Display copy of the live student count, kept in step by
    # ``ClassQuerySet.sync_student_counts``. Read only in the API and never
    # read by planning code: plan from ``student_count`` instead.
    size = models.IntegerField(default=0)
    # Off = the class is not sitting this session. It is left out of every
    # planning stage and never blocks a generate run.
    is_active = models.BooleanField(default=True)
    # Optional override for the VISA short code. Blank => auto-derived from
    # ``name`` (see ems.directory.derive_visa_code).
    visa_code = models.CharField(max_length=50, blank=True, default="")
    # Set while the school's portal owns this record (spec 0005). Null means
    # Ordo owns it; Postgres treats nulls as distinct, so they never collide.
    external_id = models.CharField(max_length=255, null=True, blank=True, unique=True)

    objects = ClassQuerySet.as_manager()

    @property
    def student_count(self) -> int:
        """Live number of students in this class (level and department match).

        Uses the ``_student_count`` annotation when present (see
        ``ClassQuerySet.with_student_count``); otherwise counts on demand.
        """
        annotated = getattr(self, "_student_count", None)
        if annotated is not None:
            return annotated
        return self.student_set.filter(department_id=self.department_id).count()

    @property
    def full_label(self) -> str:
        """Class name prefixed with its department code, e.g. ``"AC ND II"``.

        Bare class names like ``"ND II"`` are identical across departments, so
        every display/export of a class should carry the department code.
        Skips the prefix when the name already starts with the code.
        """
        dept = getattr(self, "department", None)
        code = (dept.slug.upper() if dept and dept.slug else "").strip()
        name = (self.name or "").strip()
        if code and not name.upper().startswith(code):
            return f"{code} {name}".strip()
        return name

    @property
    def visa_label(self) -> str:
        from ems.directory import derive_visa_code

        if self.visa_code.strip():
            return self.visa_code.strip()
        # The program is usually the department code, so fold it into the name
        # before deriving (e.g. dept "AC" + "ND II" -> "AC ND II" -> "AC II").
        # Skip if the name already starts with the code to avoid doubling.
        dept = getattr(self, "department", None)
        code = (dept.slug.upper() if dept and dept.slug else "").strip()
        name = self.name or ""
        if code and not name.upper().startswith(code):
            name = f"{code} {name}".strip()
        return derive_visa_code(name)

    def __str__(self) -> str:
        return f"{self.name} - {self.department.name}"


class HallQuerySet(models.QuerySet):
    def open(self):
        """Halls generation may use. A closed hall keeps its data and any
        seats already given out, but no new run plans into it."""
        return self.filter(is_open=True)


class Hall(models.Model):
    name = models.CharField(max_length=255)
    capacity = models.IntegerField()
    # Deprecated: nothing reads these. Kept so existing data survives; no
    # longer exposed by the API, the UI or the hall upload.
    max_students = models.IntegerField(default=0)
    min_courses = models.IntegerField(default=0)
    rows = models.IntegerField()
    columns = models.IntegerField()
    # Halls that stand together (AUD, BE ...). Filled from the name when
    # left blank; see halls.py.
    group = models.CharField(max_length=32, blank=True, default="")
    # Seat mask for a hall that is not a full rows x columns rectangle: one
    # string per row, "X" a seat and "." no seat (see seating_rules).
    # NULL = every cell is a seat.
    layout = models.JSONField(null=True, blank=True)
    # How seats are numbered (see seating_rules.SEAT_ORDERS). Changing it
    # renumbers the hall, so its slots need allocating again.
    seat_order = models.CharField(
        max_length=20,
        choices=[(k, v) for k, v in SEAT_ORDERS.items()],
        default=DEFAULT_SEAT_ORDER,
    )
    # Closed = left out of timetable, distribution and allocation (repairs,
    # booked for something else). Switching it back on needs no other change.
    is_open = models.BooleanField(default=True)

    objects = HallQuerySet.as_manager()

    def __str__(self) -> str:
        return str(self.name)

    def save(self, *args, **kwargs):
        from .halls import group_from_name

        self.group = (self.group or "").strip().upper() or group_from_name(self.name)
        super().save(*args, **kwargs)

    @property
    def seat_count(self) -> int:
        from .seating_rules import layout_of

        return layout_of(self).seat_count


PERIOD = (('AM', 'AM'), ('PM', 'PM'))


class TimeTable(models.Model):

    course = models.ForeignKey(
        Course, related_name="timetable_course", on_delete=models.CASCADE)
    class_obj = models.ForeignKey(Class, on_delete=models.CASCADE,
                                  related_name="timetable_class")
    period = models.CharField(max_length=50, choices=PERIOD)
    date = models.DateField()
    # The class's student count when distribution last ran for this row.
    # NULL = never distributed. Allocation refuses a slot whose rows no longer
    # match the live count (the list changed after distribution).
    planned_students = models.IntegerField(null=True, blank=True)
    # Written once by timetable generation (spec 0002). "relaxed" = the course
    # is too big for one period under the strict rule and is seated with only
    # side, front and back neighbours blocked. Every row of one course in a
    # slot carries the same value.
    seating_rule = models.CharField(
        max_length=10, choices=RULE_CHOICES, default=STRICT
    )

    class Meta:
        # Every generation step reads the timetable one (date, period) slot
        # at a time.
        indexes = [models.Index(fields=['date', 'period'])]

    def __str__(self) -> str:
        return f"{self.class_obj.department.name} | {self.course.code} | {self.date} | {self.period}"


class DistributionItem(models.Model):
    schedule = models.ForeignKey(TimeTable, on_delete=models.CASCADE)
    no_of_students = models.IntegerField()


class Distribution(models.Model):
    hall = models.ForeignKey(Hall, on_delete=models.CASCADE)
    items = models.ManyToManyField(DistributionItem)
    date = models.CharField(max_length=15, null=True)
    period = models.CharField(max_length=2, null=True)

    class Meta:
        indexes = [models.Index(fields=['date', 'period'])]


class Student(models.Model):
    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255)
    matric_no = models.CharField(max_length=15, db_index=True)
    email = models.EmailField()
    department = models.ForeignKey(Department, on_delete=models.CASCADE)
    level = models.ForeignKey(Class, on_delete=models.CASCADE)
    phone = models.CharField(max_length=15, blank=True)
    # Set while the school's portal owns this record (spec 0005). Null means
    # Ordo owns it; Postgres treats nulls as distinct, so they never collide.
    external_id = models.CharField(max_length=255, null=True, blank=True, unique=True)

    class Meta:
        indexes = [
            models.Index(fields=['department', 'level']),
            models.Index(fields=['matric_no', 'department']),
        ]

    def save(self, *args, **kwargs):
        # Bulk uploads clean these before bulk_create; this covers every
        # single-student save.
        self.matric_no = clean_number_text(self.matric_no)
        self.phone = clean_number_text(self.phone)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name} - {self.matric_no}"


class SeatArrangement(models.Model):
    period = models.CharField(max_length=50, choices=PERIOD)
    date = models.DateField()
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, null=True, blank=True)
    seat_number = models.IntegerField(null=True, blank=True)
    hall = models.ForeignKey(Hall, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    cls = models.ForeignKey(Class, on_delete=models.CASCADE)

    class Meta:
        # Allocation deletes, counts and re-reads a whole slot at a time.
        # Without this every such query scanned all of the session's seats
        # (~160k rows), and the delete-and-reinsert churn of re-runs made
        # each scan slower still.
        indexes = [models.Index(fields=['date', 'period', 'hall'])]

    def __str__(self) -> str:
        return f"{self.student.matric_no} - {self.seat_number or 'None'} - Course {self.course.code} - Date {self.date} - Period {self.period}"


class AuditLog(models.Model):
    """Append-only system activity trail.

    One row per authenticated mutating request (and per auth event). Written
    by ``ems.middleware.AuditLogMiddleware`` after the response is produced, so
    the recorded ``status_code`` reflects whether the action actually
    succeeded. Read-only at the API; never edited or deleted by app code.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    # Email snapshotted at write time so the trail survives user deletion and
    # captures failed-login attempts (where no user is attached).
    user_email = models.CharField(max_length=254, blank=True, default="")
    action = models.CharField(max_length=255)
    method = models.CharField(max_length=10, blank=True, default="")
    path = models.CharField(max_length=512, blank=True, default="")
    # Resource the action targeted, e.g. "user" / "department", plus its id.
    object_type = models.CharField(max_length=100, blank=True, default="")
    object_id = models.CharField(max_length=64, blank=True, default="")
    status_code = models.PositiveIntegerField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["object_type", "-created_at"]),
        ]

    def __str__(self) -> str:
        who = self.user_email or "anonymous"
        return f"{who} — {self.action} ({self.created_at:%Y-%m-%d %H:%M})"

    @property
    def succeeded(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 400


class BackgroundJob(models.Model):
    """Track status of long-running background tasks"""
    JOB_TYPES = (
        ('timetable', 'Timetable Generation'),
        ('distribution', 'Distribution Generation'),
        ('allocation', 'Seat Allocation'),
        ('portal_sync', 'Portal Sync'),
    )
    
    STATUSES = (
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('success', 'Success'),
        ('failed', 'Failed'),
    )
    
    job_id = models.CharField(max_length=255, unique=True, db_index=True)
    job_type = models.CharField(max_length=50, choices=JOB_TYPES)
    status = models.CharField(max_length=20, choices=STATUSES, default='pending')
    progress = models.IntegerField(default=0)
    total_steps = models.IntegerField(default=100)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    result_data = models.JSONField(default=dict, blank=True)
    
    # Job-specific parameters
    params = models.JSONField(default=dict, blank=True)
    
    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['job_id']),
            models.Index(fields=['created_by', '-started_at']),
            models.Index(fields=['status', '-started_at']),
        ]
    
    def __str__(self):
        return f"{self.get_job_type_display()} - {self.status} ({self.progress}%)"
    
    @property
    def progress_percentage(self):
        if self.total_steps == 0:
            return 0
        return min(100, int((self.progress / self.total_steps) * 100))


class Notification(models.Model):
    """One message to one user. Written by ``ems.notifications.notify``,
    which also pushes it over the user's WebSocket group."""

    class Kind(models.TextChoices):
        TIMETABLE_PUBLISHED = "timetable_published", "Timetable published"
        TIMETABLE_WITHDRAWN = "timetable_withdrawn", "Timetable withdrawn"
        PORTAL_SYNC = "portal_sync", "Portal sync"
        GENERAL = "general", "General"

    recipient = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField(max_length=40, choices=Kind.choices, default=Kind.GENERAL)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, default="")
    # In-app route the notification opens, e.g. "/timetable".
    link = models.CharField(max_length=255, blank=True, default="")
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["recipient", "-created_at"]),
            models.Index(fields=["recipient", "read_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.recipient_id}: {self.title}"


# Portal sync (spec 0005) ----------------------------------------------------


class PortalConnection(models.Model):
    """The school's own portal Ordo pulls its data from. At most one row.

    ``secret`` is a Fernet token under ``PORTAL_SECRET_KEY`` (see
    ``ems.portal_sync.secrets``); it is decrypted only inside a fetch and
    never serialized. ``first_sync_approved_at`` null means the next sync is
    a preview.
    """

    class Mode(models.TextChoices):
        COMBINED = "combined", "One endpoint for everything"
        PER_TYPE = "per_type", "One endpoint per data type"

    class AuthType(models.TextChoices):
        API_KEY_HEADER = "api_key_header", "API key header"
        BEARER = "bearer", "Bearer token"

    mode = models.CharField(max_length=20, choices=Mode.choices)
    auth_type = models.CharField(max_length=20, choices=AuthType.choices)
    header_name = models.CharField(max_length=100, blank=True, default="")
    secret = models.TextField()
    secret_last4 = models.CharField(max_length=4, blank=True, default="")
    first_sync_approved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Portal connection ({self.mode})"


class PortalEndpoint(models.Model):
    class Kind(models.TextChoices):
        COMBINED = "combined", "Combined"
        DEPARTMENTS = "departments", "Departments"
        CLASSES = "classes", "Classes"
        COURSES = "courses", "Courses"
        STUDENTS = "students", "Students"

    class Format(models.TextChoices):
        JSON = "json", "JSON"
        CSV = "csv", "CSV"

    class Pagination(models.TextChoices):
        NONE = "none", "None"
        PAGE_PARAM = "page_param", "Page number parameter"
        NEXT_LINK = "next_link", "Next page link"

    connection = models.ForeignKey(
        PortalConnection, on_delete=models.CASCADE, related_name="endpoints"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    url = models.URLField(max_length=2000)
    format = models.CharField(max_length=10, choices=Format.choices, default=Format.JSON)
    rows_path = models.CharField(max_length=200, blank=True, default="")
    pagination = models.CharField(
        max_length=20, choices=Pagination.choices, default=Pagination.NONE
    )
    page_param = models.CharField(max_length=50, blank=True, default="")
    next_path = models.CharField(max_length=200, blank=True, default="")
    # {ordo_field: their_column}; see ems.portal_sync.fields.
    field_mapping = models.JSONField(default=dict, blank=True)
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "kind"], name="one_portal_endpoint_per_kind"
            )
        ]

    def __str__(self) -> str:
        return f"{self.kind} endpoint"


class SyncRun(models.Model):
    class Trigger(models.TextChoices):
        MANUAL = "manual", "Sync now"
        APPROVAL = "approval", "Approval"
        CONFIRM = "confirm", "Mass delete confirmed"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        AWAITING_APPROVAL = "awaiting_approval", "Awaiting approval"
        APPROVED = "approved", "Approved"
        SUPERSEDED = "superseded", "Superseded"
        SUCCEEDED = "succeeded", "Succeeded"
        PARTIAL = "partial", "Partial"
        HELD = "held", "Held"
        FAILED = "failed", "Failed"

    # SET_NULL: run history survives a disconnect (AC-17).
    connection = models.ForeignKey(
        PortalConnection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
    )
    trigger = models.CharField(max_length=20, choices=Trigger.choices)
    is_preview = models.BooleanField(default=False)
    # Data types a confirm lets past the mass delete hold (AC-11).
    allow_mass_delete = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RUNNING
    )
    # Per data type: created, linked, updated, deleted, skipped, held,
    # ``type_status`` and, on a preview, ``examples`` per bucket.
    counts = models.JSONField(default=dict, blank=True)
    # Fetch failure reason: never a secret or row data.
    error = models.TextField(blank=True, default="")
    started_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    job = models.OneToOneField(
        BackgroundJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sync_run",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at", "-id"]

    def __str__(self) -> str:
        return f"Sync run {self.pk} ({self.status})"


class SyncIssue(models.Model):
    class DataType(models.TextChoices):
        FACULTIES = "faculties", "Faculties"
        DEPARTMENTS = "departments", "Departments"
        CLASSES = "classes", "Classes"
        COURSES = "courses", "Courses"
        STUDENTS = "students", "Students"

    class Kind(models.TextChoices):
        BAD_ROW = "bad_row", "Bad row"
        DELETE_HELD_REFERENCED = "delete_held_referenced", "Delete held: in use"
        MASS_DELETE_HELD = "mass_delete_held", "Mass delete held"
        PLAN_LIMIT = "plan_limit", "Plan limit"
        FETCH_ERROR = "fetch_error", "Fetch error"

    class Resolution(models.TextChoices):
        DELETE = "delete", "Delete"
        KEEP = "keep", "Keep"

    run = models.ForeignKey(SyncRun, on_delete=models.CASCADE, related_name="issues")
    data_type = models.CharField(max_length=20, choices=DataType.choices)
    kind = models.CharField(max_length=30, choices=Kind.choices)
    # Portal ID, matric no, or code only: never the full row.
    row_ref = models.CharField(max_length=255, blank=True, default="")
    message = models.TextField(blank=True, default="")
    target_id = models.IntegerField(null=True, blank=True)
    resolution = models.CharField(
        max_length=10, choices=Resolution.choices, blank=True, default=""
    )
    resolved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["run", "kind"])]

    def __str__(self) -> str:
        return f"{self.kind}: {self.row_ref}"
