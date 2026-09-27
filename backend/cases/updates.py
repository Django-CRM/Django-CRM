"""One path for changing a ticket that already exists.

The ticket PATCH (`CaseDetailView.patch`), a macro's actions
(`macros.views.MacroApplyView`) and the bulk edit
(`cases.bulk_views.BulkUpdateCasesView`) all write through `update_case`, so
each of them runs the same gates in the same order:

* `CaseCreateSerializer.validate`: the merged-ticket lock, the close gate
  (the approval a matching `pre_close` rule asks for, with a close that sends
  no `closed_on` dated today in the org's timezone) and Duplicate only by
  merge;
* assignees and tags filtered to active rows in the caller's org;
* the email to whoever the write newly assigned.

The bulk edit used to carry its own copy of the write, and that copy had lost
the last two: it assigned deactivated people and archived tags, and nobody it
assigned was told.

Authorization is the caller's job and happens first: `get_case_or_404` (the
read rule, 404 for a ticket the caller may not open) and then the write rule
(`assert_case_write_access` or `has_case_write_access`).
"""

import json

from django.db import transaction

from cases.serializer import CaseCreateSerializer
from cases.tasks import send_email_to_assigned_user
from common.custom_fields import validate_payload as validate_custom_fields_payload
from common.models import Profile, Tags, Teams
from common.validators import payload_id_list
from contacts.access import replace_visible_contacts


def notify_newly_assigned(request, case, previous_assignee_ids):
    """Email whoever the edit just put on this ticket, and nobody else.

    Shared by PUT and every `update_case` caller. It used to live only in PUT,
    so the web app, which edits with PATCH, assigned people who were never
    told. The phone edits with PUT and did notify them, which is how the two
    clients came to disagree about whether assignment says anything to the
    person assigned.

    Queued once the surrounding transaction commits, never before: the bulk
    edit writes each ticket inside its own `atomic()`, so a task queued inside
    it could reach a worker before the new assignees are committed, and a
    write that raises and rolls back would already have emailed. Outside a
    transaction it is queued at once. `robust` so a broker outage is logged
    rather than turning a saved edit into a 500.
    """
    current = set(case.assigned_to.all().values_list("id", flat=True))
    recipients = list(current - set(previous_assignee_ids))
    if not recipients:
        return
    case_id = case.id
    org_id = str(request.profile.org.id)
    transaction.on_commit(
        lambda: send_email_to_assigned_user.delay(recipients, case_id, org_id),
        robust=True,
    )


def update_case(request, case, data, *, append_tags=False):
    """Apply ``data`` to ``case`` as a partial update.

    Returns ``None`` once saved, or the error dict a 400 carries, before
    anything is written. Only the keys present in ``data`` change. ``tags``
    replaces the ticket's tags unless ``append_tags`` is set, in which case it
    adds to them (bulk tagging and macros must not wipe a ticket's other tags).
    ``assigned_to`` always replaces. A malformed id in ``contacts``, ``teams``,
    ``assigned_to`` or ``tags`` raises DRF's ``ValidationError`` (a 400).
    """
    serializer = CaseCreateSerializer(
        case,
        data=data,
        request_obj=request,
        partial=True,
    )
    if not serializer.is_valid():
        return serializer.errors

    org = request.profile.org
    previous_assignee_ids = list(case.assigned_to.all().values_list("id", flat=True))
    # `closed_on` is not passed here: the validated data carries it, dated by
    # the serializer when a close sends none, and a value passed to `save()`
    # would override that date with the stored empty one.
    save_kwargs = {
        "case_type": data.get("case_type") if "case_type" in data else case.case_type,
    }
    if "custom_fields" in data:
        cf_payload = data.get("custom_fields")
        if isinstance(cf_payload, str):
            try:
                cf_payload = json.loads(cf_payload)
            except (TypeError, ValueError):
                cf_payload = None
        cleaned_cf, cf_errors = validate_custom_fields_payload(
            "Case",
            cf_payload or {},
            org,
            existing=case.custom_fields or {},
        )
        if cf_errors:
            return {"custom_fields": cf_errors}
        save_kwargs["custom_fields"] = cleaned_cf
    # Parsed before the first write, so a malformed id leaves the ticket as it
    # was. An absent key parses to [].
    contact_ids = payload_id_list(data.get("contacts"), "contacts")
    team_ids = payload_id_list(data.get("teams"), "teams")
    assigned_ids = payload_id_list(data.get("assigned_to"), "assigned_to")
    tag_ids = payload_id_list(data.get("tags"), "tags")
    case = serializer.save(**save_kwargs)

    if "contacts" in data:
        replace_visible_contacts(case.contacts, contact_ids, request.profile)

    if "teams" in data:
        case.teams.clear()
        if team_ids:
            case.teams.add(*Teams.objects.filter(id__in=team_ids, org=org))

    if "assigned_to" in data:
        case.assigned_to.clear()
        if assigned_ids:
            case.assigned_to.add(
                *Profile.objects.filter(id__in=assigned_ids, org=org, is_active=True)
            )

    if "tags" in data:
        if not append_tags:
            case.tags.clear()
        if tag_ids:
            case.tags.add(*Tags.objects.filter(id__in=tag_ids, org=org, is_active=True))

    notify_newly_assigned(request, case, previous_assignee_ids)
    return None
