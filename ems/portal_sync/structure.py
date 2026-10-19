"""Applying the departments, classes and courses endpoints (spec 0005,
*Matching* and *Department and faculty codes*).

Each pass maps and checks every row first, then matches and writes in one
transaction. A row matches by portal ID, then links to an unlinked record
with the same natural key, then creates one. Faculties come from the
department rows that name them and apply in their own transaction first.

Codes: a mapped code with a value is portal owned and kept in step; any
other new record gets a code derived from its name once. A code two records
claim in one fetch, or that another record holds and keeps, makes the row a
``bad_row``. Codes that change are first parked on ``_TMP<pk>`` so a swap
never trips the unique constraint.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from django.db import transaction

from ems.models import Class, Course, Department, Faculty, GenerationConstraints
from ems.portal_sync import codes
from ems.portal_sync.reconcile import map_row
from ems.portal_sync.refs import Refs, class_portal_id, norm

EXAM_TYPES = ("PBE", "CBE")


def _limit(model, name):
    return model._meta.get_field(name).max_length


def _too_long(model, values):
    """A message for the first value longer than its column, or None."""
    for name, value in values.items():
        limit = _limit(model, name)
        if value and limit and len(value) > limit:
            return f"{name} is longer than {limit} characters."
    return None


def _drop_duplicates(candidates, result, key):
    """Skip every row whose portal ID another row of the fetch also sends."""
    seen = Counter(key(c) for c in candidates)
    for portal_id, n in seen.items():
        if n > 1:
            result.bad_row(portal_id, f"The portal sent {n} rows with this portal ID.", n=n)
    return [c for c in candidates if seen[key(c)] == 1]


@dataclass(eq=False)
class _Plan:
    """What one record becomes: a saved record with the fields to change,
    or a new unsaved one."""

    record: object
    rows: list = field(default_factory=list)
    code: str = ""  # a mapped code with a value; blank leaves the slug alone
    linked: bool = False
    changes: dict = field(default_factory=dict)

    def set(self, name, value):
        if self.record.pk is None:
            setattr(self.record, name, value)
        elif getattr(self.record, name) != value:
            self.changes[name] = value

    @property
    def moves_code(self):
        return bool(self.record.pk and self.code and self.record.slug != self.code)


def _code_clashes(plans, holders, noun):
    """``{plan: message}`` for plans whose code can't be applied: two plans
    claim it, or a record keeping it already holds it. Dropping a plan can
    stop its record moving off a code, so this runs until nothing changes."""
    bad = {}
    by_code = defaultdict(list)
    for p in plans:
        if p.code:
            by_code[p.code].append(p)
    for code, group in by_code.items():
        if len(group) > 1:
            for p in group:
                bad[p] = f"Code '{code}' is sent for more than one {noun}."
    while True:
        live = [p for p in plans if p not in bad]
        moving = {p.record.pk for p in live if p.moves_code}
        found = {}
        for p in live:
            holder = holders.get(p.code) if p.code else None
            if holder is None or holder.pk == p.record.pk or holder.pk in moving:
                continue
            found[p] = f"Code '{p.code}' already belongs to {noun} '{holder.name}' in Ordo."
        if not found:
            return bad
        bad.update(found)


def _assign_codes(plans, holders, fallback):
    """Give new records their code: the mapped one, else one derived from
    the name that no record has or this run claims."""
    taken = set(holders) | {p.code for p in plans if p.code}
    for p in plans:
        if p.record.pk is None:
            p.record.slug = p.code or codes.derive(p.record.name, fallback, taken)
        elif p.code:
            p.set("slug", p.code)


def _write(model, plans, result):
    """Create and update ``plans`` in the open transaction. Returns
    ``{pk: (old slug, new slug)}`` for records whose code changed."""
    existing = [p for p in plans if p.record.pk and (p.linked or p.changes)]
    moving = [p for p in existing if "slug" in p.changes]
    renamed = {p.record.pk: (p.record.slug, p.changes["slug"]) for p in moving}
    if moving:
        for p in moving:
            p.record.slug = f"_TMP{p.record.pk}"
        model.objects.bulk_update([p.record for p in moving], ["slug"])
    new = [p.record for p in plans if p.record.pk is None]
    if new:
        model.objects.bulk_create(new)
    fields = {"external_id"} if any(p.linked for p in existing) else set()
    for p in existing:
        for name, value in p.changes.items():
            setattr(p.record, name, value)
            fields.add(name)
    if existing and fields:
        model.objects.bulk_update([p.record for p in existing], sorted(fields))
    result.add("created", [_ref(r) for r in new])
    result.add("linked", [_ref(p.record) for p in existing if p.linked])
    result.add("updated", [_ref(p.record) for p in existing if not p.linked])
    return renamed


def _ref(record):
    """A preview example: a course by its code, anything else by name."""
    return record.code if isinstance(record, Course) else record.name


def _move_cbe_groups(renamed):
    """Keep each renamed faculty's CBE group under its new code."""
    if not renamed:
        return
    constraints = GenerationConstraints.objects.select_for_update().first()
    if constraints is None:
        return
    groups = dict(constraints.cbe_faculty_groups or {})
    moved = {new: groups.pop(old) for old, new in renamed.values() if old in groups}
    if moved:
        constraints.cbe_faculty_groups = {**groups, **moved}
        constraints.save(update_fields=["cbe_faculty_groups"])


# Departments, and the faculties they name ------------------------------


@dataclass(eq=False)
class _DeptRow:
    ref: str
    portal_id: str
    values: dict
    code: str
    faculty_code: str
    faculty: _Plan = None


class _Faculties:
    """The faculties department rows name, by portal ID, else code, else
    name; one plan per faculty."""

    def __init__(self, results):
        self.results = results
        self.all = list(Faculty.objects.all())
        self.by_id = {f.external_id: f for f in self.all if f.external_id}
        self.by_code = {f.slug.upper(): f for f in self.all}
        self.by_name = defaultdict(list)
        for f in self.all:
            self.by_name[norm(f.name)].append(f)
        self.plans = {}  # record pk, or the normalised name of a new one
        self.claimed_ids = set(self.by_id)

    def plan(self, ref, code):
        """``(plan, error)`` for the faculty ``ref`` names."""
        record = self.by_id.get(ref) or (self.by_code.get(code) if code else None)
        if record is None:
            found = self.by_name.get(norm(ref), [])
            if len(found) > 1:
                return None, f"More than one faculty is named '{ref}'."
            record = found[0] if found else None
        # A mass delete holds faculties: none may be created, and departments
        # can't name one the portal dropped (AC-11).
        if "faculties" in self.results.held:
            if record is None:
                return None, f"No faculty '{ref}' in Ordo, and faculties are held."
            if self.results.blocks("faculties", record):
                return None, f"Faculty '{ref}' is missing from the portal, and faculties are held."
        key = record.pk if record else ("new", norm(ref))
        plan = self.plans.get(key)
        if plan is None:
            # Its portal ID is its natural key: the code, else the name.
            portal_id = code or ref
            if record is None:
                if error := _too_long(Faculty, {"name": ref, "external_id": portal_id}):
                    return None, f"Faculty: {error}"
                if portal_id in self.claimed_ids:
                    return None, f"Faculty portal ID '{portal_id}' already belongs to another faculty."
                record = Faculty(name=ref, external_id=portal_id)
                self.claimed_ids.add(portal_id)
                plan = _Plan(record)
            elif record.external_id is None:
                if portal_id in self.claimed_ids:
                    return None, f"Faculty portal ID '{portal_id}' already belongs to another faculty."
                record.external_id = portal_id
                self.claimed_ids.add(portal_id)
                plan = _Plan(record, linked=True)
            else:
                plan = _Plan(record)
            self.plans[key] = plan
        # The first row naming a faculty with a code sets it.
        if code and not plan.code:
            plan.code = code
        return plan, None


def reconcile_departments(rows, mapping, results):
    """Apply the departments endpoint: its faculties first, then the
    departments, each in its own transaction."""
    result = results["departments"]
    code_mapped = codes.is_mapped(mapping, "code")
    faculty_mapped = codes.is_mapped(mapping, "faculty_ref")
    faculty_code_mapped = codes.is_mapped(mapping, "faculty_code")

    candidates = []
    for row in rows:
        values = map_row(row, mapping)
        name = values.get("name", "")
        code, error = "", None
        if code_mapped and values.get("code"):
            code, error = codes.clean(values["code"])
        portal_id = values.get("external_id") or code or name
        ref = portal_id or "(no name)"
        if not name:
            result.bad_row(ref, "Blank required value: name.")
            continue
        if error:
            result.bad_row(ref, error)
            continue
        faculty_code = ""
        if faculty_code_mapped and values.get("faculty_code"):
            faculty_code, error = codes.clean(values["faculty_code"])
            if error:
                result.bad_row(ref, f"Faculty: {error}")
                continue
        if error := _too_long(Department, {"name": name, "external_id": portal_id}):
            result.bad_row(ref, error)
            continue
        candidates.append(_DeptRow(ref, portal_id, values, code, faculty_code))
    candidates = _drop_duplicates(candidates, result, key=lambda c: c.portal_id)

    # Faculties: one plan each, with code clashes settled before departments
    # are matched, so a department never points at a faculty left unwritten.
    faculties = _Faculties(results)
    kept = []
    for c in candidates:
        faculty_ref = c.values.get("faculty_ref", "")
        if faculty_mapped and faculty_ref:
            c.faculty, error = faculties.plan(faculty_ref, c.faculty_code)
            if error:
                result.bad_row(c.ref, error)
                continue
            c.faculty.rows.append(c)
        kept.append(c)
    faculty_plans = list(faculties.plans.values())
    bad = _code_clashes(faculty_plans, faculties.by_code, "faculty")
    for plan, message in bad.items():
        for c in plan.rows:
            result.bad_row(c.ref, f"Faculty: {message}")
    candidates = [c for c in kept if c.faculty not in bad]
    faculty_plans = [p for p in faculty_plans if p not in bad and p.rows]
    _assign_codes(faculty_plans, faculties.by_code, "FAC")

    # Departments: match each row to a record.
    depts = list(Department.objects.select_related("faculty"))
    by_id = {d.external_id: d for d in depts if d.external_id}
    by_code = {d.slug.upper(): d for d in depts}
    unlinked_by_name = defaultdict(list)
    for d in depts:
        if d.external_id is None:
            unlinked_by_name[norm(d.name)].append(d)
    taken = set()
    plans = []
    for c in candidates:
        record, linked = by_id.get(c.portal_id), False
        if record is None and c.code:
            holder = by_code.get(c.code)
            if holder is not None and holder.external_id is None and holder.pk not in taken:
                record, linked = holder, True
        elif record is None:
            matches = [d for d in unlinked_by_name.get(norm(c.values["name"]), []) if d.pk not in taken]
            if len(matches) > 1:
                result.bad_row(
                    c.ref,
                    f"Ambiguous match: {len(matches)} departments are already named "
                    f"'{c.values['name']}'.",
                )
                continue
            if matches:
                record, linked = matches[0], True
        if record is None:
            plan = _Plan(Department(name=c.values["name"], external_id=c.portal_id))
        else:
            taken.add(record.pk)
            if linked:
                record.external_id = c.portal_id
            plan = _Plan(record, linked=linked)
        plan.rows, plan.code = [c], c.code
        plan.set("name", c.values["name"])
        if faculty_mapped:
            plan.set("faculty", c.faculty.record if c.faculty else None)
        plans.append(plan)
    bad = _code_clashes(plans, by_code, "department")
    for plan, message in bad.items():
        result.bad_row(plan.rows[0].ref, message)
    plans = [p for p in plans if p not in bad]
    _assign_codes(plans, by_code, "DEPT")

    # A type a mass delete holds applies nothing; the other one still does.
    if faculty_plans and "faculties" not in results.held:
        with transaction.atomic():
            _move_cbe_groups(_write(Faculty, faculty_plans, results["faculties"]))
    if "departments" not in results.held:
        with transaction.atomic():
            # bulk writes pick up the pks the new faculties just got.
            _write(Department, plans, result)
    return result


# Classes ---------------------------------------------------------------


def reconcile_classes(rows, mapping, results):
    """Apply the classes endpoint, creating the departments it names that
    Ordo lacks."""
    result = results["classes"]
    refs = Refs(results)
    candidates = []
    for row in rows:
        values = map_row(row, mapping)
        name, dept_ref = values.get("name", ""), values.get("department_ref", "")
        portal_id = values.get("external_id") or (
            class_portal_id(dept_ref, name) if name and dept_ref else ""
        )
        ref = portal_id or name or "(no name)"
        blank = [k for k in ("name", "department_ref") if not values.get(k)]
        if blank:
            result.bad_row(ref, f"Blank required value: {', '.join(blank)}.")
            continue
        if error := _too_long(Class, {"name": name, "external_id": portal_id}):
            result.bad_row(ref, error)
            continue
        department, error = refs.department(dept_ref)
        if error:
            result.bad_row(ref, error)
            continue
        candidates.append((ref, portal_id, name, department))
    candidates = _drop_duplicates(candidates, result, key=lambda c: c[1])

    with transaction.atomic():
        by_id = {
            c.external_id: c
            for c in Class.objects.filter(external_id__in=[c[1] for c in candidates])
        }
        unlinked = defaultdict(list)
        for cls in Class.objects.filter(external_id__isnull=True):
            unlinked[(cls.department_id, norm(cls.name))].append(cls)
        plans = []
        for ref, portal_id, name, department in candidates:
            record, linked = by_id.get(portal_id), False
            if record is None and department.pk:
                matches = unlinked.get((department.pk, norm(name)), [])
                if len(matches) > 1:
                    result.bad_row(
                        ref,
                        f"Ambiguous match: {len(matches)} classes named '{name}' in "
                        f"department '{department.name}'.",
                    )
                    continue
                if matches:
                    # Taken: a second row can't link it too.
                    record, linked = matches.pop(), True
                    record.external_id = portal_id
            if record is None:
                plan = _Plan(Class(name=name, department=department, external_id=portal_id))
            else:
                plan = _Plan(record, linked=linked)
                plan.set("name", name)
                if record.department_id != department.pk:
                    plan.changes["department"] = department
            plans.append(plan)
        refs.save_new()
        _write(Class, plans, result)
    return result


# Courses ---------------------------------------------------------------


class _Classes:
    """Classes a course row names: by portal ID, else by name. A course row
    has no department, so a name must be unique in the school."""

    def __init__(self, results):
        self.results = results
        self.by_id = {}
        self.by_name = defaultdict(list)
        for cls in Class.objects.select_related("department"):
            if cls.external_id:
                self.by_id[cls.external_id] = cls
            self.by_name[norm(cls.name)].append(cls)

    def find(self, ref):
        found = [self.by_id[ref]] if ref in self.by_id else self.by_name.get(norm(ref), [])
        if len(found) > 1:
            return None, (
                f"Class '{ref}' is ambiguous: {len(found)} departments have it. "
                "Send the class's portal ID instead."
            )
        if not found:
            return None, f"No class '{ref}' in Ordo."
        if self.results.blocks("classes", found[0]):
            return None, f"Class '{ref}' is missing from the portal, and classes are held."
        return found[0], None


def reconcile_courses(rows, mapping, results):
    """Apply the courses endpoint. A course may repeat, one row per class
    it belongs to; its first row sets its name and exam type. A class's
    course list is replaced by what the portal lists for it."""
    result = results["courses"]
    exam_mapped = codes.is_mapped(mapping, "exam_type")
    links_mapped = codes.is_mapped(mapping, "class_ref")
    classes = _Classes(results)
    groups = defaultdict(list)  # portal ID -> [(ref, values, class or None)]
    # A class with a skipped row keeps the links that row would have listed.
    partial = set()
    for row in rows:
        values = map_row(row, mapping)
        code, name = values.get("code", ""), values.get("name", "")
        portal_id = values.get("external_id") or code
        ref = portal_id or "(no code)"
        cls, error = None, None
        if links_mapped and values.get("class_ref"):
            cls, error = classes.find(values["class_ref"])
        blank = [k for k in ("code", "name") if not values.get(k)]
        if blank:
            error = f"Blank required value: {', '.join(blank)}."
        elif bad_length := _too_long(Course, {"code": code, "name": name, "external_id": portal_id}):
            error = bad_length
        elif exam_mapped and values.get("exam_type"):
            values["exam_type"] = values["exam_type"].upper()
            if values["exam_type"] not in EXAM_TYPES:
                error = f"Exam type '{values['exam_type']}' isn't PBE or CBE."
        if error:
            if cls is not None:
                partial.add(cls.pk)
            result.bad_row(ref, error)
            continue
        groups[portal_id].append((ref, values, cls))

    # Rows of one course must agree on its code.
    for portal_id, group in list(groups.items()):
        if len({v["code"] for _, v, _ in group}) > 1:
            result.bad_row(
                portal_id,
                "The portal sent different course codes for this portal ID.",
                n=len(group),
            )
            partial.update(c.pk for _, _, c in group if c is not None)
            del groups[portal_id]

    with transaction.atomic():
        by_id = {c.external_id: c for c in Course.objects.filter(external_id__in=list(groups))}
        unlinked = defaultdict(list)
        for course in Course.objects.filter(
            external_id__isnull=True, code__in=[g[0][1]["code"] for g in groups.values()]
        ):
            unlinked[course.code].append(course)
        plans = []
        links = defaultdict(list)  # class -> [plan]
        for portal_id, group in groups.items():
            ref, first, _ = group[0]
            record, linked = by_id.get(portal_id), False
            if record is None:
                matches = unlinked.get(first["code"], [])
                if len(matches) > 1:
                    result.bad_row(
                        ref,
                        f"Ambiguous match: {len(matches)} courses already have code "
                        f"{first['code']}.",
                        n=len(group),
                    )
                    partial.update(c.pk for _, _, c in group if c is not None)
                    continue
                if matches:
                    record, linked = matches.pop(), True
                    record.external_id = portal_id
            if record is None:
                plan = _Plan(
                    Course(
                        code=first["code"],
                        name=first["name"],
                        exam_type=first.get("exam_type") or "PBE",
                        external_id=portal_id,
                    )
                )
            else:
                plan = _Plan(record, linked=linked)
                plan.set("code", first["code"])
                plan.set("name", first["name"])
                if exam_mapped and first.get("exam_type"):
                    plan.set("exam_type", first["exam_type"])
            plans.append(plan)
            for _, _, cls in group:
                if cls is not None:
                    links[cls].append(plan)
        _write(Course, plans, result)
        _replace_links(links, partial)
    return result


def _replace_links(links, partial):
    """Set each listed class's courses to what the portal lists. Only links
    to portal owned courses are removed, and none from a class one of whose
    rows was skipped."""
    Through = Class.courses.through
    class_ids = {cls.pk for cls in links}
    current = defaultdict(set)
    for class_id, course_id in Through.objects.filter(class_id__in=class_ids).values_list(
        "class_id", "course_id"
    ):
        current[class_id].add(course_id)
    portal_owned = set(
        Course.objects.filter(
            pk__in={cid for ids in current.values() for cid in ids},
            external_id__isnull=False,
        ).values_list("pk", flat=True)
    )
    add = []
    for cls, plans in links.items():
        wanted = {p.record.pk for p in plans}
        have = current[cls.pk]
        add += [Through(class_id=cls.pk, course_id=cid) for cid in wanted - have]
        dropped = (have - wanted) & portal_owned
        if dropped and cls.pk not in partial:
            Through.objects.filter(class_id=cls.pk, course_id__in=dropped).delete()
    if add:
        Through.objects.bulk_create(add, ignore_conflicts=True)
