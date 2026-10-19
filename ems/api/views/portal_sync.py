"""Portal sync API (spec 0005, *API surface*), under ``/api/portal-sync/``.

Super admins and data officers only, and only on a plan with the
``portal_sync`` feature (AC-15). Invalid input is 422, as the spec's
surface says; the envelope is the usual ``{detail, errors?, code?}``.
"""
from django.db import DatabaseError, transaction
from django.utils import timezone
from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from ems.api.exceptions import CodedAPIException, Conflict
from ems.api.pagination import DefaultPagination
from ems.api.permissions import IsDataOfficer, UploadsUnlocked
from ems.api.serializers.portal_sync import (
    ConnectionSerializer,
    EndpointSerializer,
    SyncIssueSerializer,
    SyncRunSerializer,
)
from ems.middleware import set_audit
from ems.models import (
    PortalConnection,
    PortalEndpoint,
    SyncIssue,
    SyncRun,
)
from ems.portal_sync import deletes
from ems.portal_sync import fields as ordo_fields
from ems.portal_sync import runs
from ems.portal_sync.fetch import FetchError, cell, columns, fetch_rows
from tenancy.permissions import requires_feature

SAMPLE_ROWS = 20

CONNECTION = "portal connection"
ENDPOINT = "portal endpoint"
RUN = "portal sync run"
ISSUE = "portal sync issue"

# Connection fields an audit row names when they change. Never the secret's
# value: only that it changed.
_AUDITED_FIELDS = ("mode", "auth_type", "header_name")

# Endpoint fields whose change makes the next sync a preview again (AC-4).
# ``is_enabled`` counts as adding or removing the endpoint.
_PREVIEWED = (
    "url",
    "format",
    "rows_path",
    "pagination",
    "page_param",
    "next_path",
    "field_mapping",
    "is_enabled",
)


class Invalid(ValidationError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


class FetchFailed(CodedAPIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "fetch_failed"


class UploadsLocked(CodedAPIException, PermissionDenied):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = UploadsUnlocked.message
    default_code = "uploads_locked"


def _valid(serializer):
    if not serializer.is_valid():
        raise Invalid(serializer.errors)
    return serializer


def _connection():
    connection = PortalConnection.objects.first()
    if connection is None:
        raise NotFound("No portal connection yet.")
    return connection


def assert_uploads_unlocked():
    """403 ``uploads_locked`` while a timetable exists (AC-13)."""
    if runs.timetable_exists():
        raise UploadsLocked()


class _PortalSyncView(APIView):
    permission_classes = [IsDataOfficer, requires_feature("portal_sync")]


class ConnectionView(_PortalSyncView):
    def get(self, request):
        return Response(ConnectionSerializer(_connection()).data)

    def put(self, request):
        with transaction.atomic():
            instance = PortalConnection.objects.select_for_update().first()
            before = {f: getattr(instance, f) for f in _AUDITED_FIELDS} if instance else {}
            serializer = _valid(
                ConnectionSerializer(instance, data=request.data, partial=instance is not None)
            )
            # Read before save: saving encrypts the secret out of validated_data.
            secret = "secret" in serializer.validated_data
            serializer.save(**({} if instance else {"created_by": request.user}))
            connection = serializer.instance
            if instance and connection.mode != before["mode"]:
                runs.reset_approval(connection)
        if instance is None:
            set_audit(request, "Created portal connection", object_type=CONNECTION, object_id=connection.pk)
        else:
            changed = [f for f in _AUDITED_FIELDS if getattr(connection, f) != before[f]]
            action = "Changed portal connection secret" if secret else "Updated portal connection"
            if secret and changed:
                action = "Updated portal connection and changed its secret"
            set_audit(
                request,
                action,
                object_type=CONNECTION,
                object_id=connection.pk,
                changed=changed,
                secret_changed=secret,
            )
        return Response(ConnectionSerializer(connection).data)

    def delete(self, request):
        """Disconnect (AC-17): every synced record becomes Ordo owned and
        editable again, the endpoints go with the connection, and the run
        history stays (its ``connection`` is set null)."""
        with transaction.atomic():
            try:
                connection = PortalConnection.objects.select_for_update(nowait=True).first()
            except DatabaseError:
                raise Conflict("A sync is starting. Try again in a moment.")
            if connection is None:
                raise NotFound("No portal connection yet.")
            if SyncRun.objects.filter(status=SyncRun.Status.RUNNING).exists():
                raise Conflict("A sync is running. Wait for it to finish, then disconnect.")
            # Deleting clears the instance's pk.
            connection_id = connection.pk
            released = runs.disconnect(connection)
        set_audit(
            request,
            "Deleted portal connection",
            object_type=CONNECTION,
            object_id=connection_id,
            released=released,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class FieldsView(_PortalSyncView):
    def get(self, request):
        return Response(ordo_fields.as_dict())


class EndpointListView(_PortalSyncView):
    def post(self, request):
        connection = _connection()
        serializer = _valid(
            EndpointSerializer(data=request.data, context={"connection": connection})
        )
        with transaction.atomic():
            serializer.save(connection=connection)
            runs.reset_approval(connection)
        endpoint = serializer.instance
        set_audit(
            request,
            f"Added portal endpoint ({endpoint.kind})",
            object_type=ENDPOINT,
            object_id=endpoint.pk,
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class EndpointDetailView(_PortalSyncView):
    def _endpoint(self, pk):
        return get_object_or_404(PortalEndpoint, pk=pk, connection=_connection())

    def patch(self, request, pk):
        endpoint = self._endpoint(pk)
        serializer = _valid(
            EndpointSerializer(
                endpoint,
                data=request.data,
                partial=True,
                context={"connection": endpoint.connection},
            )
        )
        before = {f: getattr(endpoint, f) for f in _PREVIEWED}
        with transaction.atomic():
            serializer.save()
            if any(getattr(endpoint, f) != v for f, v in before.items()):
                runs.reset_approval(endpoint.connection)
        set_audit(
            request,
            f"Updated portal endpoint ({endpoint.kind})",
            object_type=ENDPOINT,
            object_id=endpoint.pk,
            changed=sorted(set(serializer.validated_data)),
        )
        return Response(serializer.data)

    def delete(self, request, pk):
        endpoint = self._endpoint(pk)
        with transaction.atomic():
            endpoint.delete()
            runs.reset_approval(endpoint.connection)
        set_audit(
            request, f"Removed portal endpoint ({endpoint.kind})", object_type=ENDPOINT, object_id=pk
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class EndpointTestView(_PortalSyncView):
    """Fetch the first page only and show its columns and up to 20 rows.
    Nothing from the test is stored (AC-2)."""

    def post(self, request, pk):
        connection = _connection()
        endpoint = get_object_or_404(PortalEndpoint, pk=pk, connection=connection)
        set_audit(
            request, f"Tested portal endpoint ({endpoint.kind})", object_type=ENDPOINT, object_id=pk
        )
        try:
            rows = fetch_rows(endpoint, connection, first_page_only=True, retry=False)
        except FetchError as exc:
            error = FetchFailed(exc.message)
            error.reason = exc.reason
            raise error
        return Response(
            {
                "columns": columns(rows),
                "sample_rows": [
                    {str(k): cell(v) for k, v in row.items()}
                    for row in rows[:SAMPLE_ROWS]
                ],
                "row_count_on_page": len(rows),
            }
        )

    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        if isinstance(exc, FetchFailed):
            response.data["reason"] = exc.reason
        return response


class RunListView(_PortalSyncView, ListAPIView):
    serializer_class = SyncRunSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        return (
            SyncRun.objects.select_related("job", "started_by")
            .annotate(_issue_count=Count("issues"))
            .order_by("-started_at", "-id")
        )

    def post(self, request):
        connection = _connection()
        assert_uploads_unlocked()
        problem = runs.readiness_problem(connection)
        if problem:
            raise Invalid({"detail": problem})
        run = runs.start(connection.pk, user=request.user, trigger=SyncRun.Trigger.MANUAL)
        set_audit(
            request,
            "Started portal sync preview" if run.is_preview else "Started portal sync",
            object_type=RUN,
            object_id=run.pk,
        )
        return Response(
            {"run_id": run.pk, "job_id": run.job.job_id, "is_preview": run.is_preview},
            status=status.HTTP_202_ACCEPTED,
        )


class RunDetailView(_PortalSyncView):
    def get(self, request, pk):
        run = get_object_or_404(
            SyncRun.objects.select_related("job", "started_by").annotate(
                _issue_count=Count("issues")
            ),
            pk=pk,
        )
        return Response(SyncRunSerializer(run).data)


class RunApproveView(_PortalSyncView):
    """Approve a preview: it becomes ``approved`` and a real sync of the
    same config starts (AC-4)."""

    def post(self, request, pk):
        run = get_object_or_404(SyncRun, pk=pk)
        if run.status != SyncRun.Status.AWAITING_APPROVAL:
            raise Conflict("Only a preview waiting for approval can be approved.")
        connection = _connection()
        assert_uploads_unlocked()
        new = runs.start(
            connection.pk,
            user=request.user,
            trigger=SyncRun.Trigger.APPROVAL,
            approving=run.pk,
        )
        set_audit(
            request,
            f"Approved portal sync preview #{run.pk}",
            object_type=RUN,
            object_id=run.pk,
            new_run_id=new.pk,
        )
        return Response(
            {"run_id": new.pk, "job_id": new.job.job_id, "is_preview": new.is_preview},
            status=status.HTTP_202_ACCEPTED,
        )


class RunIssueListView(_PortalSyncView, ListAPIView):
    serializer_class = SyncIssueSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        run = get_object_or_404(SyncRun, pk=self.kwargs["pk"])
        qs = run.issues.select_related("resolved_by")
        if kind := self.request.query_params.get("kind"):
            qs = qs.filter(kind=kind)
        return qs


class RunConfirmMassDeleteView(_PortalSyncView):
    """Start a sync allowed past the mass delete hold for the listed types
    (AC-11). Only from the latest run, so a confirm never applies to a
    portal answer nobody has seen."""

    def post(self, request, pk):
        run = get_object_or_404(SyncRun, pk=pk)
        data_types = request.data.get("data_types")
        if not isinstance(data_types, list) or not data_types:
            raise Invalid({"data_types": "Choose the data types to confirm."})
        held = set(
            run.issues.filter(kind=SyncIssue.Kind.MASS_DELETE_HELD).values_list(
                "data_type", flat=True
            )
        )
        not_held = sorted({str(t) for t in data_types} - held)
        if not_held:
            raise Invalid(
                {"data_types": f"Not held for mass delete in this run: {', '.join(not_held)}."}
            )
        latest = SyncRun.objects.first()
        if latest.pk != run.pk:
            raise Conflict("A newer sync has run since. Confirm from the latest run.")
        connection = _connection()
        if run.connection_id != connection.pk:
            raise Conflict("This run belongs to a portal connection that was deleted since.")
        assert_uploads_unlocked()
        problem = runs.readiness_problem(connection)
        if problem:
            raise Invalid({"detail": problem})
        new = runs.start(
            connection.pk,
            user=request.user,
            trigger=SyncRun.Trigger.CONFIRM,
            allow_mass_delete=set(data_types),
        )
        set_audit(
            request,
            f"Confirmed portal sync mass delete for run #{run.pk}",
            object_type=RUN,
            object_id=run.pk,
            data_types=sorted(set(data_types)),
            new_run_id=new.pk,
        )
        return Response(
            {"run_id": new.pk, "job_id": new.job.job_id, "is_preview": new.is_preview},
            status=status.HTTP_202_ACCEPTED,
        )


class IssueResolveView(_PortalSyncView):
    """Delete or keep a record whose delete was held because it is in use
    (AC-10). Every open issue about the same record resolves with it."""

    def post(self, request, pk):
        resolution = request.data.get("resolution")
        if resolution not in SyncIssue.Resolution.values:
            raise Invalid({"resolution": "Choose delete or keep."})
        with transaction.atomic():
            issue = get_object_or_404(
                SyncIssue.objects.select_for_update().select_related("run"), pk=pk
            )
            if issue.kind != SyncIssue.Kind.DELETE_HELD_REFERENCED:
                raise Invalid({"detail": "Only a held delete can be resolved."})
            if issue.resolution:
                raise Conflict("This issue is resolved already.")
            if issue.run.is_preview:
                raise Conflict(
                    "This is a preview: nothing was synced yet. Approve it, then "
                    "resolve what the real sync holds."
                )
            if issue.run.connection_id is None:
                # Disconnecting released the record to Ordo (AC-17).
                raise Conflict(
                    "The portal was disconnected since, so this record is Ordo's "
                    "now. Edit or delete it from its own page."
                )
            if SyncRun.objects.filter(status=SyncRun.Status.RUNNING).exists():
                raise Conflict("A sync is running. Wait for it to finish.")
            if resolution == SyncIssue.Resolution.DELETE:
                assert_uploads_unlocked()
            deletes.resolve(issue, resolution)
            SyncIssue.objects.filter(
                kind=issue.kind,
                data_type=issue.data_type,
                target_id=issue.target_id,
                resolution="",
            ).update(resolution=resolution, resolved_by=request.user, resolved_at=timezone.now())
        issue.refresh_from_db()
        set_audit(
            request,
            f"Resolved held {issue.data_type} delete as {resolution}",
            object_type=ISSUE,
            object_id=issue.pk,
            run_id=issue.run_id,
            data_type=issue.data_type,
            target_id=issue.target_id,
            resolution=resolution,
        )
        return Response(SyncIssueSerializer(issue).data)
