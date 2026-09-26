"""End-to-end inbound-email processing.

Given a parsed email and the mailbox it arrived on, decide:
  1. Should we drop it (spam/bounce/auto-reply)?
  2. Does it belong to an existing Case (threading match)?
  3. Otherwise: who's the contact, and we create a new Case.

Always records exactly one `EmailMessage` row, even drops, so admins have a
forensic trail.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from django.db import transaction

from cases.models import Case, EmailMessage, InboundMailbox

from .contacts import resolve_contact
from .parser import ParsedEmail
from .spam import should_drop
from .threading import _SUBJECT_FALLBACK_RE as _SUBJECT_TAG_RE
from .threading import find_existing_case

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    """Returned by `ingest`. Telemetry-shaped: tests assert on the fields."""

    email_message: EmailMessage
    case: Optional[Case]
    created_case: bool
    dropped: bool
    drop_reason: str = ""


def _record_email_message(
    *,
    parsed: ParsedEmail,
    mailbox: InboundMailbox,
    case: Optional[Case],
    drop_reason: str = "",
) -> EmailMessage:
    """Create the audit row for this email. Idempotent on (org, message_id)."""
    defaults = {
        "case": case,
        "mailbox": mailbox,
        "direction": "inbound",
        "in_reply_to": parsed.in_reply_to[:512],
        "references": " ".join(parsed.references)[:65535],
        "from_address": parsed.from_address,
        "to_addresses": ", ".join(parsed.to_addresses)[:65535],
        "cc_addresses": ", ".join(parsed.cc_addresses)[:65535],
        "subject": (parsed.subject or "")[:512],
        "body_text": parsed.body_text or "",
        "body_html": parsed.body_html or "",
        "received_at": parsed.received_at,
        "drop_reason": drop_reason,
    }
    obj, _created = EmailMessage.objects.update_or_create(
        org=mailbox.org,
        message_id=parsed.message_id,
        defaults=defaults,
    )
    return obj


def _sender_is_on_case(parsed: ParsedEmail, case: Case) -> bool:
    """Whether the From address is already one of the case's contacts.

    A match on the `[Case #...]` subject tag alone is trusted only for these
    senders. Outbound replies carry the tag and the reply text, so anybody who
    sees one subject line could otherwise mail in, be added as a contact and
    receive every later reply on somebody else's ticket.
    """
    if not parsed.from_address:
        return False
    return case.contacts.filter(email__iexact=parsed.from_address).exists()


def ingest(parsed: ParsedEmail, mailbox: InboundMailbox) -> IngestResult:
    """Run the inbound pipeline for one email; returns an `IngestResult`.

    Wraps DB writes in a transaction so a partial run leaves no half-written
    Case behind. Webhooks should ack with 200 even when this returns
    `dropped=True`: the provider already accepted the message and a 4xx
    would just trigger pointless retries.
    """
    drop, reason = should_drop(parsed)
    if drop:
        with transaction.atomic():
            row = _record_email_message(
                parsed=parsed, mailbox=mailbox, case=None, drop_reason=reason
            )
        return IngestResult(
            email_message=row,
            case=None,
            created_case=False,
            dropped=True,
            drop_reason=reason,
        )

    if not parsed.message_id:
        # Treat a missing Message-ID as an auto_submitted-style drop. Without
        # one we can't thread or de-dupe, and legitimate human-sent mail always
        # has it. Fabricate a synthetic id for the audit row.
        synthetic_id = f"missing-msgid-{mailbox.id}-{parsed.received_at.isoformat()}"
        parsed.message_id = synthetic_id
        with transaction.atomic():
            row = _record_email_message(
                parsed=parsed,
                mailbox=mailbox,
                case=None,
                drop_reason="missing_message_id",
            )
        return IngestResult(
            email_message=row,
            case=None,
            created_case=False,
            dropped=True,
            drop_reason="missing_message_id",
        )

    with transaction.atomic():
        # Idempotency: if we've already processed this message (provider retry),
        # bail early without creating a duplicate Case.
        prior = (
            EmailMessage.objects.filter(org=mailbox.org, message_id=parsed.message_id)
            .select_related("case")
            .first()
        )
        if prior is not None:
            return IngestResult(
                email_message=prior,
                case=prior.case,
                created_case=False,
                dropped=bool(prior.drop_reason),
                drop_reason=prior.drop_reason,
            )

        match = find_existing_case(parsed, mailbox.org)
        existing_case = None
        if match is not None and (
            match.by_header or _sender_is_on_case(parsed, match.case)
        ):
            existing_case = match.case
        contact = resolve_contact(parsed, mailbox.org)

        if existing_case is not None:
            row = _record_email_message(
                parsed=parsed, mailbox=mailbox, case=existing_case
            )
            if (
                contact is not None
                and not existing_case.contacts.filter(pk=contact.pk).exists()
            ):
                existing_case.contacts.add(contact)
            from cases.signals import (
                emit_email_received_activity,
                maybe_reopen_for_inbound_email,
            )

            maybe_reopen_for_inbound_email(existing_case, row)
            emit_email_received_activity(existing_case, row)
            return IngestResult(
                email_message=row,
                case=existing_case,
                created_case=False,
                dropped=False,
            )

        # New case path. The subject minus any [Case #...] tag becomes the
        # name: a tag here names a case this email was not threaded onto.
        subject = " ".join(_SUBJECT_TAG_RE.sub(" ", parsed.subject or "").split())
        case = Case(
            name=(subject or "(no subject)")[:64],
            status="New",
            priority=mailbox.default_priority,
            case_type=mailbox.default_case_type,
            description=parsed.body_text or parsed.body_html or "",
            org=mailbox.org,
            external_thread_id=parsed.message_id,
        )
        # Attach inbound-only fields the routing engine reads (post_save signal
        # picks these up via getattr).
        case._routing_mailbox_id = mailbox.id
        from_domain = ""
        if parsed.from_address and "@" in parsed.from_address:
            from_domain = parsed.from_address.rsplit("@", 1)[-1].lower()
        case._routing_from_domain = from_domain
        case.save()
        # Only an active member of this org. The stored default outlives the
        # member, and an inactive one cannot sign in to answer the ticket, so
        # it is treated as no default: the ticket is left to routing alone.
        assignee = mailbox.default_assignee
        if (
            assignee is not None
            and assignee.is_active
            and assignee.org_id == mailbox.org_id
        ):
            case.assigned_to.add(assignee)
        if contact is not None:
            case.contacts.add(contact)

        row = _record_email_message(parsed=parsed, mailbox=mailbox, case=case)

        from cases.signals import emit_email_received_activity

        emit_email_received_activity(case, row)

        return IngestResult(
            email_message=row,
            case=case,
            created_case=True,
            dropped=False,
        )


__all__ = ["IngestResult", "ingest"]
