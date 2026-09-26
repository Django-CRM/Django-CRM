"""``GET /api/opportunities/export/``: the deal list as CSV."""

from common.csv_export import (
    RecordExportView,
    iso_date,
    local_iso,
    names,
    people,
)
from common.money import org_currency
from opportunity.models import Opportunity
from opportunity.stages import stage_index
from opportunity.views.opportunity_views import deal_list_queryset


class DealExportView(RecordExportView):
    filename_prefix = "deals"

    def get_queryset(self, request):
        return (
            deal_list_queryset(request.profile, request.user, request.query_params)
            .select_related("account", "pipeline")
            .prefetch_related("assigned_to__user", "tags")
        )

    def columns(self, request):
        # A stage code means something only inside its pipeline, so the label
        # is looked up by both, from one read of the org's stages.
        stages = stage_index(request.profile.org_id)
        default_currency = org_currency(request.profile.org)

        def stage_label(deal):
            stage = deal.current_stage(stages)
            return stage.label if stage else deal.stage

        return (
            ("ID", lambda d: str(d.id)),
            ("Name", lambda d: d.name),
            ("Account", lambda d: d.account.name if d.account_id else ""),
            ("Pipeline", lambda d: d.pipeline.name),
            ("Stage", stage_label),
            ("Amount", lambda d: d.amount),
            # A deal with no currency counts in the org's, as the list totals do.
            ("Currency", lambda d: d.currency or default_currency),
            ("Probability", lambda d: d.probability),
            ("Close date", lambda d: iso_date(d.closed_on)),
            ("Type", Opportunity.get_opportunity_type_display),
            ("Lead source", Opportunity.get_lead_source_display),
            ("Assigned to", lambda d: people(d.assigned_to.all())),
            ("Tags", lambda d: names(d.tags.all())),
            ("Created", lambda d: local_iso(d.created_at)),
        )
