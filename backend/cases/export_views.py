"""``GET /api/cases/export/``: the ticket queue as CSV."""

from cases.models import Case
from cases.views import case_list_queryset
from common.csv_export import (
    RecordExportView,
    iso_date,
    local_iso,
    names,
    people,
)


class CaseExportView(RecordExportView):
    filename_prefix = "tickets"

    def get_queryset(self, request):
        return (
            case_list_queryset(request.profile, request.query_params)
            .select_related("account")
            .prefetch_related("assigned_to__user", "tags")
        )

    def columns(self, request):
        return (
            ("ID", lambda c: str(c.id)),
            ("Subject", lambda c: c.name),
            ("Status", Case.get_status_display),
            ("Priority", Case.get_priority_display),
            ("Type", Case.get_case_type_display),
            ("Account", lambda c: c.account.name if c.account_id else ""),
            ("Assigned to", lambda c: people(c.assigned_to.all())),
            ("Tags", lambda c: names(c.tags.all())),
            ("Created", lambda c: local_iso(c.created_at)),
            ("First response", lambda c: local_iso(c.first_response_at)),
            ("Resolved", lambda c: local_iso(c.resolved_at)),
            ("Closed on", lambda c: iso_date(c.closed_on)),
        )
