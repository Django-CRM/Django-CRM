"""``GET /api/invoices/export/``: the invoice list as CSV."""

from common.csv_export import RecordExportView, iso_date, local_iso, people
from invoices.api_views import filter_invoices
from invoices.models import Invoice
from invoices.permissions import visible_invoices_qs


class InvoiceExportView(RecordExportView):
    filename_prefix = "invoices"

    def get_queryset(self, request):
        return (
            filter_invoices(
                visible_invoices_qs(request.profile, request.user), request.query_params
            )
            .select_related("account", "contact")
            .prefetch_related("assigned_to__user")
        )

    def columns(self, request):
        # The columns `InvoiceListSerializer` shows, plus the assignees. Never
        # `public_token`: it is the credential behind the invoice's public link.
        return (
            ("ID", lambda i: str(i.id)),
            ("Number", lambda i: i.invoice_number),
            ("Title", lambda i: i.invoice_title),
            ("Status", Invoice.get_status_display),
            ("Account", lambda i: i.account.name if i.account_id else ""),
            (
                "Contact",
                lambda i: (
                    f"{i.contact.first_name} {i.contact.last_name}".strip()
                    if i.contact_id
                    else ""
                ),
            ),
            ("Client name", lambda i: i.client_name),
            ("Client email", lambda i: i.client_email),
            ("Issue date", lambda i: iso_date(i.issue_date)),
            ("Due date", lambda i: iso_date(i.due_date)),
            ("Total", lambda i: i.total_amount),
            ("Paid", lambda i: i.amount_paid),
            ("Due", lambda i: i.amount_due),
            ("Currency", lambda i: i.currency),
            ("Assigned to", lambda i: people(i.assigned_to.all())),
            ("Created", lambda i: local_iso(i.created_at)),
        )
