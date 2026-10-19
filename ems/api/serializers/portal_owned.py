from rest_framework import serializers

from ems.portal_sync import managed


class PortalOwnedMixin(serializers.Serializer):
    """Shows whether a record came from the school portal and which of its
    fields the portal owns, and refuses edits to those fields (spec 0005,
    AC-8). List it before ``ModelSerializer`` and add both fields to
    ``Meta.fields``."""

    from_portal = serializers.SerializerMethodField()
    portal_locked = serializers.SerializerMethodField()

    def get_from_portal(self, obj):
        return managed.from_portal(obj, self.context)

    def get_portal_locked(self, obj):
        return managed.locked_fields(obj, self.context)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance is not None:
            managed.assert_editable(self.instance, attrs, self.context)
        return attrs
