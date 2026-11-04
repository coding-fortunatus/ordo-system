"""Regression tests for the 2026-11-04 security audit (docs/security/
2026-11-04-audit-run-1). Each class names the finding it pins down."""
import datetime as dt
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile

from ems import pdf_gen, seating_rules, upload_handlers
from ems.directory import payload_for
from ems.models import Course, SeatArrangement, Student, SystemSettings, User
from tenancy.testcases import SchoolClient, SchoolTestCase

from .helpers import enrol, make_class, make_course, make_department, make_hall, schedule

DATE, PERIOD = dt.date(2026, 8, 17), "AM"


def officer(email, department, **fields):
    return User.objects.create_user(email=email, password="x", department=department, **fields)


def admin(email="sa@example.com", role=User.Role.SUPER_ADMIN):
    return User.objects.create_user(email=email, password="x", role=role, is_staff=True)


def client_for(user):
    c = SchoolClient()
    c.force_authenticate(user)
    return c


class NestedCourseEditTests(SchoolTestCase):
    """F01: a department officer can't edit a shared course via the class route."""

    def test_officer_cannot_patch_a_course_through_their_class(self):
        ac, bf = make_department("ac"), make_department("bf")
        ac_class, bf_class = make_class(1, ac), make_class(1, bf)
        course = make_course("BFN 101")
        bf_class.courses.add(course)
        ac_class.courses.add(course)  # linked, as the old POST allowed
        c = client_for(officer("ada@example.com", ac))

        r = c.patch(
            f"/api/classes/{ac_class.pk}/courses/{course.pk}/",
            {"name": "Hijacked"}, format="json",
        )

        self.assertEqual(r.status_code, 403)
        course.refresh_from_db()
        self.assertEqual(course.name, "Course BFN 101")

    def test_data_officer_still_can(self):
        cls = make_class(1)
        course = make_course("ACC 101")
        cls.courses.add(course)
        c = client_for(admin(role=User.Role.DATA_OFFICER))

        r = c.patch(f"/api/classes/{cls.pk}/courses/{course.pk}/", {"name": "New"}, format="json")

        self.assertEqual(r.status_code, 200)


class DirectoryScopeTests(SchoolTestCase):
    """F02: the directory respects publication and department scope."""

    def setUp(self):
        self.ac, self.bf = make_department("ac"), make_department("bf")
        self.ac_class = make_class(1, self.ac, name="ND II")
        self.bf_class = make_class(1, self.bf, name="HND II")
        ac_course, bf_course = make_course("ACC 1"), make_course("BFN 1")
        schedule(self.ac_class, ac_course, DATE, PERIOD)
        schedule(self.bf_class, bf_course, DATE, PERIOD)
        hall = make_hall("Main", 4, 4)
        for cls, course, matric in ((self.ac_class, ac_course, "AC0001"), (self.bf_class, bf_course, "BF0001")):
            (s,) = enrol(cls, [matric])
            SeatArrangement.objects.create(
                date=DATE, period=PERIOD, student=s, seat_number=1 if cls == self.ac_class else 3,
                hall=hall, course=course, cls=cls,
            )
        self.ada = officer("ada@example.com", self.ac)

    def publish(self):
        row = SystemSettings.objects.first() or SystemSettings.objects.create(
            session="2025/2026", semester="1st Semester"
        )
        row.timetable_published_at = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
        row.save()

    def test_draft_is_hidden_from_officers(self):
        self.assertEqual(payload_for(self.ada, "hall", "duration", None, PERIOD), [])
        r = client_for(self.ada).get("/api/directory/preview/?doc=visa&scope=duration")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["slots"], [])

    def test_published_shows_only_own_department(self):
        self.publish()
        hall = payload_for(self.ada, "hall", "slot", DATE, PERIOD)
        self.assertEqual([row["class_name"] for row in hall[0]["rows"]], [self.ac_class.full_label])
        visa = payload_for(self.ada, "visa", "slot", DATE, PERIOD)
        self.assertEqual([g["department"] for g in visa[0]["groups"]], ["AC"])

    def test_officer_without_department_sees_nothing(self):
        self.publish()
        nobody = User.objects.create_user(email="n@example.com", password="x")
        self.assertEqual(payload_for(nobody, "hall", "slot", DATE, PERIOD), [])

    def test_staff_see_everything(self):
        rows = payload_for(admin(), "hall", "slot", DATE, PERIOD)[0]["rows"]
        self.assertEqual(len(rows), 2)


class SeedDepartmentsTests(SchoolTestCase):
    """F03 + F04: no default password; overwrite clears the role."""

    def setUp(self):
        self.dept = make_department("acc")
        self.sa = admin()
        self.c = client_for(self.sa)

    def test_password_is_required(self):
        r = self.c.post("/api/users/seed-departments/", {}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(User.objects.filter(email="acc@ems.com").exists())

    def test_old_default_is_no_longer_accepted_silently(self):
        r = self.c.post("/api/users/seed-departments/", {"password": "short"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_overwrite_clears_an_admin_role(self):
        self.c.post("/api/users/seed-departments/", {"password": "First-Pass-9921"}, format="json")
        User.objects.filter(email="acc@ems.com").update(role=User.Role.SUPER_ADMIN, is_staff=True)

        r = self.c.post(
            "/api/users/seed-departments/",
            {"overwrite": True, "password": "Shared-Pass-7734"}, format="json",
        )

        self.assertEqual(r.status_code, 200)
        user = User.objects.get(email="acc@ems.com")
        self.assertIsNone(user.role)
        self.assertFalse(user.is_super_admin)

    def test_overwrite_false_string_is_false(self):
        self.c.post("/api/users/seed-departments/", {"password": "First-Pass-9921"}, format="json")
        r = self.c.post(
            "/api/users/seed-departments/",
            {"overwrite": "false", "password": "Shared-Pass-7734"}, format="json",
        )
        self.assertEqual(r.data["updated"], [])
        self.assertEqual(r.data["skipped"], ["acc@ems.com"])


class PdfMarkupTests(SchoolTestCase):
    """F05: school data is text in the PDF, never reportlab markup."""

    def test_img_markup_is_not_fetched(self):
        payload = [{
            "date": "2026-08-17", "period": "AM", "title": "<b>T",
            "rows": [{"hall": "<img src=http://0:8080 />", "class_name": "<b>",
                      "count": 1, "matric_range": "<img src=/etc/passwd />"}],
        }]
        with mock.patch("reportlab.lib.utils.rlUrlRead") as fetch:
            pdf = pdf_gen.build_pdf("hall", payload, SystemSettings(institution_name="<i>X"))
        fetch.assert_not_called()
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_visa_codes_are_escaped(self):
        payload = [{"date": "2026-08-17", "period": "AM", "title": "V",
                    "codes": ["AC <img src=http://0:8080 />"], "groups": []}]
        with mock.patch("reportlab.lib.utils.rlUrlRead") as fetch:
            pdf_gen.build_pdf("visa", payload, SystemSettings())
        fetch.assert_not_called()


class HallBoundsTests(SchoolTestCase):
    """F07: hall grids are bounded everywhere they enter."""

    def test_api_rejects_huge_rows(self):
        c = client_for(admin(role=User.Role.DATA_OFFICER))
        r = c.post("/api/halls/", {"name": "Big", "capacity": 1, "rows": 10**6, "columns": 10}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_api_rejects_too_many_cells(self):
        c = client_for(admin(role=User.Role.DATA_OFFICER))
        r = c.post("/api/halls/", {"name": "Big", "capacity": 1, "rows": 200, "columns": 200}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_csv_shape_rejects_huge_grid(self):
        with self.assertRaises(upload_handlers.UploadError):
            upload_handlers._hall_shape({"ROWS": "1000000000", "COLS": "1000000000", "ROW_SEATS": None}, "X")

    def test_row_seats_rejects_too_many_rows(self):
        with self.assertRaises(ValueError):
            seating_rules.mask_from_row_seats([1] * (seating_rules.MAX_ROWS + 1))

    def test_stored_oversized_hall_never_materialises(self):
        with self.assertRaises(ValueError):
            seating_rules.HallLayout(10**6, 10**6)


class ClassStudentsDepartmentTests(SchoolTestCase):
    """F08: an officer's class upload can't take another department's students."""

    HEADER = "MATRIC NUMBER,FIRSTNAME,LASTNAME,EMAIL,PHONE NUMBER\n"

    def test_officer_upload_with_foreign_matric_is_refused(self):
        vaa, vbb = make_department("vaa"), make_department("vbb")
        own, other = make_class(1, vaa), make_class(1, vbb)
        (victim,) = enrol(other, ["VB0001"])
        c = client_for(officer("ada@example.com", vaa))
        f = SimpleUploadedFile(
            "s.csv", (self.HEADER + "VB0001,Changed,Name,attacker@example.com,0999\n").encode(),
            content_type="text/csv",
        )

        r = c.post(f"/api/uploads/class-students/{own.pk}/", {"file": f}, format="multipart")

        self.assertEqual(r.status_code, 400)
        victim.refresh_from_db()
        self.assertEqual((victim.department_id, victim.level_id, victim.first_name), (vbb.pk, other.pk, "Ada"))

    def test_data_manager_can_still_move_students(self):
        vaa, vbb = make_department("vaa"), make_department("vbb")
        own, other = make_class(1, vaa), make_class(1, vbb)
        enrol(other, ["VB0001"])
        f = SimpleUploadedFile("s.csv", (self.HEADER + "VB0001,A,B,a@example.com,0\n").encode())
        upload_handlers.upload_class_students(f, own, cross_department=True)
        self.assertEqual(Student.objects.get(matric_no="VB0001").department_id, vaa.pk)


class ClassCourseAssignPortalTests(SchoolTestCase):
    """F10: creating a course from the class page respects portal ownership."""

    def test_new_course_refused_when_portal_owns_courses(self):
        cls = make_class(1)
        c = client_for(admin())
        owned = mock.Mock(types={"courses"})
        with mock.patch("ems.portal_sync.managed.current", return_value=owned), \
                mock.patch("ems.portal_sync.managed.assert_links_editable"):
            r = c.post(f"/api/classes/{cls.pk}/courses/", {"name": "Dummy", "code": "DUM101"}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Course.objects.filter(code="DUM101").exists())
