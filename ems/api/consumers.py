from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from ems.api.auth_ws import still_valid
from ems.models import BackgroundJob
from ems.notifications import job_group, unread_count, user_group
from tenancy.ws import ObservedConsumerMixin, in_school


class JobProgressConsumer(ObservedConsumerMixin, AsyncJsonWebsocketConsumer):
    """Streams progress events for a single ``BackgroundJob``.

    URL: ``/ws/jobs/<job_id>/?token=<api token>``.
    Closes 4401 when the connection is unauthenticated and 4403 when the
    user does not own the job (or is not staff).
    """

    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return
        self.job_id = self.scope["url_route"]["kwargs"]["job_id"]
        snapshot = await self._fetch_snapshot()
        if snapshot is None:
            await self.close(code=4404)
            return
        if not user.is_staff and snapshot["created_by_id"] != user.id:
            await self.close(code=4403)
            return
        self.owner_id = snapshot["created_by_id"]
        self.group_name = job_group(self.scope["school"].schema_name, self.job_id)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json(snapshot["payload"])

    async def disconnect(self, code):
        group = getattr(self, "group_name", None)
        if group:
            await self.channel_layer.group_discard(group, self.channel_name)

    async def job_progress(self, event):
        # Authorization is re-checked per event: a token revoked after the
        # handshake (logout, password change, support end, role removal)
        # must stop the stream, not just future handshakes.
        if not await self._still_valid():
            await self.close(code=4401)
            return
        await self.send_json(event["data"])

    @database_sync_to_async
    def _still_valid(self):
        return still_valid(self.scope, staff_or_owner_id=self.owner_id)

    @database_sync_to_async
    def _fetch_snapshot(self):
        return in_school(self.scope, self._snapshot)

    def _snapshot(self):
        try:
            job = BackgroundJob.objects.only(
                "id",
                "job_id",
                "job_type",
                "status",
                "progress",
                "total_steps",
                "error_message",
                "result_data",
                "created_by_id",
            ).get(job_id=self.job_id)
        except BackgroundJob.DoesNotExist:
            return None
        return {
            "created_by_id": job.created_by_id,
            "payload": {
                "job_id": job.job_id,
                "job_type": job.job_type,
                "status": job.status,
                "progress": job.progress_percentage,
                "error_message": job.error_message,
                "result": job.result_data if job.status == "success" else None,
            },
        }


class NotificationConsumer(ObservedConsumerMixin, AsyncJsonWebsocketConsumer):
    """Pushes the signed-in user's new notifications as they are sent.

    URL: ``/ws/notifications/?token=<api token>``. Sends
    ``{"type": "unread", "count": n}`` on connect, then
    ``{"type": "notification", "notification": {...}}`` per new one.
    Closes 4401 when the connection is unauthenticated.
    """

    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return
        self.group_name = user_group(self.scope["school"].schema_name, user.pk)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json({"type": "unread", "count": await self._unread(user)})

    async def disconnect(self, code):
        group = getattr(self, "group_name", None)
        if group:
            await self.channel_layer.group_discard(group, self.channel_name)

    async def notification_new(self, event):
        if not await self._still_valid():
            await self.close(code=4401)
            return
        await self.send_json({"type": "notification", "notification": event["data"]})

    @database_sync_to_async
    def _still_valid(self):
        return still_valid(self.scope)

    @database_sync_to_async
    def _unread(self, user):
        return in_school(self.scope, unread_count, user)
