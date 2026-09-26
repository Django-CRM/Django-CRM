from django.urls import path

from contacts import duplicate_views, export_views, import_views, views

app_name = "api_contacts"

urlpatterns = [
    path("", views.ContactsListView.as_view()),
    path("export/", export_views.ContactExportView.as_view(), name="contacts_export"),
    path(
        "duplicates/",
        duplicate_views.ContactDuplicateCheckView.as_view(),
        name="contacts_duplicates_check",
    ),
    # CSV import (must be before <uid:pk>/ to avoid being captured as an ID)
    path(
        "import/preview/",
        import_views.ContactImportPreviewView.as_view(),
        name="contacts_import_preview",
    ),
    path(
        "import/commit/",
        import_views.ContactImportCommitView.as_view(),
        name="contacts_import_commit",
    ),
    path("<uid:pk>/", views.ContactDetailView.as_view()),
    path(
        "<uid:pk>/duplicates/",
        duplicate_views.ContactRecordDuplicatesView.as_view(),
        name="contact_duplicates",
    ),
    path(
        "<uid:pk>/merge/",
        duplicate_views.ContactMergeView.as_view(),
        name="contact_merge",
    ),
    path("comment/<uid:pk>/", views.ContactCommentView.as_view()),
    path("attachment/<uid:pk>/", views.ContactAttachmentView.as_view()),
]
