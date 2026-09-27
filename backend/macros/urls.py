from django.urls import path

from macros import views

app_name = "api_macros"

urlpatterns = [
    path("", views.MacroListCreateView.as_view(), name="list_create"),
    path("<uid:pk>/", views.MacroDetailView.as_view(), name="detail"),
    path("<uid:pk>/render/", views.MacroRenderView.as_view(), name="render"),
    path("<uid:pk>/apply/", views.MacroApplyView.as_view(), name="apply"),
]
