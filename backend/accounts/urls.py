from django.urls import path

from accounts import duplicate_views, export_views, views

app_name = "api_accounts"

urlpatterns = [
    path("", views.AccountsListView.as_view()),
    path("export/", export_views.AccountExportView.as_view(), name="accounts_export"),
    path(
        "duplicates/",
        duplicate_views.AccountDuplicateCheckView.as_view(),
        name="accounts_duplicates_check",
    ),
    path("<uid:pk>/", views.AccountDetailView.as_view()),
    path("<uid:pk>/create_mail/", views.AccountCreateMailView.as_view()),
    path(
        "<uid:pk>/duplicates/",
        duplicate_views.AccountRecordDuplicatesView.as_view(),
        name="account_duplicates",
    ),
    path(
        "<uid:pk>/merge/",
        duplicate_views.AccountMergeView.as_view(),
        name="account_merge",
    ),
    path("comment/<uid:pk>/", views.AccountCommentView.as_view()),
    path("attachment/<uid:pk>/", views.AccountAttachmentView.as_view()),
]
