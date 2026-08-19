from django.urls import path

from leads.views.acquisition_views import (
    AttributionListCreateView,
    CampaignListCreateView,
    DataSubjectRequestActionView,
    DataSubjectRequestListCreateView,
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
    LeadUploadView,
)
from leads.views.lead_views import LeadDetailView, LeadListView

app_name = "api_leads"

urlpatterns = [
    path("campaigns/", CampaignListCreateView.as_view(), name="campaigns"),
    path("attribution/", AttributionListCreateView.as_view(), name="attribution"),
    path(
        "privacy-requests/",
        DataSubjectRequestListCreateView.as_view(),
        name="privacy_requests",
    ),
    path(
        "privacy-requests/<uuid:pk>/actions/",
        DataSubjectRequestActionView.as_view(),
        name="privacy_request_actions",
    ),
    # Lead from external site
    path(
        "create-from-site/",
        CreateLeadFromSite.as_view(),
        name="create_lead_from_site",
    ),
    # Lead list and bulk operations
    path("", LeadListView.as_view()),
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
