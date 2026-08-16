"""Customer portal routes, mounted at /api/portal/.

The org id sits after `login/` rather than at the front of the path, and that
ordering is load-bearing. `RequireOrgContext.EXEMPT_PATHS` is prefix-matched
with `startswith`, so `/api/portal/login/` exempts exactly the two anonymous
endpoints and nothing else. A path shaped `/api/portal/<org_id>/login/` has a
variable segment in the middle and could not be exempted without exempting
every authenticated portal endpoint alongside it. Do not reorder these.
"""

from django.urls import path

from cases import portal_views
from common.views import portal_auth_views

app_name = "portal"

urlpatterns = [
    path(
        "login/<uuid:org_id>/request/",
        portal_auth_views.PortalLoginRequestView.as_view(),
        name="login_request",
    ),
    path(
        "login/<uuid:org_id>/verify/",
        portal_auth_views.PortalLoginVerifyView.as_view(),
        name="login_verify",
    ),
    path(
        "articles/", portal_views.PortalArticleListView.as_view(), name="article_list"
    ),
    # Before `articles/<uid:pk>/`. The `uid` converter's regex is `[^/]+`, so it
    # matches the literal "suggest" and only declines it later, in `to_python`.
    # The resolver does fall through to the next pattern, so both orderings
    # happen to work, but relying on that is a trap for whoever adds the next
    # non-uuid segment here.
    path(
        "articles/suggest/",
        portal_views.PortalArticleSuggestView.as_view(),
        name="article_suggest",
    ),
    path(
        "articles/<uid:pk>/",
        portal_views.PortalArticleDetailView.as_view(),
        name="article_detail",
    ),
    path("cases/", portal_views.PortalCaseListView.as_view(), name="case_list"),
    path(
        "cases/<uid:pk>/",
        portal_views.PortalCaseDetailView.as_view(),
        name="case_detail",
    ),
    path(
        "cases/<uid:pk>/comment/",
        portal_views.PortalCaseCommentView.as_view(),
        name="case_comment",
    ),
]
