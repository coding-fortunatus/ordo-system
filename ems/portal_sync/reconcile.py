"""Applying fetched rows to the school's records (spec 0005, *Matching*).

A row matches a record by portal ID (``external_id``). Failing that, it links
to an unlinked record with the same natural key, and failing that it creates
one. Only records with an ``external_id`` are ever changed by a later sync,
so records the school added by hand are never touched until a row links them.

A row that can't be used is skipped and reported as a ``bad_row``; its
record counts as present. Issues store references only (portal ID, matric
no), never row content.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from django.db import transaction

from ems.identifiers import clean_number_text
from ems.models import Class, Student, SyncIssue
from ems.portal_sync.fetch import cell
from ems.portal_sync.refs import Refs
from tenancy import limits

BUCKETS = ("created", "linked", "updated", "deleted", "skipped", "held")

# A preview keeps this many references per bucket (AC-4).
EXAMPLES = 50

_STUDENT_FIELDS = ("first_name", "last_name", "email", "phone", "department", "level")


def _limit(model, name):
    return model._meta.get_field(name).max_length


@dataclass
class TypeResult:
    """What one data type's pass did, and the issues it raised."""

    data_type: str
    counts: Counter = field(default_factory=Counter)
    examples: dict = field(default_factory=lambda: defaultdict(list))
    issues: list = field(default_factory=list)
    status: str = "applied"

    def add(self, bucket, refs, n=None):
        """Count ``n`` records (default: one per ref) into ``bucket`` and
        keep the first references as examples. A ref is a matric no, course
        code, or a name, never row content."""
        refs = list(refs)
        self.counts[bucket] += len(refs) if n is None else n
        kept = self.examples[bucket]
        kept.extend(str(r) for r in refs[: EXAMPLES - len(kept)])

    def clear(self, *buckets):
        for bucket in buckets:
            self.counts[bucket] = 0
            self.examples.pop(bucket, None)

    def issue(self, kind, row_ref, message, target_id=None):
        self.issues.append(
            SyncIssue(
                data_type=self.data_type,
                kind=kind,
                row_ref=(row_ref or "")[:255],
                message=message,
                target_id=target_id,
            )
        )

    def bad_row(self, row_ref, message, n=1):
        self.add("skipped", [row_ref], n=n)
        self.issue(SyncIssue.Kind.BAD_ROW, row_ref, message)

    def as_counts(self, *, examples=False):
        counts = {**{b: self.counts[b] for b in BUCKETS}, "type_status": self.status}
        if examples:
            counts["examples"] = {b: list(self.examples[b]) for b in BUCKETS if self.examples[b]}
        return counts


class Results(dict):
    """One ``TypeResult`` per data type a run touched, in apply order. A
    type appears the first time anything reads it.

    ``held`` names the types a mass delete holds before anything applies
    (AC-11); ``gone`` maps each fetched type to the pks of its portal owned
    records the fetch no longer has."""

    def __init__(self):
        super().__init__()
        self.held = set()
        self.gone = {}

    def blocks(self, data_type, record):
        """True when ``record`` is missing from a held type: rows naming it
        become bad rows (AC-11)."""
        return data_type in self.held and record.pk in self.gone.get(data_type, ())

    def __missing__(self, data_type):
        self[data_type] = TypeResult(data_type)
        return self[data_type]


def map_row(row, mapping):
    """``{ordo_field: text}`` for one fetched row, through the saved mapping."""
    return {key: cell(row.get(column)) for key, column in mapping.items()}


def _too_long(values, limits_by_field):
    for name, limit in limits_by_field.items():
        if limit and len(values.get(name, "")) > limit:
            return name, limit
    return None


def reconcile_students(rows, mapping, results, *, create=True):
    """Apply the fetched student rows in one transaction, creating the
    departments and classes they name that Ordo lacks (unless not
    ``create``). Nothing is written, those included, when the plan's student
    limit would be passed."""
    result = results["students"]
    refs = Refs(results, create=create)
    lengths = {
        "matric_no": _limit(Student, "matric_no"),
        "first_name": _limit(Student, "first_name"),
        "last_name": _limit(Student, "last_name"),
        "email": _limit(Student, "email"),
        "phone": _limit(Student, "phone"),
        "external_id": _limit(Student, "external_id"),
    }

    # Map and validate every row first, so duplicates are known before any
    # row is matched.
    candidates = []
    for row in rows:
        values = map_row(row, mapping)
        values["matric_no"] = clean_number_text(values.get("matric_no"))
        values["phone"] = clean_number_text(values.get("phone"))
        portal_id = values.get("external_id") or values["matric_no"]
        ref = portal_id or "(no matric no)"
        blank = [
            k
            for k in ("matric_no", "first_name", "last_name", "department_ref", "class_ref")
            if not values.get(k)
        ]
        if blank:
            result.bad_row(ref, f"Blank required value: {', '.join(blank)}.")
            continue
        values["external_id"] = portal_id
        if over := _too_long(values, lengths):
            result.bad_row(ref, f"{over[0]} is longer than {over[1]} characters.")
            continue
        department, error = refs.department(values["department_ref"])
        if error is None:
            cls, error = refs.klass(values["class_ref"], department, values["department_ref"])
        if error:
            result.bad_row(ref, error)
            continue
        candidates.append((portal_id, values, department, cls))

    seen = Counter(portal_id for portal_id, *_ in candidates)
    for portal_id, n in seen.items():
        if n > 1:
            result.bad_row(portal_id, f"The portal sent {n} rows with this portal ID.", n=n)
    candidates = [c for c in candidates if seen[c[0]] == 1]

    with transaction.atomic():
        # Which rows are new is read under the plan's student lock, so an
        # upload at the same time can't jointly go over the cap.
        limits.lock_students()
        by_portal_id = {
            s.external_id: s
            for s in Student.objects.filter(
                external_id__in=[c[0] for c in candidates]
            )
        }
        unlinked = defaultdict(list)
        for s in Student.objects.filter(
            external_id__isnull=True,
            matric_no__in=[c[1]["matric_no"] for c in candidates],
        ):
            unlinked[s.matric_no].append(s)

        to_create, to_update = [], []
        # Classes, not ids: a class created below has no id yet.
        touched = []
        for portal_id, values, department, cls in candidates:
            new = {
                "first_name": values["first_name"],
                "last_name": values["last_name"],
                "email": values.get("email", ""),
                "phone": values.get("phone", ""),
                "department": department,
                "level": cls,
            }
            student = by_portal_id.get(portal_id)
            linked = False
            if student is None:
                matches = unlinked.get(values["matric_no"], [])
                if len(matches) > 1:
                    result.bad_row(
                        portal_id,
                        f"Ambiguous match: {len(matches)} students already have "
                        f"matric no {values['matric_no']}.",
                    )
                    continue
                if matches:
                    # Taken: a second row with this matric no can't link it too.
                    student, linked = matches.pop(), True
                    student.external_id = portal_id
            if student is None:
                to_create.append(
                    Student(matric_no=values["matric_no"], external_id=portal_id, **new)
                )
                touched.append(cls)
                continue
            changed = (
                student.matric_no != values["matric_no"]
                or student.department_id != department.id
                or student.level_id != cls.id
                or any(
                    getattr(student, name) != new[name]
                    for name in ("first_name", "last_name", "email", "phone")
                )
            )
            if not (changed or linked):
                continue
            student.matric_no = values["matric_no"]
            touched.append(student.level_id)
            touched.append(cls)
            for name in _STUDENT_FIELDS:
                setattr(student, name, new[name])
            to_update.append(student)
            result.add("linked" if linked else "updated", [student.matric_no])

        cap = limits.current().max_students
        if cap is not None and to_create:
            # Students the delete pass will remove: gone, and with no seats.
            leaving = Student.objects.filter(
                pk__in=results.gone.get("students", ()), seatarrangement__isnull=True
            ).count()
            projected = Student.objects.count() - leaving + len(to_create)
            if projected > cap:
                result.status = "held"
                result.clear("linked", "updated")
                result.add("held", [s.matric_no for s in to_create + to_update])
                # The departments and classes these rows named stay unwritten.
                result.issue(
                    SyncIssue.Kind.PLAN_LIMIT,
                    "",
                    f"This sync would bring the school to {projected:,} students; "
                    f"your plan allows {cap:,}. Nothing was applied for students.",
                )
                return result

        refs.save_new()
        if to_create:
            Student.objects.bulk_create(to_create, batch_size=250)
        if to_update:
            Student.objects.bulk_update(
                to_update,
                ["matric_no", "external_id", *_STUDENT_FIELDS],
                batch_size=250,
            )
        result.add("created", [s.matric_no for s in to_create])
        # Bulk writes skip model signals, so sync the display size here.
        Class.objects.sync_student_counts(
            {c if isinstance(c, int) else c.pk for c in touched}
        )
    return result
