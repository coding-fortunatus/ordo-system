"""Applying a combined endpoint: one list of student rows that also names
each student's department, faculty, class and courses (spec 0005, *Ordo
fields per kind*).

The rows are turned into one department row per distinct department, one
class row per distinct class and one course row per class and course code,
and each goes through the per type pass in apply order, so codes, links,
clashes and holds work as they do there. The students then apply against
what those passes wrote; a department or class they refused is not created
again from a student row.

A ref names an existing record by portal ID, else by its natural key. A
record found by portal ID keeps its name: the ref may be an ID, and a
combined row has no other name to give. A course keeps its title the same
way, so only a new course is named by its code.
"""
import json
import re
from collections import defaultdict

from ems.identifiers import clean_number_text
from ems.models import Course, Department
from ems.portal_sync import codes
from ems.portal_sync.fetch import cell
from ems.portal_sync.reconcile import map_row, reconcile_students
from ems.portal_sync.refs import Refs, norm
from ems.portal_sync.structure import (
    reconcile_classes,
    reconcile_courses,
    reconcile_departments,
)


def split_codes(text):
    """The course codes in a ``course_codes`` value: a JSON list, or text
    split on commas or semicolons."""
    text = (text or "").strip()
    if text.startswith("["):
        try:
            items = json.loads(text)
        except ValueError:
            items = None
        if isinstance(items, list):
            return [c for c in (cell(i) for i in items) if c]
    return [c.strip() for c in re.split(r"[,;]", text) if c.strip()]


def _identity(*keys):
    return {k: k for k in keys}


def reconcile_combined(rows, mapping, results):
    """Apply a combined endpoint: faculties and departments, classes,
    courses and their class links, then students."""
    code_mapped = codes.is_mapped(mapping, "department_code")
    faculty_mapped = codes.is_mapped(mapping, "faculty_ref")
    faculty_code_mapped = codes.is_mapped(mapping, "faculty_code")
    links_mapped = codes.is_mapped(mapping, "course_codes")

    # A row with a code that can't be used is skipped whole (AC-18).
    usable, bad_codes = [], []
    for row in rows:
        values = map_row(row, mapping)
        error = None
        for name, mapped, label in (
            ("department_code", code_mapped, ""),
            ("faculty_code", faculty_code_mapped, "Faculty: "),
        ):
            if mapped and values.get(name):
                values[name], error = codes.clean(values[name])
                if error:
                    error = label + error
                    break
            else:
                values[name] = ""
        if error:
            matric = clean_number_text(values.get("matric_no"))
            bad_codes.append((values.get("external_id") or matric or "(no matric no)", error))
            continue
        usable.append((row, values))

    _departments(usable, code_mapped, faculty_mapped, faculty_code_mapped, results)
    if "classes" not in results.held:
        _classes(usable, results)
    if links_mapped and "courses" not in results.held:
        _courses(usable, results)

    result = results["students"]
    for ref, error in bad_codes:
        result.bad_row(ref, error)
    if "students" not in results.held:
        reconcile_students([row for row, _ in usable], mapping, results, create=False)
    return result


def _departments(usable, code_mapped, faculty_mapped, faculty_code_mapped, results):
    """One department row per distinct department ref. The first row with a
    department code sets the code, and the first with a faculty sets the
    faculty; a faculty's first code is its code."""
    faculty_codes = {}
    for _, v in usable:
        if v.get("faculty_ref") and v["faculty_code"]:
            faculty_codes.setdefault(norm(v["faculty_ref"]), v["faculty_code"])
    wanted = {}
    for _, v in usable:
        ref = v.get("department_ref")
        if not ref:
            continue
        d = wanted.setdefault(norm(ref), {"ref": ref, "code": "", "faculty_ref": ""})
        d["code"] = d["code"] or v["department_code"]
        d["faculty_ref"] = d["faculty_ref"] or v.get("faculty_ref", "")
    if not wanted:
        return

    depts = list(Department.objects.only("id", "name", "slug", "external_id"))
    by_id = {d.external_id: d for d in depts if d.external_id}
    by_code = {d.slug.upper(): d for d in depts}
    by_name = defaultdict(list)
    for d in depts:
        by_name[norm(d.name)].append(d)

    out, seen = [], set()
    for d in wanted.values():
        # The natural key is the code when the rows send one, else the name.
        # A code held under another portal ID is a clash the departments
        # pass reports, not a match.
        record, name = by_id.get(d["ref"]), d["ref"]
        if record is not None:
            name = record.name
        elif d["code"]:
            holder = by_code.get(d["code"])
            if holder is not None and holder.external_id in (None, d["code"]):
                record = holder
        elif len(by_name.get(norm(d["ref"]), [])) == 1:
            record = by_name[norm(d["ref"])][0]
        if record is not None:
            # Two refs (an ID and a name) for one department: the first wins.
            if record.pk in seen:
                continue
            seen.add(record.pk)
        out.append(
            {
                "external_id": (record.external_id if record else None) or "",
                "name": name,
                "code": d["code"],
                "faculty_ref": d["faculty_ref"],
                "faculty_code": faculty_codes.get(norm(d["faculty_ref"]), ""),
            }
        )
    keys = ["external_id", "name"]
    keys += ["code"] if code_mapped else []
    keys += ["faculty_ref"] if faculty_mapped else []
    keys += ["faculty_code"] if faculty_mapped and faculty_code_mapped else []
    reconcile_departments(out, _identity(*keys), results)


def _classes(usable, results):
    """One class row per distinct class within each department."""
    refs = Refs(results, create=False)
    out, seen = [], set()
    for _, v in usable:
        dept_ref, class_ref = v.get("department_ref"), v.get("class_ref")
        if not (dept_ref and class_ref):
            continue
        department, error = refs.department(dept_ref)
        if error:
            continue  # the student row reports it
        record = refs.class_by_id.get(class_ref)
        name = record.name if record is not None else class_ref
        if record is None:
            found = refs.class_by_name.get((department.pk, norm(class_ref)), [])
            record = found[0] if len(found) == 1 else None
        key = (department.pk, record.pk if record is not None else norm(class_ref))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "external_id": (record.external_id if record else None) or "",
                "name": name,
                "department_ref": dept_ref,
            }
        )
    if out:
        reconcile_classes(out, _identity("external_id", "name", "department_ref"), results)


def _courses(usable, results):
    """One course row per class and course code. A class's courses become
    every code its students list; a class none of whose rows lists any
    keeps its courses."""
    refs = Refs(results, create=False)
    pairs = {}  # (class portal ID, code) -> None, in first seen order
    for _, v in usable:
        listed = split_codes(v.get("course_codes"))
        if not listed:
            continue
        class_id = ""
        department, error = refs.department(v.get("department_ref", ""))
        if error is None and v.get("class_ref"):
            cls, error = refs.klass(v["class_ref"], department, v["department_ref"])
            class_id = (cls.external_id or "") if error is None else ""
        for code in listed:
            pairs.setdefault((class_id, code), None)
    if not pairs:
        return

    wanted = {code for _, code in pairs}
    by_id = {c.external_id: c for c in Course.objects.filter(external_id__in=wanted)}
    by_code = defaultdict(list)
    for c in Course.objects.filter(code__in=wanted):
        by_code[c.code].append(c)
    known = {}
    for code in wanted:
        record = by_id.get(code)
        if record is None and len(by_code[code]) == 1:
            record = by_code[code][0]
        known[code] = record
    out = []
    for class_id, code in pairs:
        record = known[code]
        out.append(
            {
                "external_id": (record.external_id if record else None) or "",
                "code": code,
                "name": record.name if record is not None else code,
                "class_ref": class_id,
            }
        )
    reconcile_courses(out, _identity("external_id", "code", "name", "class_ref"), results)
