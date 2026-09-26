"""``GET /api/leads/export/``: the lead list as CSV."""

from common.csv_export import (
    RecordExportView,
    iso_date,
    local_iso,
    names,
    people,
)
from leads.models import Lead
from leads.views.lead_views import lead_list_queryset


class LeadExportView(RecordExportView):
    filename_prefix = "leads"

    def get_queryset(self, request):
        return (
            lead_list_queryset(request.profile, request.user, request.query_params)
            .select_related("created_by")
            .prefetch_related("assigned_to__user", "tags")
        )

    def columns(self, request):
        return (
            ("ID", lambda lead: str(lead.id)),
            ("First name", lambda lead: lead.first_name),
            ("Last name", lambda lead: lead.last_name),
            ("Company", lambda lead: lead.company_name),
            ("Job title", lambda lead: lead.job_title),
            ("Email", lambda lead: lead.email),
            ("Phone", lambda lead: lead.phone),
            ("Status", Lead.get_status_display),
            ("Source", Lead.get_source_display),
            ("Rating", Lead.get_rating_display),
            ("Industry", Lead.get_industry_display),
            ("Amount", lambda lead: lead.opportunity_amount),
            ("Currency", lambda lead: lead.currency),
            ("Probability", lambda lead: lead.probability),
            ("Close date", lambda lead: iso_date(lead.close_date)),
            ("Last contacted", lambda lead: iso_date(lead.last_contacted)),
            ("Next follow-up", lambda lead: iso_date(lead.next_follow_up)),
            ("City", lambda lead: lead.city),
            ("Country", Lead.get_country_display),
            ("Assigned to", lambda lead: people(lead.assigned_to.all())),
            ("Tags", lambda lead: names(lead.tags.all())),
            (
                "Created by",
                lambda lead: lead.created_by.email if lead.created_by_id else "",
            ),
            ("Created", lambda lead: local_iso(lead.created_at)),
        )
