"""The Ordo fields each endpoint kind maps (spec 0005, *Ordo fields per kind*).

A ``ref`` field accepts a portal ID or a name; see ``reconcile`` for the
order they resolve in.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    required: bool = False


_STUDENT_FIELDS = (
    Field("external_id", "Portal ID"),
    Field("matric_no", "Matric number", required=True),
    Field("first_name", "First name", required=True),
    Field("last_name", "Last name", required=True),
    Field("email", "Email"),
    Field("phone", "Phone"),
    Field("department_ref", "Department", required=True),
    Field("class_ref", "Class", required=True),
)

FIELDS = {
    "departments": (
        Field("external_id", "Portal ID"),
        Field("name", "Name", required=True),
        Field("code", "Department code"),
        Field("faculty_ref", "Faculty"),
        Field("faculty_code", "Faculty code"),
    ),
    "classes": (
        Field("external_id", "Portal ID"),
        Field("name", "Name", required=True),
        Field("department_ref", "Department", required=True),
    ),
    "courses": (
        Field("external_id", "Portal ID"),
        Field("code", "Course code", required=True),
        Field("name", "Course title", required=True),
        Field("exam_type", "Exam type (PBE or CBE)"),
        Field("class_ref", "Class"),
    ),
    "students": _STUDENT_FIELDS,
    "combined": _STUDENT_FIELDS + (
        Field("department_code", "Department code"),
        Field("faculty_ref", "Faculty"),
        Field("faculty_code", "Faculty code"),
        Field("course_codes", "Course codes"),
    ),
}


def keys(kind):
    return {f.key for f in FIELDS[kind]}


def required_keys(kind):
    return [f.key for f in FIELDS[kind] if f.required]


def unmapped_required(kind, mapping):
    """Required fields of ``kind`` with no column in ``mapping``."""
    return [k for k in required_keys(kind) if not (mapping or {}).get(k)]


def as_dict():
    """The list the ``fields/`` endpoint serves."""
    return {
        kind: [
            {"key": f.key, "label": f.label, "required": f.required}
            for f in fields
        ]
        for kind, fields in FIELDS.items()
    }
