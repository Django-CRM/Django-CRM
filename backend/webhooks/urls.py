from django.urls import path

from webhooks import views

app_name = "api_webhooks"

urlpatterns = [
    path("", views.WebhookListCreateView.as_view(), name="list_create"),
    path("<uid:pk>/", views.WebhookDetailView.as_view(), name="detail"),
    path("<uid:pk>/test/", views.WebhookTestView.as_view(), name="test"),
    path(
        "<uid:pk>/rotate-secret/",
        views.WebhookRotateSecretView.as_view(),
        name="rotate_secret",
    ),
    path(
        "<uid:pk>/deliveries/",
        views.WebhookDeliveryListView.as_view(),
        name="deliveries",
    ),
    path(
        "deliveries/<uid:pk>/redeliver/",
        views.WebhookRedeliverView.as_view(),
        name="redeliver",
    ),
]
