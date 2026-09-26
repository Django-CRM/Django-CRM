from django.urls import path

from accounts import export_views, views

app_name = "api_accounts"

urlpatterns = [
    path("", views.AccountsListView.as_view()),
    path("export/", export_views.AccountExportView.as_view(), name="accounts_export"),
    path("<uid:pk>/", views.AccountDetailView.as_view()),
    path("<uid:pk>/create_mail/", views.AccountCreateMailView.as_view()),
    path("comment/<uid:pk>/", views.AccountCommentView.as_view()),
    path("attachment/<uid:pk>/", views.AccountAttachmentView.as_view()),
]
