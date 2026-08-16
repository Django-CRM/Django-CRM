"""URL patterns mounted at ``/api/time-entries/`` (entry-scoped + timesheet).

Case-scoped patterns live in ``cases/urls.py``.
"""

from django.urls import path

from cases import time_views

app_name = "api_time_entries"

urlpatterns = [
    # Timesheet must come before <uid:pk>/ to avoid the catchall.
    path(
        "timesheet/",
        time_views.TimesheetView.as_view(),
        name="time_entries_timesheet",
    ),
    path(
        "report/",
        time_views.TimeReportView.as_view(),
        name="time_entries_report",
    ),
    path(
        "report/export/",
        time_views.TimeReportExportView.as_view(),
        name="time_entries_report_export",
    ),
    path(
        "unbilled/",
        time_views.UnbilledEntriesView.as_view(),
        name="time_entries_unbilled",
    ),
    path(
        "<uid:pk>/stop/",
        time_views.TimeEntryStopView.as_view(),
        name="time_entry_stop",
    ),
    path(
        "<uid:pk>/",
        time_views.TimeEntryDetailView.as_view(),
        name="time_entry_detail",
    ),
]
