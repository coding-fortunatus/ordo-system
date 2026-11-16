"""Plans and per school overrides in the console (spec 0003, child 0005)."""
from django.urls import Resolver404, resolve

from tenancy.models import Plan, PlatformAdmin, PlatformAuditLog, School
from tenancy.test_school_lifecycle import SchoolConsoleTestCase


class PlanTests(SchoolConsoleTestCase):
    def plan_payload(self, **overrides):
        return {
            "code": "pro",
            "name": "Pro",
            "max_students": 5000,
            "max_staff_users": 40,
            "features": ["exports", "seat_allocation", "distribution", "exports"],
            **overrides,
        }

    def create_plan(self, client=None, **overrides):
        return (client or self.owner).post(
            "/api/platform/plans/", self.plan_payload(**overrides), format="json"
        )

    def test_an_owner_creates_a_plan(self):
        # covers: AC-26
        response = self.create_plan()

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["features"], ["distribution", "exports", "seat_allocation"])
        self.assertEqual(response.data["school_count"], 0)
        self.assertTrue(response.data["is_active"])
        self.assertIsNone(response.data["price"])
        entry = PlatformAuditLog.objects.get(action="plan_created")
        self.assertEqual(entry.detail["code"], "pro")

    def test_bad_plans_are_refused(self):
        # covers: AC-26
        for overrides, field in [
            ({"features": ["teleport"]}, "features"),
            ({"features": ["seat_allocation"]}, "features"),
            ({"max_students": -1}, "max_students"),
            ({"code": "starter"}, "code"),
            ({"price": -1}, "price"),
        ]:
            response = self.create_plan(**overrides)
            self.assertEqual(response.status_code, 400, overrides)
            self.assertIn(field, response.data["errors"], overrides)

    def test_support_can_list_plans_but_not_change_them(self):
        support = self.sign_in(
            self.make_admin("support@ordo.app", role=PlatformAdmin.Role.SUPPORT)
        )

        self.assertEqual(support.get("/api/platform/plans/").status_code, 200)
        self.assertEqual(self.create_plan(support).status_code, 403)
        plan = Plan.objects.get(code="starter")
        response = support.patch(f"/api/platform/plans/{plan.pk}/", {"name": "X"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_editing_a_plan_changes_its_code_and_archives_it(self):
        # covers: AC-26
        plan = Plan.objects.get(code="starter")
        url = f"/api/platform/plans/{plan.pk}/"

        response = self.owner.patch(
            url,
            {"code": "basic", "max_students": 300, "price": 1_500_000, "is_active": False},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        plan.refresh_from_db()
        self.assertEqual(
            (plan.code, plan.max_students, plan.price, plan.is_active),
            ("basic", 300, 1_500_000, False),
        )
        entry = PlatformAuditLog.objects.get(action="plan_updated")
        self.assertEqual(entry.detail["code"], "starter")
        self.assertEqual(entry.detail["changes"]["code"], ["starter", "basic"])
        self.assertEqual(entry.detail["changes"]["price"], [None, 1_500_000])

    def test_a_code_change_keeps_the_schools_on_the_plan(self):
        self.assertEqual(self.create().status_code, 202)
        school = School.objects.get(slug="unilag")
        plan = school.plan

        response = self.owner.patch(
            f"/api/platform/plans/{plan.pk}/", {"code": "renamed"}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["school_count"], 1)
        school.refresh_from_db()
        self.assertEqual(school.plan.code, "renamed")

    def test_a_code_already_taken_is_refused_on_edit(self):
        self.assertEqual(self.create_plan().status_code, 201)
        plan = Plan.objects.get(code="starter")

        response = self.owner.patch(
            f"/api/platform/plans/{plan.pk}/", {"code": "pro"}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("code", response.data["errors"])
        plan.refresh_from_db()
        self.assertEqual(plan.code, "starter")

    def test_an_unused_plan_is_deleted(self):
        self.assertEqual(self.create_plan().status_code, 201)
        plan = Plan.objects.get(code="pro")
        listed = {p["code"]: p for p in self.owner.get("/api/platform/plans/").data}
        self.assertTrue(listed["pro"]["deletable"])

        response = self.owner.delete(f"/api/platform/plans/{plan.pk}/")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Plan.objects.filter(pk=plan.pk).exists())
        entry = PlatformAuditLog.objects.get(action="plan_deleted")
        self.assertEqual(entry.detail["code"], "pro")

    def test_a_plan_with_schools_is_archived_not_deleted(self):
        self.assertEqual(self.create().status_code, 202)
        plan = School.objects.get(slug="unilag").plan
        url = f"/api/platform/plans/{plan.pk}/"
        listed = {p["code"]: p for p in self.owner.get("/api/platform/plans/").data}
        self.assertFalse(listed[plan.code]["deletable"])

        response = self.owner.delete(url)

        self.assertEqual(response.status_code, 409)
        self.assertIn("Archive it instead", response.data["detail"])
        self.assertTrue(Plan.objects.filter(pk=plan.pk).exists())
        self.assertEqual(self.owner.patch(url, {"is_active": False}, format="json").status_code, 200)

    def test_the_platform_plan_is_never_deleted(self):
        plan = Plan.objects.get(code="platform")
        listed = {p["code"]: p for p in self.owner.get("/api/platform/plans/").data}
        self.assertFalse(listed["platform"]["deletable"])

        response = self.owner.delete(f"/api/platform/plans/{plan.pk}/")

        self.assertEqual(response.status_code, 409)
        self.assertTrue(Plan.objects.filter(pk=plan.pk).exists())

    def test_support_cant_delete_a_plan(self):
        self.assertEqual(self.create_plan().status_code, 201)
        plan = Plan.objects.get(code="pro")

        support = self.sign_in(
            self.make_admin("support@ordo.app", role=PlatformAdmin.Role.SUPPORT)
        )

        response = support.delete(f"/api/platform/plans/{plan.pk}/")

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Plan.objects.filter(pk=plan.pk).exists())

    def test_the_list_counts_schools_but_not_the_public_row(self):
        self.assertEqual(self.create().status_code, 202)

        plans = {p["code"]: p for p in self.owner.get("/api/platform/plans/").data}

        self.assertEqual(plans["starter"]["school_count"], 1)
        # Only the test school; the public row is on this plan too.
        self.assertEqual(plans["platform"]["school_count"], 1)


class EditSchoolTests(SchoolConsoleTestCase):
    def setUp(self):
        super().setUp()
        self.create()
        self.school = School.objects.get(slug="unilag")
        self.url = f"/api/platform/schools/{self.school.pk}/"

    def test_an_owner_sets_overrides(self):
        # covers: AC-26
        response = self.owner.patch(
            self.url,
            {"max_students_override": 200, "features_override": ["exports"], "notes": "Pilot"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["max_students_override"], 200)
        self.assertEqual(response.data["features_override"], ["exports"])
        self.school.refresh_from_db()
        self.assertEqual(self.school.notes, "Pilot")
        entry = PlatformAuditLog.objects.get(action="school_updated")
        self.assertEqual(entry.school, self.school)
        self.assertEqual(entry.detail["changes"]["max_students_override"], [None, 200])

    def test_clearing_an_override_goes_back_to_the_plan(self):
        self.owner.patch(self.url, {"max_students_override": 200}, format="json")

        self.owner.patch(self.url, {"max_students_override": None}, format="json")

        self.school.refresh_from_db()
        self.assertIsNone(self.school.max_students_override)

    def test_the_slug_never_changes(self):
        response = self.owner.patch(self.url, {"slug": "lagos", "name": "UNILAG"}, format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.school.refresh_from_db()
        self.assertEqual((self.school.slug, self.school.name), ("unilag", "UNILAG"))

    def test_a_school_stays_on_a_retired_plan_but_cant_move_onto_one(self):
        Plan.objects.filter(code="starter").update(is_active=False)
        Plan.objects.create(code="legacy", name="Legacy", is_active=False)

        kept = self.owner.patch(self.url, {"plan": "starter", "notes": "x"}, format="json")
        moved = self.owner.patch(self.url, {"plan": "legacy"}, format="json")

        self.assertEqual(kept.status_code, 200, kept.data)
        self.assertEqual(moved.status_code, 400)
        self.assertIn("plan", moved.data["errors"])

    def test_the_renewal_date_is_checked_against_the_saved_start(self):
        self.owner.patch(self.url, {"plan_started_on": "2026-09-01"}, format="json")

        response = self.owner.patch(self.url, {"renews_on": "2026-08-01"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("renews_on", response.data["errors"])

    def test_support_cant_edit_a_school(self):
        support = self.sign_in(
            self.make_admin("support@ordo.app", role=PlatformAdmin.Role.SUPPORT)
        )

        response = support.patch(self.url, {"notes": "x"}, format="json")

        self.assertEqual(response.status_code, 403)


class PlanAccessTests(SchoolConsoleTestCase):
    def test_plans_need_a_console_sign_in(self):
        self.assertEqual(self.console.get("/api/platform/plans/").status_code, 401)

    def test_a_school_host_has_no_plan_endpoints(self):
        # The console API lives only on console.<base>. Checked on the school
        # urlconf directly: rendering Django's 404 page breaks on Python 3.14
        # with Django 5.0.
        with self.assertRaises(Resolver404):
            resolve("/api/platform/plans/", urlconf="core.urls")

    def test_an_unknown_feature_is_refused_on_edit(self):
        plan = Plan.objects.get(code="starter")

        response = self.owner.patch(
            f"/api/platform/plans/{plan.pk}/", {"features": ["teleport"]}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("features", response.data["errors"])

    def test_an_edit_that_changes_nothing_writes_no_audit_row(self):
        plan = Plan.objects.get(code="starter")

        response = self.owner.patch(
            f"/api/platform/plans/{plan.pk}/", {"name": plan.name}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(PlatformAuditLog.objects.filter(action="plan_updated").exists())

    def test_a_missing_plan_is_404(self):
        response = self.owner.patch("/api/platform/plans/999999/", {"name": "X"}, format="json")

        self.assertEqual(response.status_code, 404)


class SchoolEditAccessTests(SchoolConsoleTestCase):
    def test_the_public_row_cant_be_edited(self):
        public = School.objects.get(schema_name="public")

        response = self.owner.patch(
            f"/api/platform/schools/{public.pk}/", {"name": "Hijacked"}, format="json"
        )

        self.assertEqual(response.status_code, 404)
        public.refresh_from_db()
        self.assertNotEqual(public.name, "Hijacked")

    def test_an_unknown_feature_override_is_refused(self):
        self.create()
        school = School.objects.get(slug="unilag")

        response = self.owner.patch(
            f"/api/platform/schools/{school.pk}/",
            {"features_override": ["teleport"]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("features_override", response.data["errors"])

    def test_a_negative_override_is_refused(self):
        self.create()
        school = School.objects.get(slug="unilag")

        response = self.owner.patch(
            f"/api/platform/schools/{school.pk}/", {"max_staff_users_override": -1}, format="json"
        )

        self.assertEqual(response.status_code, 400)
