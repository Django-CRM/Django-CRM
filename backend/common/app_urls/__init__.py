from django.urls import include, path

from cases.csat_views import PublicCsatView
from common.search_views import GlobalSearchView
from tasks.urls import board_urlpatterns

app_name = "common_urls"
urlpatterns = [
    path("", include(("common.urls"))),
    # Org-scoped global search for the ⌘K palette (spans every module, so it
    # lives here rather than in any one app).
    path("search/", GlobalSearchView.as_view(), name="global_search"),
    path("accounts/", include("accounts.urls", namespace="api_accounts")),
    path("contacts/", include("contacts.urls", namespace="api_contacts")),
    path("leads/", include("leads.urls", namespace="api_leads")),
    path("opportunities/", include("opportunity.urls", namespace="api_opportunities")),
    # Teams URLs are now in common app at /api/teams/
    path("tasks/", include("tasks.urls", namespace="api_tasks")),
    path("cases/", include("cases.urls", namespace="api_cases")),
    path(
        "time-entries/",
        include("cases.time_entry_urls", namespace="api_time_entries"),
    ),
    path("invoices/", include("invoices.api_urls", namespace="api_invoices")),
    path(
        "boards/", include((board_urlpatterns, "api_boards"))
    ),  # Kanban Boards (merged into tasks app)
    path(
        "business-hours/",
        include("business_hours.urls", namespace="api_business_hours"),
    ),
    path("macros/", include("macros.urls", namespace="api_macros")),
    # Web form management (issue #634). The public submit and embed routes are
    # NOT here: they are anonymous and mounted at /api/public/forms/ in
    # crm/urls.py, outside this authenticated tree.
    path("webforms/", include("webforms.urls", namespace="api_webforms")),
    # Public CSAT (Tier 2 csat): anonymous, token-scoped. Lives outside
    # any app namespace because the customer reaches it from an emailed
    # link with no auth context.
    path("public/csat/<str:token>/", PublicCsatView.as_view(), name="public_csat"),
]
