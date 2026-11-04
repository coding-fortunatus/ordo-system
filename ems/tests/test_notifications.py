"""The notification service, the inbox API and the live WebSocket push."""
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.db import connection
from rest_framework.authtoken.models import Token

from core.asgi import application
from ems.models import AuditLog, Notification, User
from ems.notifications import notify, user_group
from tenancy.testcases import (
    SCHOOL_TEST_HOST, SchoolClient, SchoolTestCase, SocketTestMixin,
)

from .helpers import make_department


def officer(email, department=None, **fields):
    return User.objects.create_user(
        email=email, password="x", department=department or make_department(), **fields
    )


class NotifyTests(SchoolTestCase):
    def setUp(self):
        self.ada = officer("ada@example.com")
        self.bola = officer("bola@example.com")

    def test_writes_one_row_per_recipient(self):
        notify([self.ada, self.bola.pk, self.ada], title="Hello", body="World")

        self.assertEqual(
            sorted(Notification.objects.values_list("recipient__email", "title")),
            [("ada@example.com", "Hello"), ("bola@example.com", "Hello")],
        )

    def test_skips_inactive_users(self):
        self.bola.is_active = False
        self.bola.save()

        sent = notify([self.ada, self.bola], title="Hello")

        self.assertEqual([n.recipient_id for n in sent], [self.ada.pk])

    def test_nobody_to_tell_writes_nothing(self):
        self.assertEqual(notify([], title="Hello"), [])
        self.assertFalse(Notification.objects.exists())

    def test_pushes_to_each_recipient_group_after_commit(self):
        layer = get_channel_layer()
        channel = async_to_sync(layer.new_channel)()
        async_to_sync(layer.group_add)(
            user_group(connection.schema_name, self.ada.pk), channel
        )

        with self.captureOnCommitCallbacks(execute=True):
            notify([self.ada], title="Hello", link="/timetable")

        message = async_to_sync(layer.receive)(channel)
        self.assertEqual(message["type"], "notification.new")
        self.assertEqual(message["data"]["title"], "Hello")
        self.assertEqual(message["data"]["link"], "/timetable")
        self.assertIsNone(message["data"]["read_at"])


class InboxApiTests(SchoolTestCase):
    def setUp(self):
        self.ada = officer("ada@example.com")
        self.bola = officer("bola@example.com")
        self.client = SchoolClient()
        self.client.force_authenticate(self.ada)
        self.first, = notify([self.ada], title="First")
        self.second, = notify([self.ada], title="Second")
        self.theirs, = notify([self.bola], title="Not yours")

    def test_lists_only_your_own_newest_first(self):
        data = self.client.get("/api/notifications/").data

        self.assertEqual([n["title"] for n in data["results"]], ["Second", "First"])

    def test_unread_filter_and_count(self):
        self.client.post(f"/api/notifications/{self.first.pk}/read/")

        unread = self.client.get("/api/notifications/?unread=true").data
        count = self.client.get("/api/notifications/unread-count/").data

        self.assertEqual([n["title"] for n in unread["results"]], ["Second"])
        self.assertEqual(count, {"count": 1})

    def test_mark_one_read(self):
        response = self.client.post(f"/api/notifications/{self.first.pk}/read/")

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data["read_at"])
        self.first.refresh_from_db()
        self.assertIsNotNone(self.first.read_at)

    def test_cannot_touch_someone_elses(self):
        response = self.client.post(f"/api/notifications/{self.theirs.pk}/read/")

        self.assertEqual(response.status_code, 404)
        self.theirs.refresh_from_db()
        self.assertIsNone(self.theirs.read_at)

    def test_mark_all_read_leaves_others_alone(self):
        response = self.client.post("/api/notifications/read-all/")

        self.assertEqual(response.data, {"updated": 2})
        self.assertEqual(
            list(Notification.objects.filter(read_at__isnull=True)), [self.theirs]
        )

    def test_reading_is_not_audited(self):
        self.client.post("/api/notifications/read-all/")

        self.assertFalse(AuditLog.objects.exists())

    def test_needs_sign_in(self):
        self.assertEqual(SchoolClient().get("/api/notifications/").status_code, 401)


class NotificationSocketTests(SocketTestMixin, SchoolTestCase):
    def setUp(self):
        self.ada = officer("ada@example.com")
        self.token = Token.objects.get(user=self.ada).key
        notify([self.ada], title="Waiting")

    def connect(self, token):
        return WebsocketCommunicator(
            application,
            f"/ws/notifications/?token={token}",
            headers=[
                (b"host", SCHOOL_TEST_HOST.encode()),
                (b"origin", f"http://{SCHOOL_TEST_HOST}:3000".encode()),
            ],
        )

    def test_sends_the_unread_count_then_each_new_one(self):
        async def scenario():
            ws = self.connect(self.token)
            connected, _ = await ws.connect()
            self.assertTrue(connected)
            self.assertEqual(await ws.receive_json_from(), {"type": "unread", "count": 1})

            await get_channel_layer().group_send(
                user_group("school_test", self.ada.pk),
                {"type": "notification.new", "data": {"title": "Live"}},
            )
            self.assertEqual(
                await ws.receive_json_from(),
                {"type": "notification", "notification": {"title": "Live"}},
            )
            await ws.disconnect()

        async_to_sync(scenario)()

    def test_a_revoked_token_stops_the_stream(self):
        # Security audit F09: revocation after the handshake must stop
        # delivery, not just future handshakes.
        async def scenario():
            ws = self.connect(self.token)
            connected, _ = await ws.connect()
            self.assertTrue(connected)
            await ws.receive_json_from()

            await database_sync_to_async(Token.objects.filter(key=self.token).delete)()
            await get_channel_layer().group_send(
                user_group("school_test", self.ada.pk),
                {"type": "notification.new", "data": {"title": "After revoke"}},
            )
            output = await ws.receive_output()
            self.assertEqual(output["type"], "websocket.close")
            self.assertEqual(output["code"], 4401)

        async_to_sync(scenario)()

    def test_refuses_an_unknown_token(self):
        async def scenario():
            ws = self.connect("nope")
            connected, code = await ws.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4401)

        async_to_sync(scenario)()
