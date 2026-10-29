"""The one way a lead arrives from outside.

Web forms, lead webhooks and the lead address all call `receive_lead`, so the
three routes cannot drift apart. It matches, in order, the sender's own id
(`external_source` plus `external_id`), then email (case-insensitive), then an
exact `phone_e164`, against leads of any status. The loose last-ten-digits
phone rule is never used here: a wrong automatic merge loses data, while a
missed one only leaves a duplicate that the duplicate panel shows.

A match is merged (blanks filled, never overwritten), a closed lead comes back
as recycled, and a converted lead gets the enquiry on its contact instead of a
second lead. The same external id twice is a `duplicate` that changes nothing.
A match by phone alone never takes the incoming email, and an email or number
that differs from the lead's is written into the enquiry instead.

Every arrival but a duplicate is recorded as an `enquiry` interaction: on the
lead, or on the contact a converted lead became.
"""

from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models.functions import Lower
from django.utils import timezone

from automation.capture import suppress_rules
from common.kanban import place_in_column
from common.models import Profile
from common.phones import to_e164
from contacts.models import Contact
from interactions.service import log_interaction
from leads import alerts
from leads.models import Lead, LeadStage
from leads.sources import source_keys
from leads.tasks import send_email_to_assigned_user, send_enquired_again_email
from webforms.constants import LEAD_FIELD_VALUES

FALLBACK_SOURCE = "other"
# Older than this, an arrival is history: no automation rules, no alerts and
# no emails.
LATE_AFTER = timedelta(hours=24)
DESCRIPTION_MAX = 20_000
ENQUIRY_MAX = 10_000
# The route an arrival took, from the prefix of its `external_source`.
ENQUIRY_ORIGINS = ("intake", "email", "webform")

# What a repeat enquiry may fill when the lead has it blank. Assignment,
# status, source and the pipeline columns belong to whoever owns the lead now,
# not to whoever knows the person's email address or number.
MERGE_FIELDS = (
    "salutation",
    "first_name",
    "last_name",
    "email",
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
)


@dataclass
class IncomingLead:
    values: dict  # whitelisted lead columns (the web forms' whitelist)
    custom_fields: dict
    source: str  # an org source key; unknown falls back to "other"
    source_detail: str = ""
    source_ref: str = ""
    external_source: str = ""  # "intake:<id>", "email:<mailbox id>", "webform:<id>"
    external_id: str = ""
    tags: tuple = ()
    assignee: "Profile | None" = None
    enquiry_text: str = ""
    channel_label: str = ""  # the enquiry's subject, and named in the email
    occurred_at: "datetime | None" = None


@dataclass
class IntakeResult:
    result: str  # "created", "merged" or "duplicate"
    lead: "Lead | None"
    contact: "Contact | None" = None  # set when the match was a converted lead


def receive_lead(org, incoming: IncomingLead, *, notify=True) -> IntakeResult:
    """Create or merge one arrival in `org`.

    `notify=False` is for a caller that mails its own recipients (web forms
    tell the form's notify list and the lead's owners in one message). The
    lead alerts (`leads.alerts`) go either way.
    """
    incoming = replace(
        incoming,
        external_source=(incoming.external_source or "").strip()[:100],
        external_id=(incoming.external_id or "").strip()[:255],
    )
    when = _arrived_at(incoming.occurred_at)
    late = timezone.now() - when > LATE_AFTER
    with ExitStack() as history:
        if late:
            history.enter_context(suppress_rules())
            history.enter_context(alerts.quiet())
        result = _receive(org, incoming, when)
    if not late:
        _notify(org, incoming, result, emails=notify)
    return result


@transaction.atomic
def _receive(org, data, when):
    retried = False
    while True:
        match, how = _find_match(org, data)
        if match is None:
            try:
                with transaction.atomic():
                    return IntakeResult("created", _create(org, data, when))
            except IntegrityError:
                # A concurrent arrival with the same id or email won the
                # insert. Matching again lands on its lead.
                if retried:
                    raise
                retried = True
                continue
        if how == "external":
            return IntakeResult("duplicate", match)
        # Locked, so two arrivals for one lead merge one after the other, and
        # the second sees an id the first stamped.
        lead = Lead.objects.select_for_update().get(pk=match.pk)
        if data.external_id and (lead.external_source, lead.external_id) == (
            data.external_source,
            data.external_id,
        ):
            return IntakeResult("duplicate", lead)
        return _merge(org, lead, data, when, how)


def _find_match(org, data):
    leads = Lead.objects.filter(org=org)
    if data.external_id:
        found = leads.filter(
            external_source=data.external_source, external_id=data.external_id
        ).first()
        if found is not None:
            return found, "external"
    found = _match_email(leads, _email(data))
    if found is not None:
        return found, "email"
    phone = to_e164(data.values.get("phone"), org.default_country)
    if phone:
        found = leads.filter(phone_e164=phone).order_by("-created_at").first()
        if found is not None:
            return found, "phone"
    return None, None


def _match_email(leads, email):
    if not email:
        return None
    return (
        leads.annotate(email_lower=Lower("email"))
        .filter(email_lower=email.lower())
        .first()
    )


def _create(org, data, when):
    assignee = _active_member(data.assignee, org)
    values = _columns(data.values)
    lead = Lead(
        org=org,
        status="assigned",
        source=_source_key(org, data.source),
        source_detail=(data.source_detail or "")[:100],
        source_ref=(data.source_ref or "")[:255],
        external_source=data.external_source,
        external_id=data.external_id,
        last_enquiry_at=when,
        is_active=True,
        created_by=assignee.user if assignee is not None else None,
        custom_fields=dict(data.custom_fields or {}),
    )
    for key, value in values.items():
        setattr(lead, key, value)
    text = _text(data)
    if not lead.description:
        lead.description = text[:DESCRIPTION_MAX] or None
    lead.save()

    if assignee is not None:
        lead.assigned_to.add(assignee)
    tags = [tag for tag in data.tags if tag.org_id == org.id]
    if tags:
        lead.tags.add(*tags)
    _enquiry(org, lead, data, when)
    return lead


def _merge(org, lead, data, when, how):
    claimed = {k: getattr(lead, k) for k in ("email", "external_source", "external_id")}
    values = _columns(data.values)
    changed = set()

    if lead.status == "converted":
        contact = converted_contact(lead)
        changed |= _stamp(lead, data, when)
        _save(lead, changed, claimed)
        _enquiry(org, lead, data, when, _differing(org, lead, values), contact)
        return IntakeResult("merged", lead, contact)

    for key in MERGE_FIELDS:
        value = values.get(key)
        if not value or getattr(lead, key, None):
            continue
        # A number alone does not make the sender this person's mailbox, and
        # one lead per email per org: an address another lead holds stays there.
        if key == "email" and (how == "phone" or _email_taken(org, lead, value)):
            continue
        setattr(lead, key, value)
        changed.add(key)

    custom = dict(lead.custom_fields or {})
    for key, value in (data.custom_fields or {}).items():
        if custom.get(key) in (None, "", []):
            custom[key] = value
            changed.add("custom_fields")
    lead.custom_fields = custom

    if lead.status == "closed":
        lead.status = "recycled"
        changed.add("status")
        stage = _reopened_stage(org, lead)
        if stage is not None:
            lead.stage = stage
            lead.kanban_order = place_in_column(
                Lead.objects.filter(org=org, stage=stage), exclude_pk=lead.pk
            )
            changed |= {"stage", "kanban_order"}
    changed |= _stamp(lead, data, when)
    _save(lead, changed, claimed)

    tags = [tag for tag in data.tags if tag.org_id == org.id]
    if tags:
        lead.tags.add(*tags)
    _enquiry(org, lead, data, when, _differing(org, lead, values))
    return IntakeResult("merged", lead)


def _stamp(lead, data, when):
    """The sender's id when the lead has none, so a retry is a duplicate, and
    the latest enquiry time. Returns the fields it changed."""
    changed = set()
    if data.external_id and not lead.external_id:
        lead.external_source = data.external_source
        lead.external_id = data.external_id
        changed |= {"external_source", "external_id"}
    if lead.last_enquiry_at is None or when > lead.last_enquiry_at:
        lead.last_enquiry_at = when
        changed.add("last_enquiry_at")
    return changed


def _save(lead, changed, claimed):
    if not changed:
        return
    try:
        with transaction.atomic():
            lead.save(update_fields=[*sorted(changed), "updated_at"])
    except IntegrityError:
        # Another arrival took the email or the id since they were checked.
        for key, value in claimed.items():
            setattr(lead, key, value)
        lead.save(update_fields=[*sorted(changed - set(claimed)), "updated_at"])


def _reopened_stage(org, lead):
    """The first open stage of the pipeline a closed lead sits in, when its
    stage is a won or lost one; None leaves the stage alone."""
    if lead.stage_id is None or lead.stage.stage_type == "open":
        return None
    return (
        LeadStage.objects.filter(
            org=org, pipeline_id=lead.stage.pipeline_id, stage_type="open"
        )
        .order_by("order", "created_at")
        .first()
    )


def _differing(org, lead, values):
    """`Label: value` lines for an incoming email or number the lead did not
    take, so the enquiry keeps them."""
    lines = []
    email = values.get("email")
    if email and email.lower() != (lead.email or "").strip().lower():
        lines.append(f"Email: {email}")
    phone = values.get("phone")
    if phone and not _same_number(org, phone, lead.phone):
        lines.append(f"Phone: {phone}")
    return lines


def _same_number(org, incoming, stored):
    if not stored:
        return False
    region = org.default_country
    a, b = to_e164(incoming, region), to_e164(stored, region)
    if a and b:
        return a == b
    return incoming.strip() == stored.strip()


def _notify(org, data, result, *, emails):
    # On commit, so a worker can read the lead and a web form's rotation has
    # assigned it first. `robust`: the lead is saved by now, so a queue that
    # cannot take the mail is logged rather than turned into a failed
    # delivery the sender retries.
    org_id = str(org.id)
    lead = result.lead
    if result.result == "created":
        alerts.queue_created(lead)
        assignee = _active_member(data.assignee, org)
        if not emails or assignee is None:
            return
        args = ([str(assignee.id)], str(lead.id), org_id)
        transaction.on_commit(
            lambda: send_email_to_assigned_user.delay(*args, source=data.channel_label),
            robust=True,
        )
    elif result.result == "merged":
        contact = result.contact
        transaction.on_commit(
            lambda: alerts.notify_enquired(lead, contact), robust=True
        )
        if not emails:
            return
        kwargs = {
            "lead_id": None if contact is not None else str(result.lead.id),
            "contact_id": str(contact.id) if contact is not None else None,
            "channel_label": data.channel_label,
        }
        transaction.on_commit(
            lambda: send_enquired_again_email.delay(org_id, **kwargs), robust=True
        )


def _enquiry(org, lead, data, when, lines=(), contact=None):
    """Record the arrival on `lead`, or on `contact` (and its account) when
    the lead was converted into one."""
    body = "\n\n".join(part for part in (_text(data), "\n".join(lines)) if part)
    if len(body) > ENQUIRY_MAX:
        body = body[: ENQUIRY_MAX - 1] + "…"
    route = (data.external_source or "").partition(":")[0]
    home, record, links = "lead", lead, {}
    if contact is not None:
        account = contact.account
        home, record = "contact", contact
        links = {"lead": lead}
        if account is not None and account.org_id == org.id:
            links["account"] = account
    log_interaction(
        org,
        home=home,
        record=record,
        kind="enquiry",
        direction="inbound",
        occurred_at=when,
        origin=route if route in ENQUIRY_ORIGINS else "intake",
        subject=(data.channel_label or "")[:200],
        body=body,
        **links,
    )


def converted_contact(lead):
    """The contact a converted lead became: conversion keys it by email."""
    if not lead.email:
        return None
    return (
        Contact.objects.filter(org_id=lead.org_id, email__iexact=lead.email)
        .select_related("account")
        .order_by("-created_at")
        .first()
    )


def _email_taken(org, lead, email):
    return (
        Lead.objects.filter(org=org)
        .exclude(pk=lead.pk)
        .annotate(email_lower=Lower("email"))
        .filter(email_lower=email.lower())
        .exists()
    )


def _columns(values):
    """Whitelisted, non-empty values, strings stripped."""
    cleaned = {}
    for key, value in (values or {}).items():
        if key not in LEAD_FIELD_VALUES:
            continue
        if isinstance(value, str):
            value = value.strip()
        if value in (None, ""):
            continue
        cleaned[key] = value
    return cleaned


def _email(data):
    value = (data.values or {}).get("email")
    return value.strip() if isinstance(value, str) else ""


def _text(data):
    return (data.enquiry_text or "").strip()


def _source_key(org, key):
    if not key:
        return ""
    return key if key in source_keys(org, active_only=False) else FALLBACK_SOURCE


def _active_member(profile, org):
    if profile is None or not profile.is_active or profile.org_id != org.id:
        return None
    return profile


def _arrived_at(occurred_at):
    now = timezone.now()
    if occurred_at is None:
        return now
    if timezone.is_naive(occurred_at):
        occurred_at = occurred_at.replace(tzinfo=UTC)
    return min(occurred_at, now)
