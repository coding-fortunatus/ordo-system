"""Hall Directory (hall summary) and VISA data builders.

Two per-slot exam documents:

* **Hall Directory / Hall Summary** — for a (date, period), one row per
  (hall, class): hall name, class name, number of students, and the matric
  number range. Sourced from generated seat allocation (``SeatArrangement``).
* **VISA** — the set of classes being examined that (date, period), rendered
  as short codes (see :func:`derive_visa_code`).

Both can be scoped to a single slot, a calendar week (Mon-Sat), or the whole
exam duration.
"""

from datetime import timedelta

from ems.models import Class, SeatArrangement, TimeTable

# Qualifier -> VISA prefix letter. ND has no prefix; Pre-ND -> P; Higher ND -> H.
_QUALIFIER_PREFIX = {"ND": "", "PND": "P", "HND": "H"}


def derive_visa_code(name: str) -> str:
    """Convert a stored class name to its VISA short code.

    ``PROGRAM[-SPEC] QUALIFIER YEAR`` -> ``{prefix}PROGRAM YEAR[_SPEC]``::

        "AC ND II"        -> "AC II"
        "AC PND II"       -> "PAC II"
        "AC HND II"       -> "HAC II"
        "ABET-SW HND II"  -> "HABET II_SW"

    Falls back to the trimmed name when no known qualifier is present.
    """
    if not name:
        return ""
    tokens = name.strip().split()
    qi = next(
        (i for i, t in enumerate(tokens) if t.upper() in _QUALIFIER_PREFIX),
        None,
    )
    if qi is None:
        return name.strip()
    prefix = _QUALIFIER_PREFIX[tokens[qi].upper()]
    program = " ".join(tokens[:qi])
    year = " ".join(tokens[qi + 1:])
    spec = ""
    if "-" in program:
        base, _, spec = program.partition("-")
        program = base
    code = f"{prefix}{program} {year}".strip()
    if spec:
        code = f"{code}_{spec}"
    return code


# --- Titles ---------------------------------------------------------------

def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "TH"
    else:
        suffix = {1: "ST", 2: "ND", 3: "RD"}.get(n % 10, "TH")
    return f"{n}{suffix}"


def hall_directory_title(date_obj, period: str) -> str:
    weekday = date_obj.strftime("%A").upper()
    month = date_obj.strftime("%B").upper()
    return f"{weekday}, {_ordinal(date_obj.day)} {month} {period} HALL DIRECTORY"


def visa_title(date_obj, period: str) -> str:
    weekday = date_obj.strftime("%A").upper()
    month = date_obj.strftime("%B").upper()
    return (
        f"{weekday} {_ordinal(date_obj.day)}, {month} {date_obj.year} "
        f"- {period} VISA"
    )


# --- Matric ranges --------------------------------------------------------

def format_matric_range(matrics: list[str]) -> str:
    """``["2430113585", ..., "2430113616"]`` -> ``"2430113585 - 3616"``.

    Shows the full start matric and abbreviates the end to the suffix that
    differs from the start (keeping at least the last 4 characters).

    Plain text order, the same order allocation uses to cut each class into
    per hall blocks (``order_by('matric_no')``). A numbers first key disagreed
    with it whenever a class held matrics of different lengths or forms
    (``245064439``, ``2520320002.0``), so a range could start with a student
    who actually sat in another hall's block.
    """
    cleaned = [m for m in matrics if m]
    if not cleaned:
        return ""
    ordered = sorted(set(cleaned))
    start, end = ordered[0], ordered[-1]
    if start == end:
        return start
    common = 0
    while common < len(start) and common < len(end) and start[common] == end[common]:
        common += 1
    cut = min(common, len(end) - 4) if len(end) > 4 else 0
    return f"{start} - {end[cut:]}"


# --- Data builders --------------------------------------------------------

def hall_summary_rows(date_obj, period: str, department_id=None) -> list[dict]:
    """One row per (hall, class) for a slot, from generated seat allocation.

    ``department_id`` limits the rows to that department's classes."""
    qs = (
        SeatArrangement.objects.filter(
            date=date_obj, period=period, seat_number__isnull=False
        )
        .select_related("student", "hall", "cls", "cls__department")
    )
    if department_id is not None:
        qs = qs.filter(cls__department_id=department_id)
    groups: dict[tuple, dict] = {}
    for sa in qs:
        key = (sa.hall_id, sa.cls_id)
        group = groups.setdefault(
            key, {"hall": sa.hall, "cls": sa.cls, "matrics": []}
        )
        if sa.student and sa.student.matric_no:
            group["matrics"].append(sa.student.matric_no)

    keyed = []
    for group in groups.values():
        matrics = group["matrics"]
        row = {
            "hall": group["hall"].name,
            # Department code + class name (e.g. "AC ND II").
            "class_name": group["cls"].full_label,
            "count": len(matrics),
            "matric_range": format_matric_range(matrics),
        }
        # Within a class, list halls by where their block starts so the
        # ranges read in ascending order. Blocks are handed out in database
        # hall name order, which is not the order people read hall names in
        # ("BJ1" before "BK 1", "Hall 10" before "Hall 2").
        keyed.append(((row["class_name"], min(matrics, default=""), row["hall"]), row))
    keyed.sort(key=lambda kr: kr[0])
    return [row for _, row in keyed]


def visa_groups(date_obj, period: str, department_id=None) -> list[dict]:
    """VISA codes for a slot, grouped by department.

    Returns ``[{department, department_name, codes: [...]}, ...]`` ordered by
    department code. Used by the on-screen preview's grouped table.
    ``department_id`` limits it to that department's classes.
    """
    classes = (
        Class.objects.filter(
            timetable_class__date=date_obj, timetable_class__period=period
        )
        .select_related("department")
        .distinct()
    )
    if department_id is not None:
        classes = classes.filter(department_id=department_id)
    groups: dict[str, dict] = {}
    for cls in classes:
        code = cls.visa_label
        if not code:
            continue
        dept = cls.department
        dept_code = (dept.slug.upper() if dept and dept.slug else "—")
        group = groups.setdefault(
            dept_code,
            {
                "department": dept_code,
                "department_name": dept.name if dept else "",
                "codes": [],
            },
        )
        group["codes"].append(code)

    result = []
    for dept_code in sorted(groups):
        group = groups[dept_code]
        group["codes"].sort()
        result.append(group)
    return result


def visa_codes(date_obj, period: str) -> list[str]:
    """Flat, sorted VISA codes for a slot (one entry per class). For the PDF."""
    return sorted(
        code
        for group in visa_groups(date_obj, period)
        for code in group["codes"]
    )


# --- Scope resolution -----------------------------------------------------

def all_slots(department_id=None) -> list[tuple]:
    """Every (date, period) in the timetable, ordered, AM before PM.
    ``department_id`` keeps only slots that examine that department."""
    pairs = TimeTable.objects.all()
    if department_id is not None:
        pairs = pairs.filter(class_obj__department_id=department_id)
    pairs = pairs.values_list("date", "period").distinct()
    return sorted(
        {(d, p) for d, p in pairs},
        key=lambda dp: (dp[0], 0 if dp[1] == "AM" else 1),
    )


def resolve_slots(scope: str, date_obj, period: str, department_id=None) -> list[tuple]:
    if scope == "slot":
        return [(date_obj, period)]
    slots = all_slots(department_id)
    if scope == "week":
        start = date_obj - timedelta(days=date_obj.weekday())  # Monday
        end = start + timedelta(days=5)  # Saturday
        return [(d, p) for (d, p) in slots if start <= d <= end]
    return slots  # duration


def build_payload(doc: str, scope: str, date_obj, period: str, department_id=None) -> list[dict]:
    """Structured per-slot data shared by the JSON preview and the PDF.

    ``department_id`` limits every slot and row to that department; the
    views pass it for department officers (see :func:`payload_for`)."""
    out = []
    for d, p in resolve_slots(scope, date_obj, period, department_id):
        if doc == "hall":
            out.append(
                {
                    "date": d.isoformat(),
                    "period": p,
                    "title": hall_directory_title(d, p),
                    "rows": hall_summary_rows(d, p, department_id),
                }
            )
        else:
            groups = visa_groups(d, p, department_id)
            out.append(
                {
                    "date": d.isoformat(),
                    "period": p,
                    "title": visa_title(d, p),
                    # Flat list drives the PDF; grouped list drives the preview.
                    "codes": sorted(
                        code for g in groups for code in g["codes"]
                    ),
                    "groups": groups,
                }
            )
    return out


def payload_for(user, doc: str, scope: str, date_obj, period: str) -> list[dict]:
    """:func:`build_payload` as ``user`` may see it.

    Admin-side users see everything. Everyone else sees nothing while the
    timetable is a draft, and afterwards only their own department; a
    non-admin with no department sees nothing."""
    from ems import publication

    if user.is_staff:
        return build_payload(doc, scope, date_obj, period)
    if not user.department_id or not publication.visible_to(user):
        return []
    return build_payload(doc, scope, date_obj, period, user.department_id)
