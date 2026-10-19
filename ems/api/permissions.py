from rest_framework import permissions

from ems.models import SystemSettings


class IsAdminStaff(permissions.BasePermission):
    """Mirror the existing @admin_required decorator: requires user.is_staff."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_staff)


class IsSuperAdmin(permissions.BasePermission):
    """Super-admin-only: users/roles, generation, settings, constraints, reset."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_super_admin)


class IsDataOfficer(permissions.BasePermission):
    """Super admins and data officers: validate/upload and manage data entities."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.can_manage_data)


class IsFacultyOfficer(permissions.BasePermission):
    """Super admins and faculty officers: manage faculties."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.can_manage_faculties)


class CanManageDepartmentScoped(permissions.BasePermission):
    """Data managers (super admin / data officer) always pass. Department
    officers with a department may write only to objects whose
    ``department_id`` matches their own. Read-only methods are open to any
    authenticated user (queryset filtering happens in the viewset)."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        if user.can_manage_data:
            return True
        return bool(user.department_id)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        if user.can_manage_data:
            return True
        return getattr(obj, "department_id", None) == user.department_id


class IsJobOwnerOrAdmin(permissions.BasePermission):
    """Admins see every BackgroundJob; everyone else sees only what they created."""

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return user.is_staff or obj.created_by_id == user.id


class UploadsUnlocked(permissions.BasePermission):
    """Block uploads once a timetable has been generated (matches existing logic)."""

    message = (
        "Uploads are locked because a timetable already exists. "
        "Reset the system or enable bulk upload to continue."
    )

    def has_permission(self, request, view):
        if request.method.upper() in ("GET", "HEAD", "OPTIONS"):
            return True
        settings_row = SystemSettings.objects.first()
        if settings_row and settings_row.has_timetable:
            return False
        return True



class NotPortalManaged(permissions.BasePermission):
    """403 ``portal_managed`` for writes the school portal owns (spec 0005,
    AC-8): a CSV upload or a create of a data type it syncs, and deleting a
    record it synced. The view names the type with
    ``portal_data_type(request)``; None (halls, edits) is never refused here.
    Edits to portal owned fields are refused by the serializers."""

    def has_permission(self, request, view):
        if request.method.upper() in ("GET", "HEAD", "OPTIONS"):
            return True
        data_type = view.portal_data_type(request)
        if data_type:
            from ems.portal_sync import managed

            action = "added by hand" if getattr(view, "action", None) else "uploaded from CSV"
            managed.assert_type_unsynced(data_type, action=action)
        return True

    def has_object_permission(self, request, view, obj):
        if getattr(view, "action", None) == "destroy":
            from ems.portal_sync import managed

            managed.assert_deletable(obj)
        return True


class PortalManagedViewSetMixin:
    """Applies ``NotPortalManaged`` to an entity viewset. Set ``portal_type``
    to the data type its records are."""

    portal_type = ""

    # The viewsets pick their permissions per action, so this runs after
    # whichever they pick rather than joining ``get_permissions``.
    def check_permissions(self, request):
        super().check_permissions(request)
        NotPortalManaged().has_permission(request, self)

    def check_object_permissions(self, request, obj):
        super().check_object_permissions(request, obj)
        NotPortalManaged().has_object_permission(request, self, obj)

    def portal_data_type(self, request):
        return self.portal_type if self.action == "create" else None
