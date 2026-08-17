from django.urls import path

from webforms import views

app_name = "api_webforms"

urlpatterns = [
    path("", views.WebFormListCreateView.as_view(), name="list_create"),
    path("<uid:pk>/", views.WebFormDetailView.as_view(), name="detail"),
    path("<uid:pk>/publish/", views.WebFormPublishView.as_view(), name="publish"),
    path(
        "<uid:pk>/unpublish/",
        views.WebFormUnpublishView.as_view(),
        name="unpublish",
    ),
    path(
        "<uid:pk>/submissions/",
        views.WebFormSubmissionListView.as_view(),
        name="submissions",
    ),
    path(
        "<uid:pk>/analytics/",
        views.WebFormAnalyticsView.as_view(),
        name="analytics",
    ),
]
