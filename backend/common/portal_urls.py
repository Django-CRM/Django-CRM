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
