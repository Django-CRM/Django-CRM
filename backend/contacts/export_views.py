"""``GET /api/contacts/export/``: the contact list as CSV."""

from common.csv_export import RecordExportView, local_iso, names, people, yes_no
from contacts.models import Contact
from contacts.views import contact_list_queryset


class ContactExportView(RecordExportView):
    filename_prefix = "contacts"

    def get_queryset(self, request):
        _matching, rows = contact_list_queryset(request.profile, request.query_params)
        return rows.select_related("account").prefetch_related(
            "account_contacts", "assigned_to__user", "tags"
        )

    def columns(self, request):
        return (
            ("ID", lambda c: str(c.id)),
            ("First name", lambda c: c.first_name),
            ("Last name", lambda c: c.last_name),
            ("Email", lambda c: c.email),
            ("Phone", lambda c: c.phone),
            ("Title", lambda c: c.title),
            ("Department", lambda c: c.department),
            ("Organization", lambda c: c.organization),
            # Both account links, as `ContactSerializer` publishes both: the
            # "primary" FK and the `Account.contacts` membership that in
            # practice carries the data.
            ("Account", lambda c: c.account.name if c.account_id else ""),
            ("Linked accounts", lambda c: names(c.account_contacts.all())),
            ("City", lambda c: c.city),
            ("State", lambda c: c.state),
            ("Country", Contact.get_country_display),
            ("Do not call", lambda c: yes_no(c.do_not_call)),
            ("Active", lambda c: yes_no(c.is_active)),
            ("Assigned to", lambda c: people(c.assigned_to.all())),
            ("Tags", lambda c: names(c.tags.all())),
            ("Created", lambda c: local_iso(c.created_at)),
        )
