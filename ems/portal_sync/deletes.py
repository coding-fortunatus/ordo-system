"""Removing records the portal no longer sends (spec 0005, AC-10 to AC-12).

Only a type whose own endpoint was fetched completely can lose records:
a portal owned record (``external_id`` set) that no fetched row names is
"gone". A row names a record by its portal ID, or by its natural key when
the row has no portal ID, bad rows included, so a skipped row's record is
always present (AC-9).

Before anything applies, a type losing more than
``PORTAL_SYNC_MASS_DELETE_PCT`` of its portal owned records is held:
nothing applies for it until an admin confirms (AC-11). After every type
applied, the gone records are deleted in reverse apply order, except those
a delete would cascade into exam data or into records that stay; each of
those is a ``delete_held_referenced`` issue an admin resolves (AC-10).
"""
from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction

from ems.identifiers import clean_number_text
from ems.models import (
    Class,
    Course,
    Department,
    Faculty,
    Invite,
    SeatArrangement,
    Student,
    SyncIssue,
    TimeTable,
    User,
)
from ems.portal_sync import codes
from ems.portal_sync.combined import split_codes
from ems.portal_sync.reconcile import map_row
from ems.portal_sync.refs import class_portal_id, norm

MODELS = {
    "faculties": Faculty,
    "departments": Department,
    "classes": Class,
    "courses": Course,
    "students": Student,
}

# Deletes run in reverse apply order: a department can only go once its
# classes and students have.
_ORDER = ("students", "courses", "classes", "departments", "faculties")

_NOUNS = {
    "faculties": "faculty",
    "departments": "department",
    "classes": "class",
    "courses": "course",
    "students": "student",
}


def ref(record):
    """How an issue or example names a record: never row content."""
    if isinstance(record, Student):
        return record.matric_no
    if isinstance(record, Course):
        return record.code
    return record.name


@dataclass
class Plan:
    data_type: str
    owned: int  # portal owned records before the run
    gone: list = field(default_factory=list)


@dataclass
class _Seen:
    """What a fetch names: portal IDs, and natural keys from rows that
    carry no portal ID."""

    ids: set = field(default_factory=set)
    keys: set = field(default_factory=set)
    names: set = field(default_factory=set)

    def row(self, portal_id, natural_id, *keys, names=()):
        if portal_id:
            self.ids.add(portal_id)
            return
        if natural_id:
            self.ids.add(natural_id)
        self.keys.update(k for k in keys if k)
        self.names.update(norm(n) for n in names if n)


def _code(value):
    code, error = codes.clean(value) if value else ("", None)
    return "" if error else code


def _named(seen, ref, code):
    """Note a department or faculty a row names by ``ref`` (a portal ID or
    a name) and maybe a code. Its portal ID is its code, else its ref."""
    if ref:
        seen.ids.update(x for x in (ref, code) if x)
        seen.keys.update([code] if code else [])
        seen.names.add(norm(ref))


def _by_code_or_name(seen):
    return lambda r: r.slug.upper() in seen.keys or norm(r.name) in seen.names


def _in_named_department(seen):
    """A class is present when a row names it within its department, by
    the department's portal ID or name (a ref resolves by either)."""
    names = {
        d.pk: {norm(d.name), norm(d.external_id)}
        for d in Department.objects.only("id", "name", "external_id")
    }
    return lambda c: any(
        (dept, norm(c.name)) in seen.keys for dept in names.get(c.department_id, ())
    )


def _seen(kind, rows, mapping):
    """``{data_type: (seen, present)}`` for what ``kind``'s rows name;
    ``present(record)`` says whether a record is still sent."""
    out = {}
    if kind == "students":
        seen = _Seen()
        for row in rows:
            v = map_row(row, mapping)
            matric = clean_number_text(v.get("matric_no"))
            seen.row(v.get("external_id"), matric, matric)
        out["students"] = (seen, lambda s: s.matric_no in seen.keys)
    elif kind == "courses":
        seen = _Seen()
        for row in rows:
            v = map_row(row, mapping)
            seen.row(v.get("external_id"), v.get("code"), v.get("code"))
        out["courses"] = (seen, lambda c: c.code in seen.keys)
    elif kind == "classes":
        seen = _Seen()
        for row in rows:
            v = map_row(row, mapping)
            name, dept = v.get("name", ""), v.get("department_ref", "")
            natural = class_portal_id(dept, name) if name and dept else ""
            seen.row(v.get("external_id"), natural, names=[name])
        # Its department may be named by portal ID or name: the class name
        # alone keeps it, the safe side.
        out["classes"] = (seen, lambda c: norm(c.name) in seen.names)
    elif kind == "departments":
        seen = _Seen()
        code_mapped = codes.is_mapped(mapping, "code")
        faculties = _Seen()
        for row in rows:
            v = map_row(row, mapping)
            name = v.get("name", "")
            code = _code(v.get("code")) if code_mapped else ""
            seen.row(v.get("external_id"), code or name, code, names=[name])
            _named(faculties, v.get("faculty_ref", ""), _code(v.get("faculty_code")))
        out["departments"] = (seen, _by_code_or_name(seen))
        if codes.is_mapped(mapping, "faculty_ref"):
            out["faculties"] = (faculties, _by_code_or_name(faculties))
    elif kind == "combined":
        # One fetch names every type: each student's department, faculty,
        # class and courses are refs, by portal ID or by name.
        students, depts, faculties, classes, courses = (_Seen() for _ in range(5))
        code_mapped = codes.is_mapped(mapping, "department_code")
        for row in rows:
            v = map_row(row, mapping)
            matric = clean_number_text(v.get("matric_no"))
            students.row(v.get("external_id"), matric, matric)
            dept = v.get("department_ref", "")
            _named(depts, dept, _code(v.get("department_code")) if code_mapped else "")
            _named(faculties, v.get("faculty_ref", ""), _code(v.get("faculty_code")))
            if cls := v.get("class_ref", ""):
                classes.ids.update([cls, class_portal_id(dept, cls)] if dept else [cls])
                classes.keys.add((norm(dept), norm(cls)))
            listed = split_codes(v.get("course_codes"))
            courses.ids.update(listed)
            courses.keys.update(listed)
        out["students"] = (students, lambda s: s.matric_no in students.keys)
        out["departments"] = (depts, _by_code_or_name(depts))
        out["classes"] = (classes, _in_named_department(classes))
        if codes.is_mapped(mapping, "faculty_ref"):
            out["faculties"] = (faculties, _by_code_or_name(faculties))
        if codes.is_mapped(mapping, "course_codes"):
            out["courses"] = (courses, lambda c: c.code in courses.keys)
    return out


def plan(fetched):
    """``{data_type: Plan}`` for every type ``fetched`` (``{kind: (rows,
    endpoint)}``) covers completely."""
    plans = {}
    for kind, (rows, endpoint) in fetched.items():
        for data_type, (seen, present) in _seen(kind, rows, endpoint.field_mapping).items():
            owned = list(MODELS[data_type].objects.filter(external_id__isnull=False))
            gone = [r for r in owned if r.external_id not in seen.ids and not present(r)]
            plans[data_type] = Plan(data_type, len(owned), gone)
    return plans


def hold_mass_deletes(plans, allowed, results):
    """Hold every type that would lose too many of its portal owned
    records, unless a confirm allowed it (AC-11). Records each type's gone
    pks in ``results.gone``."""
    threshold = settings.PORTAL_SYNC_MASS_DELETE_PCT
    for p in plans.values():
        results.gone[p.data_type] = {r.pk for r in p.gone}
        n = len(p.gone)
        if not p.owned or n * 100 <= threshold * p.owned or p.data_type in allowed:
            continue
        results.held.add(p.data_type)
        result = results[p.data_type]
        result.status = "held"
        result.add("held", [ref(r) for r in p.gone])
        result.issue(
            SyncIssue.Kind.MASS_DELETE_HELD,
            "",
            f"This sync would delete {n:,} of {p.owned:,} portal {p.data_type} "
            f"({n * 100 / p.owned:.0f}%), more than the {threshold}% allowed. "
            f"Nothing was applied for {p.data_type}. Check the portal, then "
            "confirm if these deletes are right.",
        )


def _in_use(data_type, pks):
    """``{pk: reason}`` for the records whose delete would cascade into
    exam data or into records that stay."""
    checks = {
        "students": [(SeatArrangement.objects, "student", "has exam seats")],
        "courses": [
            (TimeTable.objects, "course", "is on the timetable"),
            (SeatArrangement.objects, "course", "has exam seats"),
        ],
        "classes": [
            (TimeTable.objects, "class_obj", "is on the timetable"),
            (SeatArrangement.objects, "cls", "has exam seats"),
            (Student.objects, "level", "still has students"),
        ],
        "departments": [
            (Class.objects, "department", "still has classes"),
            (Student.objects, "department", "still has students"),
            (User.all_objects, "department", "still has staff accounts"),
            (Invite.objects, "department", "still has pending invites"),
        ],
        "faculties": [(Department.objects, "faculty", "still has departments")],
    }[data_type]
    reasons = {}
    for manager, fk, reason in checks:
        for pk in manager.filter(**{f"{fk}_id__in": pks}).values_list(f"{fk}_id", flat=True):
            reasons.setdefault(pk, reason)
    return reasons


def apply(plans, results):
    """Delete each applied type's gone records, newest dependants first."""
    for data_type in _ORDER:
        p = plans.get(data_type)
        if p is None or not p.gone or results[data_type].status == "held":
            continue
        with transaction.atomic():
            _delete(data_type, [r.pk for r in p.gone], results[data_type])


def _delete(data_type, pks, result):
    model = MODELS[data_type]
    # Re-read: still portal owned, and still there.
    records = list(model.objects.filter(pk__in=pks, external_id__isnull=False))
    in_use = _in_use(data_type, [r.pk for r in records])
    noun = _NOUNS[data_type]
    doomed = []
    for record in records:
        reason = in_use.get(record.pk)
        if reason is None:
            doomed.append(record)
            continue
        result.add("held", [ref(record)])
        result.issue(
            SyncIssue.Kind.DELETE_HELD_REFERENCED,
            ref(record),
            f"The portal no longer sends this {noun}, but it {reason}, so it "
            "wasn't deleted. Delete it anyway, or keep it as an Ordo record.",
            target_id=record.pk,
        )
    if not doomed:
        return
    model.objects.filter(pk__in=[r.pk for r in doomed]).delete()
    result.add("deleted", [ref(r) for r in doomed])
    if data_type == "students":
        Class.objects.sync_student_counts({r.level_id for r in doomed})


def resolve(issue, resolution):
    """Act on a held delete (AC-10): ``delete`` removes the record now, with
    its cascade; ``keep`` clears its portal ID so later syncs ignore it."""
    model = MODELS[issue.data_type]
    record = model.objects.filter(pk=issue.target_id).first()
    if record is None:
        return
    if resolution == SyncIssue.Resolution.KEEP:
        model.objects.filter(pk=record.pk).update(external_id=None)
        return
    level_id = getattr(record, "level_id", None)
    record.delete()
    if level_id:
        Class.objects.sync_student_counts({level_id})
