from django.urls import path

from opportunity.views.aging_views import StageAgingConfigView
from opportunity.views.goal_views import (
    SalesGoalDetailView,
    SalesGoalHistoryView,
    SalesGoalLeaderboardView,
    SalesGoalListView,
)
from opportunity.views.kanban_views import (
    OpportunityKanbanView,
    OpportunityMoveView,
)
from opportunity.views.line_item_views import (
    OpportunityLineItemDetailView,
    OpportunityLineItemListView,
)
from opportunity.views.opportunity_interactions import (
    OpportunityAttachmentView,
    OpportunityCommentView,
)
from opportunity.views.opportunity_views import (
    OpportunityDetailView,
    OpportunityListView,
)

app_name = "api_opportunities"

urlpatterns = [
    path("", OpportunityListView.as_view()),
    path("kanban/", OpportunityKanbanView.as_view()),
    path("aging-config/", StageAgingConfigView.as_view()),
    path("goals/", SalesGoalListView.as_view()),
    path("goals/leaderboard/", SalesGoalLeaderboardView.as_view()),
    path("goals/history/", SalesGoalHistoryView.as_view()),
    path("goals/<uid:pk>/", SalesGoalDetailView.as_view()),
    path("<uid:pk>/", OpportunityDetailView.as_view()),
    path("<uid:pk>/move/", OpportunityMoveView.as_view()),
    path("comment/<uid:pk>/", OpportunityCommentView.as_view()),
    path("attachment/<uid:pk>/", OpportunityAttachmentView.as_view()),
    # Line items
    path(
        "<uid:opportunity_id>/line-items/",
        OpportunityLineItemListView.as_view(),
        name="opportunity-line-items-list",
    ),
    path(
        "<uid:opportunity_id>/line-items/<uid:line_item_id>/",
        OpportunityLineItemDetailView.as_view(),
        name="opportunity-line-items-detail",
    ),
]
