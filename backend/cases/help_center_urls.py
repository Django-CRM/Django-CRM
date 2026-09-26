"""Public help center routes, mounted at /api/public/help/.

The slug follows the fixed prefix so `RequireOrgContext.EXEMPT_PATHS` can exempt
exactly `/api/public/help/` and nothing wider. See `cases/help_center_views.py`.
"""

from django.urls import path

from cases import help_center_views

app_name = "public_help"

urlpatterns = [
    path(
        "<str:slug>/",
        help_center_views.PublicHelpCenterListView.as_view(),
        name="list",
    ),
    path(
        "<str:slug>/articles/<uid:pk>/",
        help_center_views.PublicHelpCenterArticleView.as_view(),
        name="article",
    ),
]
