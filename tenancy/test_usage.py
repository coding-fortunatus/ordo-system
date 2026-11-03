"""Nightly usage snapshots and the console list's usage columns (spec 0003, child 0005)."""
from datetime import timedelta
from unittest import mock

from django.utils import timezone
from django_tenants.utils import schema_context

from ems.models import AuditLog, BackgroundJob, User
from ems.tests.helpers import listed_class
from tenancy import tasks
from tenancy.models import School, UsageSnapshot
from tenancy.tasks import SNAPSHOT_KEEP_DAYS, snapshot_usage
from tenancy.test_school_lifecycle import SchoolConsoleTestCase


class UsageTestCase(SchoolConsoleTestCase):
    def setUp(self):
        super().setUp()
        self.school = self.tenant

    def snapshot(self):
        with schema_context("public"):
            snapshot_usage.apply()

    def latest(self, school=None):
        return UsageSnapshot.objects.get(school=school or self.school, taken_on=timezone.now().date())


class SnapshotTests(UsageTestCase):
    def test_a_snapshot_records_the_schools_usage(self):
        # covers: AC-31, AC-35
        day_ago = timezone.now() - timedelta(hours=25)
        with schema_context(self.school.schema_name):
            listed_class(3)
            officer = User.objects.create_user(email="officer@test.localhost", password="x-123456789")
            User.objects.create_user(email="gone@test.localhost", password="x", is_active=False)
            User.objects.create_user(
                email="support+1@ordo.invalid", password="x",
                support_expires_at=timezone.now() + timedelta(hours=1),
            )
            BackgroundJob.objects.create(job_id="new", job_type="timetable", created_by=officer)
            old = BackgroundJob.objects.create(job_id="old", job_type="timetable", created_by=officer)
            BackgroundJob.objects.filter(pk=old.pk).update(started_at=day_ago)
            seen = AuditLog.objects.create(action="login")

        self.snapshot()

        usage = self.latest()
        self.assertEqual(usage.students, 3)
        # Active staff only, and never the support user.
        self.assertEqual(usage.staff_users, 1)
        self.assertEqual(usage.jobs_last_24h, 1)
        self.assertEqual(usage.last_activity_at, seen.created_at)

    def test_running_twice_in_a_day_updates_the_one_row(self):
        # covers: AC-31
        self.snapshot()
        with schema_context(self.school.schema_name):
            listed_class(2)
        self.snapshot()
        self.assertEqual(UsageSnapshot.objects.filter(school=self.school).count(), 1)
        self.assertEqual(self.latest().students, 2)

    def test_one_failing_school_never_stops_the_rest(self):
        # covers: AC-31
        self.create()
        other = School.objects.get(slug="unilag")
        real = tasks._usage

        def usage(school, now):
            if school.pk == self.school.pk:
                with schema_context(school.schema_name):
                    from django.db import connection

                    with connection.cursor() as cursor:
                        cursor.execute("SELECT * FROM no_such_table")
            return real(school, now)

        with mock.patch.object(tasks, "_usage", side_effect=usage):
            self.snapshot()

        self.assertFalse(UsageSnapshot.objects.filter(school=self.school).exists())
        self.assertEqual(self.latest(other).students, 0)

    def test_only_schools_with_a_live_schema_are_snapshotted(self):
        # covers: AC-31
        self.create()
        other = School.objects.get(slug="unilag")
        for status in (School.Status.SUSPENDED, School.Status.PENDING_DELETION):
            School.objects.filter(pk=other.pk).update(status=status)
            UsageSnapshot.objects.all().delete()
            self.snapshot()
            self.assertTrue(UsageSnapshot.objects.filter(school=other).exists(), status)

        for status in (School.Status.PROVISIONING, School.Status.FAILED, School.Status.PURGED):
            School.objects.filter(pk=other.pk).update(status=status)
            UsageSnapshot.objects.all().delete()
            self.snapshot()
            self.assertFalse(UsageSnapshot.objects.filter(school=other).exists(), status)

        # The public schema's own row is never a school.
        self.assertFalse(UsageSnapshot.objects.filter(school__schema_name="public").exists())

    def test_snapshots_older_than_400_days_are_dropped(self):
        # covers: AC-31
        today = timezone.now().date()
        for age in (SNAPSHOT_KEEP_DAYS, SNAPSHOT_KEEP_DAYS + 1):
            UsageSnapshot.objects.create(
                school=self.school, taken_on=today - timedelta(days=age),
                students=0, staff_users=0, jobs_last_24h=0,
            )
        self.snapshot()
        ages = sorted(
            (today - taken_on).days
            for taken_on in UsageSnapshot.objects.filter(school=self.school)
            .values_list("taken_on", flat=True)
        )
        self.assertEqual(ages, [0, SNAPSHOT_KEEP_DAYS])


class ConsoleListTests(UsageTestCase):
    def rows(self):
        response = self.owner.get("/api/platform/schools/")
        self.assertEqual(response.status_code, 200, response.data)
        return {row["id"]: row for row in response.data["results"]}

    def test_the_list_shows_each_schools_latest_snapshot(self):
        # covers: AC-31
        self.create()
        other = School.objects.get(slug="unilag")
        today = timezone.now().date()
        for days_ago, students in ((1, 10), (0, 12)):
            UsageSnapshot.objects.create(
                school=self.school, taken_on=today - timedelta(days=days_ago),
                students=students, staff_users=2, jobs_last_24h=4,
            )

        # One snapshot query and one "first admin joined" query for the
        # whole page, however many schools.
        with self.assertNumQueries(14):
            rows = self.rows()

        usage = rows[self.school.pk]["usage"]
        self.assertEqual(usage["students"], 12)
        self.assertEqual(usage["staff_users"], 2)
        self.assertEqual(usage["jobs_last_24h"], 4)
        self.assertEqual(usage["taken_on"], today.isoformat())
        self.assertIsNone(usage["last_activity_at"])
        # Not snapshotted yet.
        self.assertIsNone(rows[other.pk]["usage"])
