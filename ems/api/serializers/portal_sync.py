import re

from rest_framework import serializers

from ems.models import PortalConnection, PortalEndpoint, SyncIssue, SyncRun
from ems.portal_sync import fields as ordo_fields
from ems.portal_sync import secrets
from ems.portal_sync.fetch import FetchError, check_url

# An HTTP header name (RFC 9110 token).
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class EndpointSerializer(serializers.ModelSerializer):
    class Meta:
        model = PortalEndpoint
        fields = [
            "id",
            "kind",
            "url",
            "format",
            "rows_path",
            "pagination",
            "page_param",
            "next_path",
            "field_mapping",
            "is_enabled",
        ]
        read_only_fields = ["id"]
        # Uniqueness per connection is checked in ``validate``.
        validators = []

    def validate_kind(self, value):
        if self.instance and value != self.instance.kind:
            raise serializers.ValidationError("An endpoint's kind can't change.")
        return value

    def validate_url(self, value):
        try:
            check_url(value)
        except FetchError as exc:
            raise serializers.ValidationError(exc.message)
        return value

    def validate_field_mapping(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Send the mapping as {ordo_field: column}.")
        return {
            str(k): str(v).strip()
            for k, v in value.items()
            if v is not None and str(v).strip()
        }

    def validate(self, attrs):
        connection = self.context["connection"]
        merged = {
            **(
                {f: getattr(self.instance, f) for f in self.Meta.fields}
                if self.instance
                else {}
            ),
            **attrs,
        }
        kind = merged.get("kind")
        if kind == "combined" and connection.mode != "combined":
            raise serializers.ValidationError(
                {"kind": "Only a combined connection has a combined endpoint."}
            )
        if kind != "combined" and connection.mode == "combined":
            raise serializers.ValidationError(
                {"kind": "A combined connection has one combined endpoint only."}
            )
        clash = connection.endpoints.filter(kind=kind)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                {"kind": f"This connection already has a {kind} endpoint."}
            )
        if merged.get("format") == "csv":
            attrs["pagination"] = "none"
            attrs["rows_path"] = ""
        if merged.get("pagination") == "page_param" and not merged.get("page_param"):
            raise serializers.ValidationError({"page_param": "Name the page parameter."})
        if merged.get("pagination") == "next_link" and not merged.get("next_path"):
            raise serializers.ValidationError(
                {"next_path": "Give the path of the next page link."}
            )
        if "field_mapping" in attrs:
            mapping = attrs["field_mapping"]
            unknown = sorted(set(mapping) - ordo_fields.keys(kind))
            if unknown:
                raise serializers.ValidationError(
                    {"field_mapping": f"Unknown Ordo fields: {', '.join(unknown)}."}
                )
            missing = ordo_fields.unmapped_required(kind, mapping)
            if missing:
                raise serializers.ValidationError(
                    {"field_mapping": f"Map every required field: {', '.join(missing)}."}
                )
        return attrs


class ConnectionSerializer(serializers.ModelSerializer):
    # Write only: no response ever carries the secret (AC-1).
    secret = serializers.CharField(write_only=True, required=False, trim_whitespace=True)
    has_secret = serializers.SerializerMethodField()
    approved = serializers.SerializerMethodField()
    endpoints = EndpointSerializer(many=True, read_only=True)

    class Meta:
        model = PortalConnection
        fields = [
            "id",
            "mode",
            "auth_type",
            "header_name",
            "secret",
            "has_secret",
            "secret_last4",
            "approved",
            "first_sync_approved_at",
            "endpoints",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "secret_last4",
            "first_sync_approved_at",
            "created_at",
            "updated_at",
        ]

    def get_has_secret(self, obj):
        return bool(obj.secret)

    def get_approved(self, obj):
        return obj.first_sync_approved_at is not None

    def validate_secret(self, value):
        if not value or any(c in value for c in "\r\n"):
            raise serializers.ValidationError("Give the key on one line.")
        return value

    def validate(self, attrs):
        auth_type = attrs.get("auth_type", getattr(self.instance, "auth_type", None))
        header = attrs.get("header_name", getattr(self.instance, "header_name", ""))
        if auth_type == "api_key_header":
            header = (header or "").strip()
            if not _HEADER_NAME.match(header):
                raise serializers.ValidationError(
                    {"header_name": "Give the header name the portal expects, e.g. X-API-Key."}
                )
            attrs["header_name"] = header
        else:
            attrs["header_name"] = ""
        if self.instance is None and not attrs.get("secret"):
            raise serializers.ValidationError({"secret": "Give the portal's key."})
        mode = attrs.get("mode")
        if self.instance and mode and mode != self.instance.mode:
            if self.instance.endpoints.exists():
                raise serializers.ValidationError(
                    {"mode": "Remove the endpoints before changing the mode."}
                )
        return attrs

    def save(self, **kwargs):
        secret = self.validated_data.pop("secret", None)
        instance = self.instance or PortalConnection(**kwargs)
        for attr, value in self.validated_data.items():
            setattr(instance, attr, value)
        if secret:
            secrets.store(instance, secret)
        instance.save()
        self.instance = instance
        return instance


class SyncRunSerializer(serializers.ModelSerializer):
    job_id = serializers.CharField(source="job.job_id", read_only=True, default=None)
    started_by_email = serializers.CharField(
        source="started_by.email", read_only=True, default=None
    )
    issue_count = serializers.SerializerMethodField()

    class Meta:
        model = SyncRun
        fields = [
            "id",
            "connection",
            "trigger",
            "is_preview",
            "status",
            "counts",
            "error",
            "allow_mass_delete",
            "started_by_email",
            "job_id",
            "issue_count",
            "started_at",
            "finished_at",
        ]
        read_only_fields = fields

    def get_issue_count(self, obj):
        annotated = getattr(obj, "_issue_count", None)
        return annotated if annotated is not None else obj.issues.count()


class SyncIssueSerializer(serializers.ModelSerializer):
    resolved_by_email = serializers.CharField(
        source="resolved_by.email", read_only=True, default=None
    )

    class Meta:
        model = SyncIssue
        fields = [
            "id",
            "data_type",
            "kind",
            "row_ref",
            "message",
            "target_id",
            "resolution",
            "resolved_by_email",
            "resolved_at",
        ]
        read_only_fields = fields
