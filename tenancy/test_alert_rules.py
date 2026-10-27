"""Explorer, dashboards and alert rules (spec 0006, slice 5)."""
from datetime import timedelta
from unittest import mock

from django.db import connection
from django.urls import Resolver404, resolve
from django.utils import timezone

from tenancy.models import (
    AlertIncident,
    AlertRule,
    AlertState,
    Chart,
    Dashboard,
    Plan,
    PlatformAdmin,
    PlatformAuditLog,
    School,
)
from tenancy.observability import alert_rules, context, flush, metrics
from tenancy.test_metrics import NOW, MetricsMixin, at, recorded_at
from tenancy.test_observability import ObservabilityMixin
from tenancy.test_platform_auth import PlatformTestCase
from tenancy.testcases import SchoolTestCase

LABELS = {"route": "api/x/", "method": "GET", "status_class": "2xx"}


def request_at(school_id, minutes_ago, ms):
    with recorded_at(at(minutes_ago)), context.for_school(school_id):
        metrics.HTTP_REQUESTS.add(1, **LABELS)
        metrics.HTTP_DURATION.record(ms, **LABELS)


class AlertMixin(MetricsMixin, ObservabilityMixin):
    def setUp(self):
        super().setUp()
        # A second live school, without a schema: only its row is read.
        connection.set_schema_to_public()
        self.other = School.objects.create(
            slug="other",
            name="Other School",
            schema_name="school_other",
            plan=Plan.objects.get(code="platform"),
            status=School.Status.ACTIVE,
            first_admin_name="O",
            first_admin_email="o@other.localhost",
        )
        connection.set_tenant(self.tenant)
        self.sent = mock.patch("tenancy.observability.alert_rules.platform_alerts.send").start()
        self.addCleanup(mock.patch.stopall)
        self.flushed_ok(NOW)

    def flushed_ok(self, when):
        self.telemetry.set(flush.LAST_OK_KEY, int(when.timestamp()))

    def headlines(self):
        return [c.args[0] for c in self.sent.call_args_list]

    def rule(self, **fields):
        values = {
            "name": "Slow school",
            "metric_name": "http.server.duration",
            "aggregation": "p95",
            "comparison": "gt",
            "threshold": 2000,
            "for_minutes": 10,
            "scope": "per_school",
        }
        return AlertRule.objects.create(**{**values, **fields})


class EvaluationTests(AlertMixin, SchoolTestCase):
    def test_fires_once_for_the_slow_school_and_resolves_after_five_clear_checks(self):
        # covers: AC-18
        for minute in range(2, 25):
            request_at(self.tenant.pk, minute, 3000)
            request_at(self.other.pk, minute, 40)
        self.flush()
        rule = self.rule()

        alert_rules.evaluate(now=NOW)
        alert_rules.evaluate(now=NOW)
        incident = AlertIncident.objects.get()
        self.assertEqual((incident.school_id, incident.resolved_at), (self.tenant.pk, None))
        self.assertEqual(self.headlines(), [f"Alert: Slow school for {self.tenant.name}"])
        states = dict(AlertState.objects.filter(rule=rule).values_list("school_id", "state"))
        self.assertEqual(states, {self.tenant.pk: "firing", self.other.pk: "ok"})

        # Fast again from the next minute: 4 clear checks keep it open, the
        # 5th resolves it with one email.
        later = NOW + timedelta(minutes=10)
        for minute in range(-9, 2):
            request_at(self.tenant.pk, minute, 40)
            request_at(self.other.pk, minute, 40)
        self.flush(now=later)
        for i in range(1, 6):
            self.flushed_ok(NOW + timedelta(minutes=i))
            alert_rules.evaluate(now=NOW + timedelta(minutes=i))
            incident.refresh_from_db()
            self.assertEqual(incident.resolved_at is not None, i == 5)
        self.assertEqual(len(self.headlines()), 2)
        self.assertEqual(self.headlines()[1], f"Resolved: Slow school for {self.tenant.name}")
        self.assertEqual(AlertState.objects.get(rule=rule, school=self.tenant).state, "ok")

    def test_one_slow_minute_is_not_enough(self):
        for minute in range(2, 25):
            request_at(self.tenant.pk, minute, 3000 if minute == 2 else 40)
        self.flush()
        rule = self.rule()
        alert_rules.evaluate(now=NOW)
        self.assertFalse(AlertIncident.objects.exists())
        self.assertEqual(AlertState.objects.get(rule=rule, school=self.tenant).state, "ok")

    def test_a_gap_neither_fires_nor_resolves(self):
        # covers: AC-19
        for minute in range(2, 25):
            request_at(self.tenant.pk, minute, 3000)
        self.flush()
        rule = self.rule()
        alert_rules.evaluate(now=NOW)
        incident = AlertIncident.objects.get()

        # Nothing flushed past NOW - 2 minutes: ten minutes on, every window
        # holds missing minutes.
        for i in range(1, 11):
            self.flushed_ok(NOW + timedelta(minutes=i))
            alert_rules.evaluate(now=NOW + timedelta(minutes=i))
        incident.refresh_from_db()
        self.assertIsNone(incident.resolved_at)
        state = AlertState.objects.get(rule=rule, school=self.tenant)
        self.assertEqual((state.state, state.clear_streak), ("no_data", 0))
        self.assertEqual(len(self.headlines()), 1)

    def test_a_quiet_school_counts_as_zero(self):
        # covers: AC-19
        self.flush()
        rule = self.rule(
            metric_name="http.server.requests", aggregation="sum", comparison="lt", threshold=1, for_minutes=3
        )
        alert_rules.evaluate(now=NOW)
        self.assertEqual(
            set(AlertState.objects.filter(rule=rule).values_list("state", flat=True)), {"firing"}
        )
        self.assertEqual(AlertIncident.objects.count(), 2)

    def test_platform_scope_sums_schools(self):
        for minute in range(2, 8):
            request_at(self.tenant.pk, minute, 40)
            request_at(self.other.pk, minute, 40)
        self.flush()
        self.rule(
            metric_name="http.server.requests",
            aggregation="sum",
            threshold=1.5,
            for_minutes=3,
            scope="platform",
        )
        alert_rules.evaluate(now=NOW)
        incident = AlertIncident.objects.get()
        self.assertIsNone(incident.school_id)
        self.assertEqual(incident.peak_value, 2)
        self.assertEqual(self.headlines(), ["Alert: Slow school for Platform"])

    def test_disabled_rules_are_skipped(self):
        for minute in range(2, 25):
            request_at(self.tenant.pk, minute, 3000)
        self.flush()
        self.rule(enabled=False)
        alert_rules.evaluate(now=NOW)
        self.assertFalse(AlertIncident.objects.exists())


class FlushStalledTests(AlertMixin, SchoolTestCase):
    def test_emails_once_an_hour_when_the_flush_stops(self):
        # covers: AC-19
        alert_rules.check_flush(NOW)
        self.assertEqual(self.headlines(), [])
        self.flushed_ok(NOW - timedelta(minutes=6))
        alert_rules.check_flush(NOW)
        alert_rules.check_flush(NOW + timedelta(minutes=1))
        self.assertEqual(self.headlines(), ["Telemetry flush has stopped"])

    def test_emails_when_the_telemetry_redis_is_down(self):
        with mock.patch("tenancy.observability.alert_rules.buffer.run", return_value=None):
            alert_rules.check_flush(NOW)
        self.assertEqual(self.headlines(), ["Telemetry flush has stopped"])
        self.assertIn("can't be reached", self.sent.call_args.args[1][0])


class RuleApiTests(AlertMixin, PlatformTestCase):
    URL = "/api/platform/observability/alert-rules/"
    BODY = {
        "name": "Slow school",
        "metric_name": "http.server.duration",
        "aggregation": "p95",
        "filters": {"method": "GET"},
        "comparison": "gt",
        "threshold": 2000,
        "for_minutes": 10,
        "scope": "per_school",
    }

    def setUp(self):
        super().setUp()
        self.owner = self.sign_in(self.make_admin())

    def test_owner_creates_edits_and_deletes_with_audit(self):
        # covers: AC-17, AC-20
        created = self.owner.post(self.URL, self.BODY, format="json")
        self.assertEqual(created.status_code, 201, created.content)
        rule_id = created.json()["id"]
        self.assertIn("p95 of http.server.duration", created.json()["description"])

        AlertIncident.objects.create(rule_id=rule_id, school=self.tenant, started_at=NOW, peak_value=1, last_value=1)
        AlertState.objects.create(rule_id=rule_id, school=self.tenant, state="firing", evaluated_at=NOW)

        # A new name changes nothing else.
        self.owner.patch(f"{self.URL}{rule_id}/", {"name": "Slow"}, format="json")
        self.assertTrue(AlertIncident.objects.filter(resolved_at__isnull=True).exists())
        # A new threshold resolves silently and forgets the states.
        patched = self.owner.patch(f"{self.URL}{rule_id}/", {"threshold": 3000}, format="json")
        self.assertEqual(patched.status_code, 200)
        self.assertFalse(AlertIncident.objects.filter(resolved_at__isnull=True).exists())
        self.assertFalse(AlertState.objects.exists())
        self.assertEqual(self.headlines(), [])

        detail = self.owner.get(f"{self.URL}{rule_id}/").json()
        self.assertEqual(len(detail["incidents"]), 1)
        self.assertEqual(self.owner.delete(f"{self.URL}{rule_id}/").status_code, 204)
        actions = list(
            PlatformAuditLog.objects.filter(action__startswith="observability.rule").values_list("action", flat=True)
        )
        self.assertEqual(
            sorted(actions),
            ["observability.rule.create", "observability.rule.delete", "observability.rule.update", "observability.rule.update"],
        )

    def test_validation(self):
        # covers: AC-17
        bad = {
            "metric_name": {"metric_name": "nope"},
            "aggregation": {"aggregation": "last"},
            "filters": {"filters": {"method": "BREW"}},
            "for_minutes": {"for_minutes": 121},
        }
        for field, change in bad.items():
            response = self.owner.post(self.URL, {**self.BODY, **change}, format="json")
            self.assertEqual(response.status_code, 400, field)
            self.assertIn(field, response.json()["errors"], field)
        host = {**self.BODY, "metric_name": "host.cpu.percent", "aggregation": "last", "filters": {}}
        refused = self.owner.post(self.URL, host, format="json")
        self.assertEqual(refused.json()["errors"]["scope"], ["This metric has no per school values."])
        self.assertEqual(self.owner.post(self.URL, {**host, "scope": "platform"}, format="json").status_code, 201)

    def test_who_can_do_what(self):
        # covers: AC-20
        support = self.sign_in(self.make_admin("support@ordo.app", role=PlatformAdmin.Role.SUPPORT))
        rule = self.rule()
        self.assertEqual(support.get(self.URL).status_code, 200)
        self.assertEqual(support.get("/api/platform/observability/incidents/").status_code, 200)
        self.assertEqual(support.post(self.URL, self.BODY, format="json").status_code, 403)
        self.assertEqual(support.patch(f"{self.URL}{rule.pk}/", {"name": "x"}, format="json").status_code, 403)
        self.assertEqual(support.delete(f"{self.URL}{rule.pk}/").status_code, 403)
        self.assertEqual(support.post(f"{self.URL}preview/", self.BODY, format="json").status_code, 403)
        self.assertIn(self.console.get(self.URL).status_code, (401, 403))
        # Never routed on a school host.
        with self.assertRaises(Resolver404):
            resolve(self.URL, urlconf="core.urls")

    def test_preview_shows_when_it_would_have_fired(self):
        now = timezone.now()
        stamp = (now - timedelta(minutes=30)).replace(second=0, microsecond=0)
        for i in range(12):
            with recorded_at((stamp + timedelta(minutes=i)).timestamp()), context.for_school(self.tenant.pk):
                metrics.HTTP_DURATION.record(3000, **LABELS)
        self.flush(now=now)
        response = self.owner.post(f"{self.URL}preview/", {**self.BODY, "filters": {}}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        fired = response.json()["fired"]
        self.assertEqual([f["school"] for f in fired], [self.tenant.pk])
        self.assertEqual(fired[0]["minute"], int((stamp + timedelta(minutes=9)).timestamp()))
        self.assertEqual(response.json()["schools"][str(self.tenant.pk)]["name"], self.tenant.name)

    def test_incidents_list_open_first_with_a_cursor(self):
        rule = self.rule()
        for i in range(60):
            AlertIncident.objects.create(
                rule=rule,
                school=self.tenant,
                started_at=NOW - timedelta(hours=i + 1),
                resolved_at=NOW,
                peak_value=1,
                last_value=1,
            )
        AlertIncident.objects.create(rule=rule, school=self.other, started_at=NOW - timedelta(days=9), peak_value=1, last_value=1)
        url = "/api/platform/observability/incidents/"
        first = self.owner.get(url).json()
        self.assertIsNone(first["results"][0]["resolved_at"])
        self.assertEqual(first["results"][0]["school"]["id"], self.other.pk)
        second = self.owner.get(url, {"cursor": first["next"]}).json()
        ids = [r["id"] for r in first["results"] + second["results"]]
        self.assertEqual(len(ids), 61)
        self.assertEqual(len(set(ids)), 61)
        self.assertIsNone(second["next"])
        only = self.owner.get(url, {"school": self.other.pk}).json()["results"]
        self.assertEqual(len(only), 1)


class ExplorerAndDashboardTests(AlertMixin, PlatformTestCase):
    URL = "/api/platform/observability/dashboards/"

    def setUp(self):
        super().setUp()
        self.owner_admin = self.make_admin()
        self.owner = self.sign_in(self.owner_admin)
        now = timezone.now()
        with recorded_at((now - timedelta(minutes=5)).timestamp()):
            with context.for_school(self.tenant.pk):
                metrics.HTTP_REQUESTS.add(3, **LABELS)
            with context.for_school(self.other.pk):
                metrics.HTTP_REQUESTS.add(1, **LABELS)
        self.flush(now=now)

    def test_registry_and_query(self):
        # covers: AC-24
        registry = self.owner.get("/api/platform/observability/metrics/registry/").json()["results"]
        requests = next(m for m in registry if m["name"] == "http.server.requests")
        self.assertEqual(requests["aggregations"], ["rate", "sum"])
        self.assertEqual(requests["group_by"][0], "school")
        self.assertIn("5xx", requests["labels"]["status_class"])
        cpu = next(m for m in registry if m["name"] == "host.cpu.percent")
        self.assertEqual(cpu["group_by"], [])

        url = "/api/platform/observability/metrics/query/"
        body = {"metric": "http.server.requests", "aggregation": "sum", "group_by": "school", "mode": "totals"}
        totals = self.owner.post(url, body, format="json").json()
        self.assertEqual({r["labels"]["school"]: r["value"] for r in totals["results"]}, {self.tenant.pk: 3, self.other.pk: 1})
        self.assertEqual(totals["schools"][str(self.other.pk)]["name"], "Other School")
        mine = self.owner.post(url, {**body, "school": self.tenant.pk, "group_by": "", "mode": "series"}, format="json").json()
        self.assertEqual(sum(v or 0 for _, v in mine["series"][0]["points"]), 3)
        self.assertEqual(self.owner.post(url, {**body, "aggregation": "p95"}, format="json").status_code, 400)
        self.assertEqual(self.owner.post(url, {**body, "filters": {"colour": "red"}}, format="json").status_code, 400)
        too_long = {**body, "start": (timezone.now() - timedelta(days=91)).isoformat()}
        self.assertEqual(self.owner.post(url, too_long, format="json").status_code, 400)

    def test_dashboards_are_shared_and_only_owners_delete(self):
        # covers: AC-24, AC-20
        support_admin = self.make_admin("support@ordo.app", role=PlatformAdmin.Role.SUPPORT)
        support = self.sign_in(support_admin)
        chart = {"title": "Requests", "metric_name": "http.server.requests", "aggregation": "rate", "group_by": "status_class"}
        created = support.post(self.URL, {"name": "Traffic", "charts": [chart]}, format="json")
        self.assertEqual(created.status_code, 201, created.content)
        dashboard = created.json()
        self.assertEqual(dashboard["created_by"]["name"], support_admin.name)
        self.assertEqual(dashboard["charts"][0]["school"], None)

        fixed = {**chart, "title": "Theirs", "school": self.other.pk}
        patched = self.owner.patch(f"{self.URL}{dashboard['id']}/", {"charts": [fixed, chart]}, format="json").json()
        self.assertEqual([c["title"] for c in patched["charts"]], ["Theirs", "Requests"])
        self.assertEqual(patched["charts"][0]["school"]["name"], "Other School")
        self.assertEqual(patched["updated_by"]["name"], self.owner_admin.name)
        self.assertEqual(Chart.objects.count(), 2)

        bad = self.owner.patch(f"{self.URL}{dashboard['id']}/", {"charts": [{**chart, "aggregation": "p95"}]}, format="json")
        self.assertEqual(bad.status_code, 400)
        listed = support.get(self.URL).json()["results"]
        self.assertEqual([(d["name"], d["chart_count"]) for d in listed], [("Traffic", 2)])

        self.assertEqual(support.delete(f"{self.URL}{dashboard['id']}/").status_code, 403)
        self.assertEqual(self.owner.delete(f"{self.URL}{dashboard['id']}/").status_code, 204)
        self.assertFalse(Dashboard.objects.exists())
        self.assertTrue(PlatformAuditLog.objects.filter(action="observability.dashboard.delete").exists())
        with self.assertRaises(Resolver404):
            resolve(self.URL, urlconf="core.urls")
