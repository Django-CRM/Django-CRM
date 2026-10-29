"""The single write path for web form submissions.

Both `webforms.public_views.WebFormSubmitView` and the legacy
`leads.views.lead_interactions.CreateLeadFromSite` go through here. That is the
point: the legacy endpoint had six separate defects in its own copy of this
logic, and one shared implementation is what stops them being fixed once and
reintroduced next to it.

A lead form hands its submission to `leads.intake.receive_lead`, the service
the lead webhooks and the lead address use too, so matching and merging cannot
drift between them. A ticket form creates a Case the way a new inbound email
does (`cases/inbound/pipeline.py`), so routing and SLA stamping run through the
same Case signals rather than a shortcut of their own.

Everything this module writes is derived from the form row or from values a
serializer has already validated. Nothing is read from a request.
"""

import logging

from crum import get_current_user
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from cases.models import Case
from cases.signals import route_after_relations
from contacts.models import Contact
from leads.intake import IncomingLead, receive_lead
from leads.models import Lead
from webforms.constants import TICKET_SUBJECT_MAX_LENGTH
from webforms.models import WebForm, WebFormSubmission

logger = logging.getLogger(__name__)

# Lead statuses that end a lead's life as somebody's open work. What is left
# (assigned, in process, recycled) counts against a rotation member's cap.
CLOSED_LEAD_STATUSES = ("converted", "closed")


def active_assignee(form):
    """The form's `assign_to` while it is an active member of the form's org.

    Otherwise None, so that every caller treats a deactivated (or foreign)
    assignee exactly as a form with no assignee. The stored row outlives the
    member: deactivating somebody does not clear the forms pointing at them,
    and without this they went on receiving, and being mailed, each new lead.

    Also None on a rotation form. Its `assign_to` is a leftover from before the
    switch, kept so switching back restores it, and it must not go on
    authoring merge comments or owning legacy contacts.
    """
    if form.assignment_mode == WebForm.ASSIGN_ROTATION:
        return None
    assignee = form.assign_to
    if assignee is None or not assignee.is_active or assignee.org_id != form.org_id:
        return None
    return assignee


def rotation_assignee(form):
    """The member a rotation form's next new lead goes to, or None.

    Call inside the transaction that inserts the lead. The form row is locked
    first, so two submissions arriving together queue here instead of both
    reading the same cursor and handing two leads to one person.

    Eligible means an active member of the form's org holding fewer open
    leads than `rotation_cap` (when one is set). Open is active and neither
    converted nor closed, counted over every lead in the org, because the cap
    is about the member's workload rather than about this one form.

    The eligible members are taken in id order and the first whose id is
    greater than the cursor wins, wrapping to the lowest. Ordering by id
    rather than by a stored position is what keeps the turn fair when members
    are added or removed: the cursor names a person, not a list index. When
    nobody is eligible the lead is left unassigned and the cursor stays put.
    """
    last_id = (
        WebForm.objects.select_for_update()
        .filter(pk=form.pk, org_id=form.org_id)
        .values_list("rotation_last_assigned_id", flat=True)
        .first()
    )
    members = form.rotation_members.filter(org_id=form.org_id, is_active=True)
    if form.rotation_cap:
        members = members.annotate(
            open_leads=Count(
                "lead_assigned_users",
                filter=Q(
                    lead_assigned_users__org_id=form.org_id,
                    lead_assigned_users__is_active=True,
                )
                & ~Q(lead_assigned_users__status__in=CLOSED_LEAD_STATUSES),
                distinct=True,
            )
        ).filter(open_leads__lt=form.rotation_cap)
    eligible = sorted(members, key=lambda profile: profile.id)
    if not eligible:
        return None
    chosen = next(
        (profile for profile in eligible if last_id and profile.id > last_id),
        eligible[0],
    )
    # `update`, not `save`: this is the system moving a cursor, not somebody
    # editing the form, so neither the audit stamps nor `updated_at` move.
    WebForm.objects.filter(pk=form.pk).update(rotation_last_assigned=chosen)
    form.rotation_last_assigned = chosen
    return chosen


def _created_by_user(form, assignee):
    """The User stamped on the created Lead's or Case's `created_by`.

    `created_by` points at a User while the assignee is a Profile, so this
    reaches through. Falls back to the form's own creator when the record has
    no active assignee, because a record with no creator is one nobody can be
    asked about.
    """
    if assignee is not None and assignee.user_id:
        return assignee.user
    return form.created_by


def _receive_lead(form, values, custom_fields):
    """Hand a lead form's submission to `receive_lead` and return its result.

    Only a new lead takes a rotation turn, so the turn is taken once the
    service has created one, in the same transaction and under the form's
    row lock. Outside a signed-in request (the public form) a new lead is
    credited to its assignee, else to the form's creator; inside one,
    `BaseModel.save` has already credited the caller.
    """
    person = active_assignee(form)
    result = receive_lead(
        form.org,
        IncomingLead(
            values=values,
            custom_fields=dict(custom_fields or {}),
            source=form.lead_source,
            external_source=f"webform:{form.id}",
            tags=tuple(form.tags.all()),
            assignee=person,
            enquiry_text=values.get("description") or "",
            channel_label=f"web form {form.name}",
        ),
        # `webforms.tasks` mails the form's notify list and the lead's owners
        # in one message, so the service sends nothing of its own.
        notify=False,
    )
    if result.result != "created":
        return result
    lead = result.lead
    assignee = person
    if form.assignment_mode == WebForm.ASSIGN_ROTATION:
        assignee = rotation_assignee(form)
        if assignee is not None:
            lead.assigned_to.add(assignee)
    user = get_current_user()
    creator = _created_by_user(form, assignee)
    if (user is None or user.is_anonymous) and lead.created_by != creator:
        Lead.objects.filter(pk=lead.pk).update(
            created_by=creator, updated_at=timezone.now()
        )
        lead.created_by = creator
    return result


def _ticket_contact(form, values):
    """The org's contact for the submitted email, created when there is none.

    Matched case-insensitively within the form's org, as the inbound email
    path matches (`cases.inbound.contacts.resolve_contact`) and as Contact's
    `UniqueConstraint(Lower("email"), "org")` compares. A contact in another
    org with the same address is a different person.

    An existing contact is returned untouched. Anyone who knows an address can
    post this form, so the submitted name, phone and company only ever
    describe a contact this call creates.
    """
    email = values.get("email")
    if not email:
        return None
    existing = (
        Contact.objects.filter(org=form.org, email__iexact=email)
        .order_by("-created_at")
        .first()
    )
    if existing is not None:
        return existing
    try:
        # A savepoint: a concurrent submission for the same address can win
        # the race to the unique constraint, and its IntegrityError must not
        # poison the transaction the ticket is being written in.
        with transaction.atomic():
            return Contact.objects.create(
                org=form.org,
                email=email,
                first_name=values.get("first_name") or email.split("@", 1)[0],
                last_name=values.get("last_name") or "",
                phone=values.get("phone") or None,
                organization=values.get("company_name") or None,
                auto_created=True,
                is_active=True,
            )
    except IntegrityError:
        return Contact.objects.filter(org=form.org, email__iexact=email).first()


def _create_ticket(form, values, custom_fields):
    """Create the Case, in the shape `cases.inbound.pipeline.ingest` does.

    Status "New", the form's own priority and type, and the `_routing_*`
    attribute the routing engine reads, so a rule on the sender's domain
    matches a web form ticket as it would an emailed one. Routing waits until
    the form's tags are on the ticket (`route_after_relations`), so a rule on
    one of them matches too. SLA targets are stamped by the Case pre_save
    signal.
    """
    contact = _ticket_contact(form, values)
    email = values.get("email") or ""
    subject = values.get("name") or form.name
    case = Case(
        org=form.org,
        # Server-derived, never from the submission.
        name=subject[:TICKET_SUBJECT_MAX_LENGTH],
        status="New",
        priority=form.ticket_priority,
        case_type=form.ticket_type or None,
        description=values.get("description") or "",
        custom_fields=dict(custom_fields or {}),
        created_by=_created_by_user(form, active_assignee(form)),
    )
    case._routing_from_domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    with route_after_relations():
        case.save()

        # Only an active member of this org, as the inbound path treats a
        # mailbox's default assignee. Otherwise the ticket is left to routing.
        assignee = active_assignee(form)
        if assignee is not None:
            case.assigned_to.add(assignee)
        if contact is not None:
            case.contacts.add(contact)
        tags = list(form.tags.all())
        if tags:
            case.tags.add(*tags)
    return case


@transaction.atomic
def submit_form(
    form,
    values,
    *,
    custom_fields=None,
    ip=None,
    referer="",
    rejected=None,
    reason="",
):
    """Record one submission and create its lead or ticket.

    `values` is the VALIDATED dict keyed by whitelist key (a Lead attribute
    name, or a ticket field), never raw request data. `custom_fields` is a
    separate map keyed by CustomFieldDefinition key, because those land in a
    JSON column rather than on an attribute.

    `rejected`, when set to a WebFormSubmission rejection status, records the
    attempt and writes no record at all.

    Returns the WebFormSubmission. Callers read `.status` to decide what to
    tell the visitor, and must never leak `.reject_reason`, which is triage
    detail: telling a bot which control caught it is how it learns to get past
    that control.
    """
    if rejected is not None:
        return WebFormSubmission.objects.create(
            org=form.org,
            form=form,
            lead=None,
            payload=values or {},
            status=rejected,
            reject_reason=reason[:255],
            submitted_ip=ip,
            referer=referer,
        )

    lead = case = None
    if form.target == WebForm.TARGET_TICKET:
        case = _create_ticket(form, values, custom_fields)
        status = WebFormSubmission.ACCEPTED
    else:
        result = _receive_lead(form, values, custom_fields)
        lead = result.lead
        status = (
            WebFormSubmission.ACCEPTED
            if result.result == "created"
            else WebFormSubmission.ACCEPTED_DUPLICATE
        )

    payload = dict(values)
    if custom_fields:
        payload["custom_fields"] = dict(custom_fields)

    return WebFormSubmission.objects.create(
        org=form.org,
        form=form,
        lead=lead,
        case=case,
        payload=payload,
        status=status,
        submitted_ip=ip,
        referer=referer,
    )
