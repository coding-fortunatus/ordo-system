from django.urls import include, path

from ems.api.routers import router
from ems.api.views.auth import LoginView, LogoutView, MeView
from ems.api.views.constraints import GenerationConstraintsView
from ems.api.views.system import (
    BrandingView,
    DashboardStatsView,
    EnableBulkUploadView,
    ResetSystemView,
    SystemSettingsView,
)
from ems.api.views.uploads import (
    BulkUploadView,
    ClassCoursesUploadView,
    ClassStudentsUploadView,
    ClassesForDepartmentUploadView,
    CourseUploadView,
    DepartmentUploadView,
    HallUploadView,
)
from ems.api.views.jobs import (
    ClearAllocationView,
    ClearDistributionView,
    GenerateAllocationAllView,
    GenerateAllocationView,
    GenerateDistributionAllView,
    GenerateDistributionView,
    GenerateTimetableView,
    ManualSeatAssignmentView,
)
from ems.api.views.scheduling import (
    AllocationListView,
    DistributionListView,
    DistributionStatisticsView,
    HallAllocationView,
    ReadinessView,
    TimetableDatesView,
    TimetableEstimateView,
    TimetableListView,
)
from ems.api.views.exports import (
    ArrangementExportView,
    AttendanceSheetsView,
    BulkAttendanceSheetsView,
    BroadsheetView,
    DistributionExportView,
    TimetableExportView,
)
from ems.api.views.publication import (
    PublishTimetableView,
    TimetablePublicationView,
    UnpublishTimetableView,
)
from ems.api.views.directory import DirectoryExportView, DirectoryPreviewView
from ems.api.views.portal_sync import (
    ConnectionView,
    EndpointDetailView,
    EndpointListView,
    EndpointTestView,
    FieldsView,
    IssueResolveView,
    RunApproveView,
    RunConfirmMassDeleteView,
    RunDetailView,
    RunIssueListView,
    RunListView,
)
from ems.api.views.school import SchoolStatusView, SchoolView
from ems.api.views.support import SupportEndView, SupportRedeemView
from ems.api.views.invites import (
    InviteAcceptView,
    InviteDetailView,
    InviteListView,
    InviteResendView,
    InviteRevokeView,
)

auth_patterns = [
    path("login/", LoginView.as_view(), name="api-login"),
    path("logout/", LogoutView.as_view(), name="api-logout"),
    path("me/", MeView.as_view(), name="api-me"),
]

system_patterns = [
    path("settings/", SystemSettingsView.as_view(), name="api-settings"),
    path("branding/", BrandingView.as_view(), name="api-branding"),
    path("dashboard/", DashboardStatsView.as_view(), name="api-dashboard"),
    path("reset/", ResetSystemView.as_view(), name="api-reset"),
    path(
        "enable-bulk-upload/",
        EnableBulkUploadView.as_view(),
        name="api-enable-bulk-upload",
    ),
    path(
        "constraints/",
        GenerationConstraintsView.as_view(),
        name="api-constraints",
    ),
]


upload_patterns = [
    path("departments/", DepartmentUploadView.as_view(), name="api-upload-departments"),
    path("halls/", HallUploadView.as_view(), name="api-upload-halls"),
    path("courses/", CourseUploadView.as_view(), name="api-upload-courses"),
    path(
        "classes/<slug:dept_slug>/",
        ClassesForDepartmentUploadView.as_view(),
        name="api-upload-classes",
    ),
    path(
        "class-courses/<int:class_id>/",
        ClassCoursesUploadView.as_view(),
        name="api-upload-class-courses",
    ),
    path(
        "class-students/<int:class_id>/",
        ClassStudentsUploadView.as_view(),
        name="api-upload-class-students",
    ),
    path("bulk/", BulkUploadView.as_view(), name="api-upload-bulk"),
]


scheduling_patterns = [
    path("readiness/", ReadinessView.as_view(), name="api-readiness"),
    path("timetable/", TimetableListView.as_view(), name="api-timetable"),
    path(
        "timetable/dates/",
        TimetableDatesView.as_view(),
        name="api-timetable-dates",
    ),
    path(
        "timetable/estimate/",
        TimetableEstimateView.as_view(),
        name="api-timetable-estimate",
    ),
    path(
        "timetable/generate/",
        GenerateTimetableView.as_view(),
        name="api-generate-timetable",
    ),
    path(
        "timetable/publication/",
        TimetablePublicationView.as_view(),
        name="api-timetable-publication",
    ),
    path(
        "timetable/publish/",
        PublishTimetableView.as_view(),
        name="api-publish-timetable",
    ),
    path(
        "timetable/unpublish/",
        UnpublishTimetableView.as_view(),
        name="api-unpublish-timetable",
    ),
    path("distribution/", DistributionListView.as_view(), name="api-distribution"),
    path(
        "distribution/statistics/",
        DistributionStatisticsView.as_view(),
        name="api-distribution-statistics",
    ),
    path(
        "distribution/generate/",
        GenerateDistributionView.as_view(),
        name="api-generate-distribution",
    ),
    path(
        "distribution/clear/",
        ClearDistributionView.as_view(),
        name="api-clear-distribution",
    ),
    path(
        "distribution/generate-all/",
        GenerateDistributionAllView.as_view(),
        name="api-generate-distribution-all",
    ),
    path("allocation/", AllocationListView.as_view(), name="api-allocation"),
    path(
        "allocation/hall/",
        HallAllocationView.as_view(),
        name="api-allocation-hall",
    ),
    path(
        "allocation/generate/",
        GenerateAllocationView.as_view(),
        name="api-generate-allocation",
    ),
    path(
        "allocation/clear/",
        ClearAllocationView.as_view(),
        name="api-clear-allocation",
    ),
    path(
        "allocation/generate-all/",
        GenerateAllocationAllView.as_view(),
        name="api-generate-allocation-all",
    ),
    path(
        "allocation/manual-assign/",
        ManualSeatAssignmentView.as_view(),
        name="api-allocation-manual-assign",
    ),
]


export_patterns = [
    path("timetable/", TimetableExportView.as_view(), name="api-export-timetable"),
    path(
        "distribution/",
        DistributionExportView.as_view(),
        name="api-export-distribution",
    ),
    path(
        "arrangement/",
        ArrangementExportView.as_view(),
        name="api-export-arrangement",
    ),
    path(
        "attendance-sheets/",
        AttendanceSheetsView.as_view(),
        name="api-export-attendance-sheets",
    ),
    path(
        "attendance-sheets/bulk/",
        BulkAttendanceSheetsView.as_view(),
        name="api-export-attendance-sheets-bulk",
    ),
    path("broadsheet/", BroadsheetView.as_view(), name="api-export-broadsheet"),
]

directory_patterns = [
    path("preview/", DirectoryPreviewView.as_view(), name="api-directory-preview"),
    path("export/", DirectoryExportView.as_view(), name="api-directory-export"),
]

invite_patterns = [
    path("", InviteListView.as_view(), name="api-invites"),
    path("<int:pk>/revoke/", InviteRevokeView.as_view(), name="api-invite-revoke"),
    path("<int:pk>/resend/", InviteResendView.as_view(), name="api-invite-resend"),
    path("<str:token>/", InviteDetailView.as_view(), name="api-invite-detail"),
    path("<str:token>/accept/", InviteAcceptView.as_view(), name="api-invite-accept"),
]

portal_sync_patterns = [
    path("connection/", ConnectionView.as_view(), name="api-portal-connection"),
    path("fields/", FieldsView.as_view(), name="api-portal-fields"),
    path("endpoints/", EndpointListView.as_view(), name="api-portal-endpoints"),
    path(
        "endpoints/<int:pk>/",
        EndpointDetailView.as_view(),
        name="api-portal-endpoint",
    ),
    path(
        "endpoints/<int:pk>/test/",
        EndpointTestView.as_view(),
        name="api-portal-endpoint-test",
    ),
    path("runs/", RunListView.as_view(), name="api-portal-runs"),
    path("runs/<int:pk>/", RunDetailView.as_view(), name="api-portal-run"),
    path(
        "runs/<int:pk>/approve/",
        RunApproveView.as_view(),
        name="api-portal-run-approve",
    ),
    path(
        "runs/<int:pk>/issues/",
        RunIssueListView.as_view(),
        name="api-portal-run-issues",
    ),
    path(
        "runs/<int:pk>/confirm-mass-delete/",
        RunConfirmMassDeleteView.as_view(),
        name="api-portal-run-confirm-mass-delete",
    ),
    path(
        "issues/<int:pk>/resolve/",
        IssueResolveView.as_view(),
        name="api-portal-issue-resolve",
    ),
]

urlpatterns = [
    path("auth/", include(auth_patterns)),
    path("school/", SchoolView.as_view(), name="api-school"),
    path("school/status/", SchoolStatusView.as_view(), name="api-school-status"),
    path("support/redeem/", SupportRedeemView.as_view(), name="api-support-redeem"),
    path("support/end/", SupportEndView.as_view(), name="api-support-end"),
    path("invites/", include(invite_patterns)),
    path("system/", include(system_patterns)),
    path("uploads/", include(upload_patterns)),
    path("exports/", include(export_patterns)),
    path("directory/", include(directory_patterns)),
    path("portal-sync/", include(portal_sync_patterns)),
    path("public/", include("ems.api.public_urls")),
    path("", include(scheduling_patterns)),
    path("", include(router.urls)),
]
