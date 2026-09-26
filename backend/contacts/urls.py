from django.urls import path

from contacts import export_views, import_views, views

app_name = "api_contacts"

urlpatterns = [
    path("", views.ContactsListView.as_view()),
    path("export/", export_views.ContactExportView.as_view(), name="contacts_export"),
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
    path("comment/<uid:pk>/", views.ContactCommentView.as_view()),
    path("attachment/<uid:pk>/", views.ContactAttachmentView.as_view()),
]
