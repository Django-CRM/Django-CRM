"""One path for editing a lead that already exists.

The lead PATCH (`LeadDetailView.patch`) and automation rules both write
through `update_lead`, so a gate added here holds for both, as
`cases.updates.update_case` does for tickets. Conversion is not an edit: it
creates an account, a contact and a deal, so it stays in the view, which calls
`update_lead` only for a body that does not convert.

Authorization is the caller's job and happens first: the view looks the lead
up through `leads.access.visible_leads_qs`.
"""

import json

from django.db import transaction

from common.custom_fields import validate_payload as validate_custom_fields_payload
from common.models import Profile, Tags, Teams
from common.validators import payload_id_list
from contacts.access import replace_visible_contacts
from leads.alerts import assigned_by
from leads.serializer import LeadCreateSerializer
from leads.tasks import send_email_to_assigned_user

CONVERSION_REFUSED = "A lead is converted from its page, not by an edit."


def converts(data):
    """Whether a write with this body converts the lead.

    PATCH also takes `is_converted`.
    """
    return data.get("status") == "converted" or bool(data.get("is_converted"))


def update_lead(request, lead, data):
    """Apply ``data`` to ``lead`` as a partial update.

    Returns ``None`` once saved, or the error dict a 400 carries, before
    anything is written. A body that would convert the lead is refused. Only
    the keys present in ``data`` change; ``assigned_to`` replaces, and the
    email to whoever it newly assigns is queued once the transaction commits,
    as in `cases.updates.notify_newly_assigned`. The lead alert
    (`leads.alerts`) skips ``request.profile``. A malformed id in ``tags``,
    ``contacts``, ``teams`` or ``assigned_to`` raises DRF's ``ValidationError``
    (a 400).
    """
    if converts(data):
        return {"status": [CONVERSION_REFUSED]}

    org = request.profile.org
    previous_assigned_to_users = list(
        lead.assigned_to.all().values_list("id", flat=True)
    )
    serializer = LeadCreateSerializer(
        data=data,
        instance=lead,
        request_obj=request,
        partial=True,
    )
    if not serializer.is_valid():
        return serializer.errors

    save_kwargs = {}
    if "custom_fields" in data:
        cf_payload = data.get("custom_fields")
        if isinstance(cf_payload, str):
            try:
                cf_payload = json.loads(cf_payload)
            except (TypeError, ValueError):
                cf_payload = None
        cleaned_cf, cf_errors = validate_custom_fields_payload(
            "Lead",
            cf_payload or {},
            org,
            existing=lead.custom_fields or {},
        )
        if cf_errors:
            return {"custom_fields": cf_errors}
        save_kwargs["custom_fields"] = cleaned_cf
    # Parsed before the first write, so a malformed id leaves the lead as it
    # was. An absent key parses to [].
    tag_ids = payload_id_list(data.get("tags"), "tags")
    contact_ids = payload_id_list(data.get("contacts"), "contacts")
    team_ids = payload_id_list(data.get("teams"), "teams")
    assigned_ids = payload_id_list(data.get("assigned_to"), "assigned_to")
    lead = serializer.save(**save_kwargs)

    if "tags" in data:
        lead.tags.clear()
        if tag_ids:
            lead.tags.add(*Tags.objects.filter(id__in=tag_ids, org=org, is_active=True))

    if "contacts" in data:
        replace_visible_contacts(lead.contacts, contact_ids, request.profile)

    if "teams" in data:
        lead.teams.clear()
        if team_ids:
            lead.teams.add(*Teams.objects.filter(id__in=team_ids, org=org))

    if "assigned_to" in data:
        with assigned_by(request.profile):
            lead.assigned_to.set(
                Profile.objects.filter(id__in=assigned_ids, org=org, is_active=True)
            )
        current_assigned_users = list(
            lead.assigned_to.all().values_list("id", flat=True)
        )
        recipients = list(set(current_assigned_users) - set(previous_assigned_to_users))
        if recipients:
            lead_id = lead.id
            org_id = str(org.id)
            transaction.on_commit(
                lambda: send_email_to_assigned_user.delay(recipients, lead_id, org_id),
                robust=True,
            )
    return None
