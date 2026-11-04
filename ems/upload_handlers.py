"""Shared CSV/Excel ingestion logic for the upload endpoints.

Each handler accepts a Django UploadedFile (and any resource-specific arg)
and returns a dict ``{"created": int, "updated": int}``. Validation
problems raise ``UploadError`` with a human-readable message — the API
view layer turns those into 400 responses.

The API endpoints are the only callers; the legacy HTML upload views have
been deleted along with the templates.
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd
from django.db import transaction

from ems.identifiers import clean_number_text
from ems.seating_rules import DEFAULT_SEAT_ORDER, SEAT_ORDERS
from ems.models import Class, Course, Department, Student
from tenancy import limits


class UploadError(ValueError):
    """Raised for any human-correctable problem with an uploaded file."""


def _read_csv(file, **kwargs) -> pd.DataFrame:
    try:
        return pd.read_csv(file, **kwargs)
    except Exception as exc:  # pragma: no cover - pandas error surface is wide
        raise UploadError(
            f"Could not read file: {exc}. Please confirm it is a valid CSV."
        ) from exc


def _check_columns(df: pd.DataFrame, required: Iterable[str]) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise UploadError(f"Missing required columns: {', '.join(missing)}")


def _check_no_duplicates(df: pd.DataFrame, column: str, label: str) -> None:
    dupes = df[df.duplicated(subset=[column], keep=False)][column].tolist()
    if dupes:
        sample = ", ".join(sorted(set(map(str, dupes)))[:5])
        suffix = "…" if len(set(dupes)) > 5 else ""
        raise UploadError(f"Duplicate {label} found in file: {sample}{suffix}")


def upload_departments(file) -> dict[str, int]:
    df = _read_csv(file)
    _check_columns(df, ["Name", "Code"])
    _check_no_duplicates(df, "Code", "department codes")

    created = updated = 0
    with transaction.atomic():
        for row in df.to_dict("records"):
            slug = str(row["Code"]).strip().upper()
            name = str(row["Name"]).strip()
            obj, was_created = Department.objects.get_or_create(
                slug=slug, defaults={"name": name}
            )
            if was_created:
                created += 1
            else:
                if obj.name != name:
                    obj.name = name
                    obj.save(update_fields=["name"])
                updated += 1
    return {"created": created, "updated": updated}


def _blank(value) -> bool:
    return value is None or pd.isna(value) or not str(value).strip()


def _hall_shape(row, name) -> dict:
    """``rows``, ``columns`` and ``layout`` for one hall upload row. A row
    with no ROW_SEATS is a full ROWS x COLS rectangle (``layout`` None), so
    re-uploading a hall without it resets any earlier layout."""
    from ems import seating_rules

    raw = row.get("ROW_SEATS")
    if _blank(raw):
        if _blank(row.get("ROWS")) or _blank(row.get("COLS")):
            raise UploadError(f"Hall '{name}': give ROWS and COLS, or ROW_SEATS.")
        try:
            rows, cols = int(row["ROWS"]), int(row["COLS"])
            seating_rules.check_grid(rows, cols)
        except ValueError as exc:
            raise UploadError(f"Hall '{name}': {exc}") from exc
        return {"rows": rows, "columns": cols, "layout": None}
    align = "left" if _blank(row.get("ALIGN")) else str(row["ALIGN"])
    try:
        counts = [
            int(float(part)) for part in str(raw).replace(";", ",").split(",") if part.strip()
        ]
        rows, cols, mask = seating_rules.mask_from_row_seats(counts, align)
    except ValueError as exc:
        raise UploadError(f"Hall '{name}': bad ROW_SEATS '{raw}'. {exc}") from exc
    return {"rows": rows, "columns": cols, "layout": list(mask) if mask else None}


def upload_halls(file) -> dict[str, int]:
    from ems.models import Hall

    df = _read_csv(file)
    # MAX STUDENTS and MIN COURSES are deprecated (see Hall): older files
    # that still carry them are accepted and the columns ignored.
    required = ["EXAM VENUE", "CAPACITY"]
    # ROW_SEATS ("10,12,12,8": seats per row, front row first) describes a
    # hall whose rows differ in length; ALIGN (left/center/right, default
    # left) says where the short rows sit. Without it ROWS x COLS is a full
    # rectangle.
    if "ROW_SEATS" not in df.columns:
        required += ["ROWS", "COLS"]
    _check_columns(df, required)
    _check_no_duplicates(df, "EXAM VENUE", "hall names")

    created = updated = 0
    with transaction.atomic():
        for row in df.to_dict("records"):
            name = str(row["EXAM VENUE"]).strip()
            group = row.get("GROUP")
            order = row.get("SEAT_ORDER")
            if not _blank(order) and str(order).strip().lower() not in SEAT_ORDERS:
                raise UploadError(
                    f"Hall '{name}': SEAT_ORDER must be one of "
                    f"{', '.join(SEAT_ORDERS)} (got '{order}')."
                )
            defaults = {
                "seat_order": (
                    DEFAULT_SEAT_ORDER if _blank(order) else str(order).strip().lower()
                ),
                # Blank = filled from the name on save (see Hall.save).
                "group": "" if _blank(group) else str(group).strip().upper(),
                "capacity": int(row["CAPACITY"]),
                **_hall_shape(row, name),
            }
            hall, was_created = Hall.objects.get_or_create(
                name=name, defaults=defaults
            )
            if was_created:
                created += 1
            else:
                for k, v in defaults.items():
                    setattr(hall, k, v)
                hall.save()
                updated += 1
    return {"created": created, "updated": updated}


def upload_courses(file) -> dict[str, int]:
    df = _read_csv(file)
    _check_columns(df, ["COURSE CODE", "COURSE TITLE", "EXAM TYPE"])
    _check_no_duplicates(df, "COURSE CODE", "course codes")

    created = updated = 0
    with transaction.atomic():
        for row in df.to_dict("records"):
            code = str(row["COURSE CODE"]).strip()
            exam_type = str(row["EXAM TYPE"]).strip().upper()
            if exam_type not in ("PBE", "CBE"):
                raise UploadError(
                    f"Row with code {code} has invalid exam type '{exam_type}'. "
                    "Allowed values are PBE or CBE."
                )
            name = str(row["COURSE TITLE"]).strip()
            course, was_created = Course.objects.get_or_create(
                code=code, defaults={"name": name, "exam_type": exam_type}
            )
            if was_created:
                created += 1
            else:
                course.name = name
                course.exam_type = exam_type
                course.save()
                updated += 1
    return {"created": created, "updated": updated}


def upload_classes_for_department(file, department: Department) -> dict[str, int]:
    df = _read_csv(file)
    # A ``Size`` column is still accepted but ignored: a class's size is its
    # uploaded student list (spec 0001).
    _check_columns(df, ["Name"])
    _check_no_duplicates(df, "Name", "class names")

    created = updated = 0
    with transaction.atomic():
        for row in df.to_dict("records"):
            name = str(row["Name"]).strip()
            _cls, was_created = Class.objects.get_or_create(
                name=name, department=department
            )
            if was_created:
                created += 1
            else:
                updated += 1
    return {"created": created, "updated": updated}


def upload_class_courses(file, cls: Class) -> dict[str, int]:
    if not Course.objects.exists():
        raise UploadError(
            "No courses found in the system. Upload the institutional course "
            "catalog before assigning courses to a class."
        )
    df = _read_csv(file)
    _check_columns(df, ["COURSE CODE"])

    codes_in_file = [
        str(c).strip()
        for c in df["COURSE CODE"].tolist()
        if pd.notna(c) and str(c).strip() and str(c).strip().lower() != "nan"
    ]
    if not codes_in_file:
        raise UploadError("No course codes found in the uploaded file.")
    existing = set(Course.objects.values_list("code", flat=True))
    invalid = [c for c in codes_in_file if c not in existing]
    if invalid:
        sample = ", ".join(sorted(set(invalid))[:10])
        raise UploadError(
            f"The following course codes do not exist in the system: {sample}"
            + ("…" if len(set(invalid)) > 10 else "")
        )

    courses = Course.objects.filter(code__in=codes_in_file)
    added = 0
    with transaction.atomic():
        for course in courses:
            if not cls.courses.filter(pk=course.pk).exists():
                cls.courses.add(course)
                added += 1
    return {"created": added, "updated": len(codes_in_file) - added}


def upload_class_students(file, cls: Class, *, cross_department: bool = True) -> dict[str, int]:
    """Create or update ``cls``'s students from a CSV.

    ``cross_department`` False (any uploader who is not a data manager)
    refuses the whole file when a matric number already belongs to a
    student of another department: re-uploading would move and overwrite
    a student the uploader has no access to."""
    # Read identifiers as text. Left to inference, one blank cell turns the
    # column into floats and astype(str) below then stores "2520320002.0".
    df = _read_csv(file, dtype={"MATRIC NUMBER": str, "PHONE NUMBER": str})
    _check_columns(
        df,
        ["MATRIC NUMBER", "FIRSTNAME", "LASTNAME", "EMAIL", "PHONE NUMBER"],
    )
    # Drop rows with no matric number (blank trailing rows would otherwise
    # become the literal string "nan" after astype(str) and trigger the
    # duplicate-matric-number check).
    df = df.dropna(subset=["MATRIC NUMBER"])
    df = df.astype(str).apply(lambda c: c.str.strip())
    # A CSV saved from Excel can hold "2530710047.0"; strip that tail
    # before the duplicate check so "X" and "X.0" count as one student.
    df = df.assign(**{
        col: df[col].map(clean_number_text)
        for col in ("MATRIC NUMBER", "PHONE NUMBER")
        if col in df.columns
    })
    df = df[~df["MATRIC NUMBER"].str.lower().isin(("", "nan"))]
    if df.empty:
        raise UploadError("No student rows with a matric number were found in the file.")
    _check_no_duplicates(df, "MATRIC NUMBER", "matric numbers")

    matric_numbers = df["MATRIC NUMBER"].tolist()
    with transaction.atomic():
        # Which rows are new is read under the plan's student lock, so two
        # uploads at once can't jointly go over the cap.
        limits.lock_students()
        existing = {
            s.matric_no: s
            for s in Student.objects.filter(matric_no__in=matric_numbers).only(
                "id",
                "matric_no",
                "first_name",
                "last_name",
                "email",
                "phone",
                "department_id",
                "level_id",
            )
        }
        if not cross_department:
            foreign = sorted(
                m for m, s in existing.items() if s.department_id != cls.department_id
            )
            if foreign:
                shown = ", ".join(foreign[:5]) + (" …" if len(foreign) > 5 else "")
                raise UploadError(
                    f"{len(foreign)} matric number(s) belong to students of another "
                    f"department ({shown}). Ask a data officer to move them."
                )

        to_create: list[Student] = []
        to_update: list[Student] = []
        # Classes whose lists this upload changes: this one, plus any class a
        # re-uploaded student moves out of.
        touched = {cls.id}
        for record in df.to_dict("records"):
            matric = record["MATRIC NUMBER"]
            if matric in existing:
                s = existing[matric]
                touched.add(s.level_id)
                s.first_name = record["FIRSTNAME"]
                s.last_name = record["LASTNAME"]
                s.email = record["EMAIL"]
                s.phone = record["PHONE NUMBER"]
                s.department = cls.department
                s.level = cls
                to_update.append(s)
            else:
                to_create.append(
                    Student(
                        matric_no=matric,
                        first_name=record["FIRSTNAME"],
                        last_name=record["LASTNAME"],
                        email=record["EMAIL"],
                        phone=record["PHONE NUMBER"],
                        department=cls.department,
                        level=cls,
                    )
                )

        limits.assert_can_add_students(len(to_create))
        if to_create:
            Student.objects.bulk_create(to_create, batch_size=250)
        if to_update:
            Student.objects.bulk_update(
                to_update,
                [
                    "first_name",
                    "last_name",
                    "email",
                    "phone",
                    "department",
                    "level",
                ],
                batch_size=250,
            )
        # Bulk writes skip model signals, so sync the display size here.
        Class.objects.sync_student_counts(touched)
    return {"created": len(to_create), "updated": len(to_update)}


# Map for the bulk upload dispatcher (POST /api/uploads/bulk/)
BULK_HANDLERS = {
    "departments": upload_departments,
    "halls": upload_halls,
    "courses": upload_courses,
}
