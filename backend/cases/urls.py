from django.urls import path

from cases import (
    analytics_views,
    approval_views,
    bulk_views,
    csat_views,
    escalation_views,
    export_views,
    import_views,
    inbound_views,
    kanban_views,
    kb_views,
    merge_views,
    parent_views,
    routing_views,
    solution_views,
    time_views,
    unmerge_views,
    views,
    watcher_views,
)

app_name = "api_cases"

urlpatterns = [
    # Cases list endpoint
    path("", views.CaseListView.as_view()),
    path("export/", export_views.CaseExportView.as_view(), name="cases_export"),
    # Kanban endpoints (must be before <uid:pk>/ to avoid conflicts)
    path("kanban/", kanban_views.CaseKanbanView.as_view(), name="case_kanban"),
    # Pipeline management
    path(
        "pipelines/",
        kanban_views.CasePipelineListCreateView.as_view(),
        name="pipeline_list_create",
    ),
    path(
        "pipelines/<uid:pk>/",
        kanban_views.CasePipelineDetailView.as_view(),
        name="pipeline_detail",
    ),
    path(
        "pipelines/<uid:pipeline_pk>/stages/",
        kanban_views.CaseStageCreateView.as_view(),
        name="stage_create",
    ),
    path(
        "pipelines/<uid:pipeline_pk>/stages/reorder/",
        kanban_views.CaseStageReorderView.as_view(),
        name="stage_reorder",
    ),
    # Stage management
    path(
        "stages/<uid:pk>/",
        kanban_views.CaseStageDetailView.as_view(),
        name="stage_detail",
    ),
    # Solutions (Knowledge Base) endpoints (must be before <uid:pk>/ to avoid conflicts)
    path(
        "solutions/", solution_views.SolutionListView.as_view(), name="solutions_list"
    ),
    path(
        "solutions/<uid:pk>/",
        solution_views.SolutionDetailView.as_view(),
        name="solution_detail",
    ),
    path(
        "solutions/<uid:pk>/publish/",
        solution_views.SolutionPublishView.as_view(),
        name="solution_publish",
    ),
    path(
        "solutions/<uid:pk>/unpublish/",
        solution_views.SolutionUnpublishView.as_view(),
        name="solution_unpublish",
    ),
    # Reopen policy (admin singleton, must be before <uid:pk>/ patterns)
    path(
        "reopen-policy/",
        views.ReopenPolicyView.as_view(),
        name="reopen_policy",
    ),
    # Escalation policies (admin CRUD, must be before <uid:pk>/ patterns)
    path(
        "escalation-policies/",
        escalation_views.EscalationPolicyListCreateView.as_view(),
        name="escalation_policy_list_create",
    ),
    path(
        "escalation-policies/<uid:pk>/",
        escalation_views.EscalationPolicyDetailView.as_view(),
        name="escalation_policy_detail",
    ),
    # Routing rules (admin CRUD + dry-run test endpoint, must be before <uid:pk>/ patterns)
    path(
        "routing-rules/",
        routing_views.RoutingRuleListCreateView.as_view(),
        name="routing_rule_list_create",
    ),
    path(
        "routing-rules/<uid:pk>/",
        routing_views.RoutingRuleDetailView.as_view(),
        name="routing_rule_detail",
    ),
    path(
        "routing-rules/<uid:pk>/test/",
        routing_views.RoutingRuleTestView.as_view(),
        name="routing_rule_test",
    ),
    # Inbound email: public webhook (SNS-signed) + admin mailbox CRUD.
    path(
        "inbound/<uid:mailbox_id>/",
        inbound_views.InboundMailboxWebhookView.as_view(),
        name="inbound_webhook",
    ),
    path(
        "mailboxes/",
        inbound_views.InboundMailboxListCreateView.as_view(),
        name="inbound_mailbox_list_create",
    ),
    path(
        "mailboxes/<uid:pk>/",
        inbound_views.InboundMailboxDetailView.as_view(),
        name="inbound_mailbox_detail",
    ),
    # Watching list (must be before <uid:pk>/ patterns)
    path(
        "watching/",
        watcher_views.WatchingListView.as_view(),
        name="cases_watching",
    ),
    # Analytics dashboards (must be before <uid:pk>/ patterns)
    path(
        "analytics/frt/",
        analytics_views.AnalyticsFrtView.as_view(),
        name="analytics_frt",
    ),
    path(
        "analytics/nrt/",
        analytics_views.AnalyticsNrtView.as_view(),
        name="analytics_nrt",
    ),
    path(
        "analytics/mttr/",
        analytics_views.AnalyticsMttrView.as_view(),
        name="analytics_mttr",
    ),
    path(
        "analytics/backlog/",
        analytics_views.AnalyticsBacklogView.as_view(),
        name="analytics_backlog",
    ),
    path(
        "analytics/agents/",
        analytics_views.AnalyticsAgentsView.as_view(),
        name="analytics_agents",
    ),
    path(
        "analytics/sla/",
        analytics_views.AnalyticsSlaView.as_view(),
        name="analytics_sla",
    ),
    path(
        "analytics/service/",
        analytics_views.AnalyticsServiceView.as_view(),
        name="analytics_service",
    ),
    path(
        "analytics/drilldown/",
        analytics_views.AnalyticsDrilldownView.as_view(),
        name="analytics_drilldown",
    ),
    path(
        "analytics/export/",
        analytics_views.AnalyticsExportView.as_view(),
        name="analytics_export",
    ),
    # CSAT aggregate (must be before <uid:pk>/ patterns)
    path(
        "csat/aggregate/",
        csat_views.CsatAggregateView.as_view(),
        name="csat_aggregate",
    ),
    # Bulk endpoints (must be before <uid:pk>/ patterns)
    path(
        "bulk/update/",
        bulk_views.BulkUpdateCasesView.as_view(),
        name="cases_bulk_update",
    ),
    path(
        "bulk/delete/",
        bulk_views.BulkDeleteCasesView.as_view(),
        name="cases_bulk_delete",
    ),
    # CSV import (must be before <uid:pk>/ patterns)
    path(
        "import/preview/",
        import_views.CaseImportPreviewView.as_view(),
        name="cases_import_preview",
    ),
    path(
        "import/commit/",
        import_views.CaseImportCommitView.as_view(),
        name="cases_import_commit",
    ),
    # Case ↔ Solution linking (M2M endpoints, must be before <uid:pk>/ patterns)
    path(
        "<uid:pk>/solutions/",
        views.CaseSolutionLinkView.as_view(),
        name="case_solution_link",
    ),
    path(
        "<uid:pk>/solutions/<uid:solution_pk>/",
        views.CaseSolutionLinkView.as_view(),
        name="case_solution_unlink",
    ),
    # Audit-log feed (must be before <uid:pk>/ pattern)
    path(
        "<uid:pk>/activities/",
        views.CaseActivityListView.as_view(),
        name="case_activities",
    ),
    # KB suggester typeahead (must be before <uid:pk>/ pattern)
    path(
        "<uid:pk>/solution-suggestions/",
        kb_views.SolutionSuggestionsView.as_view(),
        name="case_solution_suggestions",
    ),
    # Watch toggle + watchers list (must be before <uid:pk>/ pattern)
    path(
        "<uid:pk>/watch/",
        watcher_views.WatchView.as_view(),
        name="case_watch",
    ),
    path(
        "<uid:pk>/watchers/",
        watcher_views.WatchersListView.as_view(),
        name="case_watchers",
    ),
    # Merge endpoint (must be before <uid:pk>/ pattern)
    path(
        "<uid:pk>/merge/<uid:into_id>/",
        merge_views.CaseMergeView.as_view(),
        name="case_merge",
    ),
    path(
        "<uid:pk>/merge-targets/",
        merge_views.CaseMergeTargetsView.as_view(),
        name="case_merge_targets",
    ),
    path(
        "<uid:pk>/unmerge/",
        unmerge_views.CaseUnmergeView.as_view(),
        name="case_unmerge",
    ),
    # Parent/child tree endpoints (must be before <uid:pk>/ pattern)
    path(
        "<uid:pk>/tree/",
        parent_views.CaseTreeView.as_view(),
        name="case_tree",
    ),
    path(
        "<uid:pk>/link/",
        parent_views.CaseLinkParentView.as_view(),
        name="case_link_parent",
    ),
    path(
        "<uid:pk>/close-with-children/",
        parent_views.CaseCloseWithChildrenView.as_view(),
        name="case_close_with_children",
    ),
    # Time-tracking endpoints (must be before <uid:pk>/ catchall)
    path(
        "<uid:pk>/time-entries/",
        time_views.TimeEntryListCreateView.as_view(),
        name="case_time_entries",
    ),
    path(
        "<uid:pk>/time-entries/start/",
        time_views.TimeEntryStartView.as_view(),
        name="case_time_entry_start",
    ),
    path(
        "<uid:pk>/time-summary/",
        time_views.TimeSummaryView.as_view(),
        name="case_time_summary",
    ),
    # Approval workflows (must be before <uid:pk>/ catchall), Tier 3 approvals.
    path(
        "approval-rules/",
        approval_views.ApprovalRuleListCreateView.as_view(),
        name="approval_rule_list_create",
    ),
    path(
        "approval-rules/<uid:pk>/",
        approval_views.ApprovalRuleDetailView.as_view(),
        name="approval_rule_detail",
    ),
    path(
        "approvals/",
        approval_views.ApprovalInboxView.as_view(),
        name="approval_inbox",
    ),
    path(
        "approvals/<uid:pk>/approve/",
        approval_views.ApprovalApproveView.as_view(),
        name="approval_approve",
    ),
    path(
        "approvals/<uid:pk>/reject/",
        approval_views.ApprovalRejectView.as_view(),
        name="approval_reject",
    ),
    path(
        "approvals/<uid:pk>/cancel/",
        approval_views.ApprovalCancelView.as_view(),
        name="approval_cancel",
    ),
    path(
        "<uid:pk>/request-approval/",
        approval_views.CaseRequestApprovalView.as_view(),
        name="case_request_approval",
    ),
    # Case detail routes (must be after all named routes due to <uid:pk> pattern)
    path("<uid:pk>/", views.CaseDetailView.as_view()),
    path("<uid:pk>/move/", kanban_views.CaseMoveView.as_view(), name="case_move"),
    path("comment/<uid:pk>/", views.CaseCommentView.as_view()),
    path("attachment/<uid:pk>/", views.CaseAttachmentView.as_view()),
]
