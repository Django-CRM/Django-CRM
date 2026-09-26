"""The single write path for web form submissions.

Both `webforms.public_views.WebFormSubmitView` and the legacy
`leads.views.lead_interactions.CreateLeadFromSite` go through here. That is the
point: the legacy endpoint had six separate defects in its own copy of this
logic, and one shared implementation is what stops them being fixed once and
reintroduced next to it.

A lead form creates or merges a Lead. A ticket form creates a Case the way a
new inbound email does (`cases/inbound/pipeline.py`), so routing and SLA
stamping run through the same Case signals rather than a shortcut of their own.

Everything this module writes is derived from the form row or from values a
serializer has already validated. Nothing is read from a request.
"""

import logging

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models.functions import Lower

from cases.models import Case
from cases.signals import route_after_relations
from common.models import Comment
from contacts.models import Contact
from leads.models import Lead
from webforms.constants import TICKET_SUBJECT_MAX_LENGTH
from webforms.models import WebForm, WebFormSubmission

logger = logging.getLogger(__name__)

# The Lead columns the merge path may fill in on a repeat submission.
# Assignment, status, source and the pipeline columns are absent on purpose:
# those belong to whoever owns the lead now, not to a stranger who happens to
# know their email address.
MERGEABLE_FIELDS = [
    "salutation",
    "first_name",
    "last_name",
    "phone",
    "company_name",
    "job_title",
    "website",
    "title",
    "city",
    "state",
    "country",
    "postcode",
    "industry",
]


def _existing_lead(form, email):
    """The org's lead with this address, if there is one.

    Case-insensitive, because `Lead` enforces uniqueness on `Lower("email")`.
    Matching case-sensitively here would find nothing and then hit the
    constraint on insert, which is exactly the 500 this function exists to
    avoid.

    Scoped to the form's org. A lead in another tenant with the same address is
    a different person, and merging across that boundary would leak one org's
    record into another's form.
    """
    if not email:
        return None
    return (
        Lead.objects.filter(org=form.org)
        .annotate(email_lower=Lower("email"))
        .filter(email_lower=email.lower())
        .first()
    )


def active_assignee(form):
    """The form's `assign_to` while it is an active member of the form's org.

    Otherwise None, so that every caller treats a deactivated (or foreign)
    assignee exactly as a form with no assignee. The stored row outlives the
    member: deactivating somebody does not clear the forms pointing at them,
    and without this they went on receiving, and being mailed, each new lead.
    """
    assignee = form.assign_to
    if assignee is None or not assignee.is_active or assignee.org_id != form.org_id:
        return None
    return assignee


def _owner(form):
    """The Profile credited with the submission's comment, or None.

    A web form submission has no request user, so this is the form's configured
    assignee. There is deliberately no fallback to `form.created_by`: that is a
    User and `Comment.commented_by` is a Profile, so the two are not
    interchangeable. `commented_by` is nullable, and a comment with no author
    is the honest record of a form nobody is assigned to.
    """
    return active_assignee(form)


def _created_by_user(form):
    """The User stamped on the created Lead's or Case's `created_by`.

    `created_by` points at a User while `assign_to` is a Profile, so this
    reaches through. Falls back to the form's own creator when the form has no
    active assignee, because a record with no creator is one nobody can be
    asked about.
    """
    assignee = active_assignee(form)
    if assignee is not None and assignee.user_id:
        return assignee.user
    return form.created_by


def _create_lead(form, values, custom_fields):
    lead = Lead(
        org=form.org,
        # Server-derived, never from the submission.
        status="assigned",
        source=form.lead_source,
        is_active=True,
        created_by=_created_by_user(form),
        custom_fields=dict(custom_fields or {}),
    )
    for key, value in values.items():
        setattr(lead, key, value)
    lead.save()

    assignee = active_assignee(form)
    if assignee is not None:
        lead.assigned_to.add(assignee)
    tags = list(form.tags.all())
    if tags:
        lead.tags.add(*tags)
    return lead


def _merge_lead(form, lead, values, custom_fields):
    """Fill blanks only, then record the new message as a comment.

    Overwriting a populated field is deliberately excluded. Anyone who knows a
    prospect's email address can post this form, so allowing an overwrite would
    let a stranger rewrite that prospect's record.
    """
    changed = []
    for key in MERGEABLE_FIELDS:
        incoming = values.get(key)
        if not incoming:
            continue
        if getattr(lead, key, None):
            continue
        setattr(lead, key, incoming)
        changed.append(key)

    custom_changed = False
    for key, value in (custom_fields or {}).items():
        if lead.custom_fields.get(key) in (None, "", []):
            lead.custom_fields[key] = value
            custom_changed = True
    if custom_changed:
        changed.append("custom_fields")

    if changed:
        lead.save(update_fields=sorted(set(changed)) + ["updated_at"])

    message = values.get("description") or ""
    if message:
        Comment.objects.create(
            org=form.org,
            content_type=ContentType.objects.get_for_model(Lead),
            object_id=lead.id,
            comment=f"Web form submission ({form.name}):\n{message}",
            commented_by=_owner(form),
        )
    return lead


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
        created_by=_created_by_user(form),
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
        existing = _existing_lead(form, values.get("email"))
        if existing is not None:
            lead = _merge_lead(form, existing, values, custom_fields)
            status = WebFormSubmission.ACCEPTED_DUPLICATE
        else:
            lead = _create_lead(form, values, custom_fields)
            status = WebFormSubmission.ACCEPTED

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
