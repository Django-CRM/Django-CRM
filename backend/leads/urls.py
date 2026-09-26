from django.urls import path

from leads.views.export_views import LeadExportView
from leads.views.import_views import (
    LeadImportCommitView,
    LeadImportPreviewView,
    LeadUploadView,
)
from leads.views.kanban_views import (
    LeadKanbanView,
    LeadMoveView,
    LeadPipelineDetailView,
    LeadPipelineListCreateView,
    LeadStageCreateView,
    LeadStageDetailView,
    LeadStageReorderView,
)
from leads.views.lead_interactions import (
    CreateLeadFromSite,
    LeadAttachmentView,
    LeadCommentView,
)
from leads.views.lead_views import LeadDetailView, LeadListView

app_name = "api_leads"

urlpatterns = [
    # Lead from external site
    path(
        "create-from-site/",
        CreateLeadFromSite.as_view(),
        name="create_lead_from_site",
    ),
    # Lead list and bulk operations
    path("", LeadListView.as_view()),
    path("export/", LeadExportView.as_view(), name="leads_export"),
    # CSV import (before <uid:pk>/, like every other fixed path here)
    path(
        "import/preview/",
        LeadImportPreviewView.as_view(),
        name="leads_import_preview",
    ),
    path("import/commit/", LeadImportCommitView.as_view(), name="leads_import_commit"),
    # Deprecated single-step alias over import/commit/; kept for API clients.
    path("upload/", LeadUploadView.as_view()),
    # Kanban endpoints
    path("kanban/", LeadKanbanView.as_view(), name="lead_kanban"),
    # Pipeline management
    path(
        "pipelines/", LeadPipelineListCreateView.as_view(), name="pipeline_list_create"
    ),
    path(
        "pipelines/<uid:pk>/", LeadPipelineDetailView.as_view(), name="pipeline_detail"
    ),
    path(
        "pipelines/<uid:pipeline_pk>/stages/",
        LeadStageCreateView.as_view(),
        name="stage_create",
    ),
    path(
        "pipelines/<uid:pipeline_pk>/stages/reorder/",
        LeadStageReorderView.as_view(),
        name="stage_reorder",
    ),
    # Stage management
    path("stages/<uid:pk>/", LeadStageDetailView.as_view(), name="stage_detail"),
    # Lead detail routes (must be after specific routes due to pk pattern)
    path("<uid:pk>/", LeadDetailView.as_view()),
    path("<uid:pk>/move/", LeadMoveView.as_view(), name="lead_move"),
    path("comment/<uid:pk>/", LeadCommentView.as_view()),
    path("attachment/<uid:pk>/", LeadAttachmentView.as_view()),
]
