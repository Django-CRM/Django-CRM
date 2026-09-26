"""``GET /api/accounts/export/``: the account list as CSV."""

from accounts.models import Account
from accounts.views import account_list_queryset
from common.csv_export import RecordExportView, local_iso, names, people, yes_no


class AccountExportView(RecordExportView):
    filename_prefix = "accounts"

    def get_queryset(self, request):
        return account_list_queryset(
            request.profile, request.user, request.query_params
        ).prefetch_related("assigned_to__user", "tags")

    def columns(self, request):
        return (
            ("ID", lambda a: str(a.id)),
            ("Name", lambda a: a.name),
            ("Industry", Account.get_industry_display),
            ("Email", lambda a: a.email),
            ("Phone", lambda a: a.phone),
            ("Website", lambda a: a.website),
            ("City", lambda a: a.city),
            ("State", lambda a: a.state),
            ("Country", Account.get_country_display),
            ("Employees", lambda a: a.number_of_employees),
            ("Annual revenue", lambda a: a.annual_revenue),
            ("Currency", lambda a: a.currency),
            ("Active", lambda a: yes_no(a.is_active)),
            ("Assigned to", lambda a: people(a.assigned_to.all())),
            ("Tags", lambda a: names(a.tags.all())),
            ("Created", lambda a: local_iso(a.created_at)),
        )
