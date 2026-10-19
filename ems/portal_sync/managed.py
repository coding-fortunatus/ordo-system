"""What the portal owns while a connection exists (spec 0005, AC-8).

A record with an ``external_id`` is portal managed: it can't be deleted by
hand, and the fields a sync writes to it are read only. Which fields those
are depends on the connection's enabled endpoints and their mappings,
because a sync writes only what some endpoint sends:

- students: every synced field (matric no, names, email, phone, class).
- departments: the name when a departments endpoint exists; the faculty
  when ``faculty_ref`` is mapped; the code when ``code`` (``departments``)
  or ``department_code`` (``combined``) is mapped (AC-18).
- faculties: the code when ``faculty_code`` is mapped beside ``faculty_ref``.
- classes: the name with a classes endpoint; the department with a classes
  or combined endpoint; the course links when ``class_ref`` (``courses``)
  or ``course_codes`` (``combined``) is mapped.
- courses: code and title with a courses endpoint (a combined fetch sends
  only the code); the exam type when ``exam_type`` is mapped.

Everything else stays Ordo owned and editable (``Class.is_active``,
``Class.visa_code``, an unmapped exam type or code). Hand made records of
a type the connection syncs can't be created, and CSV uploads of that type
are refused, so the portal stays the one source.

Field names are the API's (``class_id``, ``department_id``), so the client
can lock the same inputs the server refuses.
"""
from rest_framework import status
from rest_framework.exceptions import PermissionDenied

from ems.api.exceptions import CodedAPIException
from ems.models import Class, Course, Department, Faculty, PortalConnection, Student

DATA_TYPE = {
    Faculty: "faculties",
    Department: "departments",
    Class: "classes",
    Course: "courses",
    Student: "students",
}

_STUDENT = {"matric_no", "first_name", "last_name", "email", "phone", "class_id"}


class PortalManaged(CodedAPIException, PermissionDenied):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "This comes from the school portal. Change it there, then sync."
    default_code = "portal_managed"


def _mapped(endpoint, key):
    return bool(endpoint and (endpoint.field_mapping or {}).get(key))


class Owned:
    """The portal's share of the school's data, for one connection."""

    def __init__(self, endpoints):
        by_kind = {e.kind: e for e in endpoints if e.is_enabled}
        combined = by_kind.get("combined")
        departments = by_kind.get("departments")
        courses = by_kind.get("courses")
        # A faculty comes from the endpoint that names it.
        faculty_source = departments or combined
        faculty_mapped = _mapped(faculty_source, "faculty_ref")
        course_links = _mapped(courses, "class_ref") or _mapped(combined, "course_codes")

        self.types = {"departments", "classes", "students"}
        if faculty_mapped:
            self.types.add("faculties")
        if courses or _mapped(combined, "course_codes"):
            self.types.add("courses")

        dept = set()
        if departments:
            dept.add("name")
        if faculty_mapped:
            dept.add("faculty")
        if _mapped(departments, "code") or _mapped(combined, "department_code"):
            dept.add("slug")
        faculty = set()
        if faculty_mapped and _mapped(faculty_source, "faculty_code"):
            faculty.add("slug")
        if "faculty" in dept:
            # Moving departments between faculties sets their faculty.
            faculty.add("department_ids")
        cls = set()
        if "classes" in by_kind:
            cls.add("name")
        if "classes" in by_kind or combined:
            cls.add("department_id")
        if course_links:
            cls.add("courses")
        course = set()
        if "courses" in self.types:
            course.add("code")
        if courses:
            course.add("name")
            if _mapped(courses, "exam_type"):
                course.add("exam_type")

        self.fields = {
            Faculty: faculty,
            Department: dept,
            Class: cls,
            Course: course,
            Student: set(_STUDENT),
        }

    def locked(self, record):
        """The API fields of ``record`` only the portal may change."""
        if not record.external_id:
            return set()
        return self.fields[type(record)]


def current():
    """The ``Owned`` view of the school's connection, or None without one."""
    connection = PortalConnection.objects.prefetch_related("endpoints").first()
    if connection is None:
        return None
    return Owned(connection.endpoints.all())


def cached(context):
    """``current()``, once per serializer tree (a list shares its context)."""
    if context is None:
        return current()
    if "_portal_owned" not in context:
        context["_portal_owned"] = current()
    return context["_portal_owned"]


def locked_fields(record, context=None):
    owned = cached(context)
    return sorted(owned.locked(record)) if owned else []


def from_portal(record, context=None):
    return bool(record.external_id) and cached(context) is not None


# Checks the views and serializers call ----------------------------------


def assert_type_unsynced(data_type, action="added by hand"):
    """403 when the connection syncs ``data_type``: new records of it come
    from the portal (create views, CSV uploads)."""
    owned = current()
    if owned and data_type in owned.types:
        raise PortalManaged(
            f"{data_type.capitalize()} come from the school portal, so they can't be "
            f"{action}. Add them in the portal, then sync."
        )


def assert_deletable(record):
    if record.external_id and current() is not None:
        raise PortalManaged(
            "This record comes from the school portal. Remove it there, and the "
            "next sync deletes it here."
        )


def _value(record, field):
    if field == "class_id":
        return record.level_id
    if field == "department_id":
        return record.department_id
    if field == "faculty":
        return record.faculty_id
    return getattr(record, field)


def _incoming(attrs, field):
    """``attrs``' value for API ``field`` (validated data keys by source)."""
    source = {"class_id": "level", "department_id": "department"}.get(field, field)
    if source not in attrs:
        return False, None
    value = attrs[source]
    return True, getattr(value, "pk", value)


def assert_editable(record, attrs, context=None):
    """403 when ``attrs`` changes a field the portal owns on ``record``.
    Sending a portal owned field unchanged is fine (forms send them all)."""
    owned = cached(context)
    if not owned or not record.external_id:
        return
    for field in owned.locked(record):
        present, value = _incoming(attrs, field)
        if not present or field == "department_ids":
            continue
        current_value = _value(record, field)
        if isinstance(value, str):
            # The serializers clean what they validate (trim, upper case codes).
            current_value, value = (current_value or "").strip(), value.strip()
        if current_value != value:
            raise PortalManaged(
                f"'{field}' comes from the school portal. Change it there, then sync."
            )


def assert_faculty_departments_editable(faculty, departments, context=None):
    """403 when assigning ``departments`` to ``faculty`` would move a
    portal managed department while the portal owns department faculties."""
    owned = cached(context)
    if not owned or "faculty" not in owned.fields[Department]:
        return
    wanted = {d.pk for d in departments}
    current_ids = set(faculty.departments.values_list("pk", flat=True)) if faculty else set()
    moved = Department.objects.filter(
        pk__in=wanted ^ current_ids, external_id__isnull=False
    )
    if moved.exists():
        raise PortalManaged(
            "A department's faculty comes from the school portal. Change it there, "
            "then sync."
        )


def assert_links_editable(cls):
    """403 when the portal owns this class's course list."""
    owned = current()
    if owned and "courses" in owned.locked(cls):
        raise PortalManaged(
            "This class's courses come from the school portal. Change them there, "
            "then sync."
        )
