"""Inbound email webhook + admin mailbox CRUD endpoints.

The webhook is intentionally public (no auth). Trust rests on two checks, and
it takes both. The signature alone is not enough:

1. `verify_sns_message` proves the payload was signed by AWS SNS. That rules
   out arbitrary JSON posted at the URL, but note what it does *not* prove:
   anyone with an AWS account can create a topic, so a valid signature only
   says the message came from *some* topic in *some* account.
2. The TopicArn pin (`InboundMailbox.topic_arn`) proves it came from *this
   mailbox's* topic. Without it, anyone who learned a mailbox UUID could point
   their own SNS topic here and have AWS sign forged mail for them, which,
   because the pipeline threads replies onto existing cases, means injecting
   messages into live customer conversations, not just spam tickets.

A mailbox gets its pin one of two ways. An admin enters the Topic ARN on the
mailbox, or, while it has none, a signature-verified SubscriptionConfirmation
pins its TopicArn, but only when that topic belongs to an AWS account listed in
the `INBOUND_SNS_ACCOUNT_IDS` setting. SNS lets any AWS account subscribe any
HTTPS endpoint to its own topic, so pinning whichever confirmation came first
handed the mailbox to whoever subscribed it first. With no pin and no allowed
account, every message is refused. Once pinned, only that exact ARN is accepted
and a confirmation never re-pins.

Neither check says the mail was meant for *this* mailbox. One topic may fan
out to many mailboxes (a platform-wide SES receipt rule, say, now that
`INBOUND_SNS_ACCOUNT_IDS` lets every mailbox pin a topic in the platform
account), so each would receive every org's mail. A notification is therefore
ingested only when the mailbox's address is one of its recipients, and is
otherwise acknowledged and discarded without writing anything in this org.

Postmark and Mailgun post to the same URL and are proven by the mailbox's
`webhook_secret` instead (`cases.inbound.providers`). Generated addresses all
arrive through `InboundCatchAllView`, pinned to `INBOUND_EMAIL_TOPIC_ARN`. A
`creates=leads` mailbox hands its mail to `cases.inbound.leads.ingest_lead`.
"""

from __future__ import annotations

import base64
import binascii
import copy
import email.utils
import hashlib
import json
import logging
from collections import Counter, defaultdict
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Max
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.inbound.generated import (
    address_domain,
    catch_all_topic,
    generated_domain,
    lookup_token,
)
from cases.inbound.leads import ingest_lead, read_email
from cases.inbound.limits import daily_limit
from cases.inbound.parser import parse_raw_email
from cases.inbound.pipeline import ingest
from cases.inbound.providers import (
    claim_mailgun_token,
    mailgun_signed,
    parse_mailgun,
    parse_postmark,
    postmark_authorized,
    release_mailgun_token,
)
from cases.inbound.sns import (
    SNSVerificationError,
    confirm_subscription,
    topic_account_id,
    verify_sns_message,
)
from cases.inbound.stored import StoredEmailUnavailable, stored_email
from cases.models import EmailMessage, InboundMailbox
from cases.serializer import InboundMailboxSerializer
from common.models import PortalAccessToken
from common.modules import module_is_available
from common.org_time import activate_org_timezone
from common.permissions import HasOrgContext, may_configure
from common.portal_tokens import resolve_portal_org
from common.tasks import clear_rls_context, set_rls_context

logger = logging.getLogger(__name__)

# Window the mailbox page reports ticket counts over.
MAILBOX_WINDOW_DAYS = 30
# Postmark and Mailgun posts larger than this are refused unread: above either
# provider's message limit once attachments are base64 encoded.
PROVIDER_BODY_MAX = 50 * 1024 * 1024
SES_SPAM_VERDICTS = (("virusVerdict", "virus"), ("spamVerdict", "spam"))
# A DMARC failure is acted on only where the sender's domain asks receivers to.
DMARC_ENFORCED_POLICIES = {"quarantine", "reject"}


def _ses_verdict(receipt: dict) -> str:
    """Why SES's checks failed an email ("virus", "spam" or "spoofed"), or an
    empty string."""
    for key, name in SES_SPAM_VERDICTS:
        if isinstance(receipt.get(key), dict) and receipt[key].get("status") == "FAIL":
            return name
    dmarc = receipt.get("dmarcVerdict")
    policy = str(receipt.get("dmarcPolicy") or "").lower()
    if (
        isinstance(dmarc, dict)
        and dmarc.get("status") == "FAIL"
        and policy in DMARC_ENFORCED_POLICIES
    ):
        return "spoofed"
    return ""


# How the settings page groups a dropped email's reason; any other is "other".
DROP_CATEGORIES = {
    "spam": "spam",
    "virus": "virus",
    "spoofed": "spoofed",
    "bounce": "automatic",
    "auto_submitted": "automatic",
    "precedence_bulk": "automatic",
    "x_autoreply": "automatic",
    "mailing_list": "automatic",
}


def _mailbox_drops(org):
    """Per mailbox, the emails dropped in the window by category, and the set
    of mailboxes that have accepted their daily limit in the last 24 hours."""
    now = timezone.now()
    inbound = EmailMessage.objects.filter(
        org=org, direction="inbound", mailbox__isnull=False
    ).order_by()
    drops = defaultdict(Counter)
    for row in (
        inbound.filter(created_at__gte=now - timedelta(days=MAILBOX_WINDOW_DAYS))
        .exclude(drop_reason="")
        .values("mailbox_id", "drop_reason")
        .annotate(n=Count("id"))
    ):
        category = DROP_CATEGORIES.get(row["drop_reason"], "other")
        drops[str(row["mailbox_id"])][category] += row["n"]
    full = {
        str(row["mailbox_id"])
        for row in inbound.filter(
            drop_reason="", created_at__gte=now - timedelta(hours=24)
        )
        .values("mailbox_id")
        .annotate(n=Count("id"))
        if row["n"] >= daily_limit()
    }
    return drops, full


def _mailbox_analytics(org):
    """Per-mailbox ticket counts + last-received timestamp, plus an org total.

    Both come from the ``EmailMessage.mailbox`` FK (set at ingest since the
    migration): a message records exactly which inbound address it arrived
    through, so these are attributed, not guessed.

    - ``cases_last_30d`` per mailbox = distinct cases *created* in the last 30
      days that have an inbound message through that mailbox (a reply arriving
      at the address for an older case is not a new ticket, so we anchor on the
      case's ``created_at``).
    - ``last_received_at`` per mailbox = the newest inbound message's
      ``received_at`` (any message, including dropped ones, the address still
      received mail).
    - org ``cases_last_30d`` = distinct such cases across all mailboxes (not a
      sum, so a case cross-posted to two addresses is not double counted).

    All scoped to ``org`` explicitly (RLS is inert in dev/test).
    """
    cutoff = timezone.now() - timedelta(days=MAILBOX_WINDOW_DAYS)
    inbound = EmailMessage.objects.filter(
        org=org, direction="inbound", mailbox__isnull=False
    )
    created = (
        inbound.filter(case__isnull=False, case__created_at__gte=cutoff)
        .values("mailbox_id")
        .annotate(n=Count("case", distinct=True))
    )
    cases_by_mailbox = {str(row["mailbox_id"]): row["n"] for row in created}
    last = inbound.values("mailbox_id").annotate(last=Max("received_at"))
    last_by_mailbox = {str(row["mailbox_id"]): row["last"] for row in last}
    total_cases = (
        inbound.filter(case__isnull=False, case__created_at__gte=cutoff)
        .values("case")
        .distinct()
        .count()
    )
    return cases_by_mailbox, last_by_mailbox, total_cases


def _admin_required():
    return Response(
        {"error": True, "errors": "Admin access required"},
        status=status.HTTP_403_FORBIDDEN,
    )


def _unwrap_sns_message(message):
    """Split an SNS Notification's `Message` into (raw email, recipients,
    verdict).

    SES's SNS receipt action publishes a JSON notification: the raw email in
    `content`, and in `receipt.recipients` the envelope RCPT TO addresses the
    receipt rule matched. SES sets those, Bcc included, and the sender cannot
    forge them. A `Message` that is not a JSON object is taken to be the raw
    email itself, and the recipients come back as None: the caller then has
    only the message's own headers to go on. The verdict is `_ses_verdict`'s.
    """
    try:
        envelope = json.loads(message)
    except (ValueError, TypeError):
        return message, None, ""
    if not isinstance(envelope, dict):
        return message, None, ""
    receipt = envelope.get("receipt")
    if not isinstance(receipt, dict):
        receipt = {}
    verdict = _ses_verdict(receipt)
    recipients = receipt.get("recipients")
    if not isinstance(recipients, list):
        recipients = []
    content = envelope.get("content")
    if not isinstance(content, str):
        content = ""
    # The SNS action's Encoding option may be Base64. A raw email always has a
    # `Name: value` header, and ":" is outside the base64 alphabet, so content
    # without one is the encoded form.
    if content and ":" not in content:
        try:
            content = base64.b64decode("".join(content.split()), validate=True)
        except (binascii.Error, ValueError):
            pass
    return content, [r for r in recipients if isinstance(r, str)], verdict


def _sns_payload(request):
    """The SNS body as a dict, or None when it is not a JSON object.

    Read from the raw body whatever its Content-Type: SNS posts
    `text/plain; charset=UTF-8` unless the subscription's delivery policy sets
    `headerContentType`, and DRF has no parser for that, so `request.data`
    answered 415 to every default subscription.
    """
    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _header_recipients(parsed):
    """With no SES envelope, the headers a relay stamps with the delivery
    address, then To and Cc."""
    return [*parsed.delivered_to, *parsed.to_addresses, *parsed.cc_addresses]


def _addressed_to(mailbox, recipients):
    """Whether `mailbox.address` is one of `recipients`, ignoring case, spaces
    and display names. Exact otherwise: `support+x@` is a different address."""
    target = mailbox.address.strip().lower()
    return any(
        addr.strip().lower() == target
        for _, addr in email.utils.getaddresses(recipients)
    )


def _topic_rejected():
    """Deliberately as opaque as the signature failure. A caller probing the
    webhook shouldn't learn whether a mailbox is pinned or to what."""
    return Response(
        {"error": True, "errors": "Signature verification failed"},
        status=status.HTTP_403_FORBIDDEN,
    )


def mailbox_module(mailbox) -> str:
    """The module a mailbox needs on: Leads for a lead address, else Tickets."""
    return "leads" if mailbox.creates == InboundMailbox.CREATES_LEADS else "tickets"


def _deliver(parsed, mailbox, reading=None) -> dict:
    """Run one email through its mailbox; the webhook's answer. `reading`
    (`cases.inbound.leads.read_email`) is shared by the catch-all's recipients."""
    if mailbox.creates == InboundMailbox.CREATES_LEADS:
        outcome = ingest_lead(parsed, mailbox, reading)
        return {
            "ok": True,
            "lead_id": str(outcome.lead.id) if outcome.lead else None,
            "result": outcome.result,
            "dropped": outcome.dropped,
            "reason": outcome.drop_reason,
        }
    result = ingest(parsed, mailbox)
    return {
        "ok": True,
        "case_id": str(result.case.id) if result.case else None,
        "dropped": result.dropped,
        "reason": result.drop_reason,
        "created_case": result.created_case,
    }


def _provider_delivery(request, mailbox):
    """A Postmark or Mailgun post, checked against the mailbox's secret."""
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length > PROVIDER_BODY_MAX:
        return Response(
            {"error": True, "errors": "Message too large"},
            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )
    refused = Response(
        {"error": True, "errors": "Signature verification failed"},
        status=status.HTTP_401_UNAUTHORIZED,
    )
    if mailbox.provider == "postmark":
        # The password is in a header, so it is checked before the body is read.
        if not postmark_authorized(
            request.META.get("HTTP_AUTHORIZATION"), mailbox.webhook_secret
        ):
            logger.warning("Postmark auth failed for mailbox=%s", mailbox.id)
            return refused
        if not isinstance(request.data, dict):
            return Response(
                {"error": True, "errors": "Body is not a JSON object"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            _deliver(parse_postmark(request.data), mailbox), status=status.HTTP_200_OK
        )
    if not mailgun_signed(request.data, mailbox.webhook_secret):
        logger.warning("Mailgun signature failed for mailbox=%s", mailbox.id)
        return refused
    if not claim_mailgun_token(mailbox, request.data):
        logger.warning("Mailgun token reused for mailbox=%s", mailbox.id)
        return refused
    try:
        answer = _deliver(parse_mailgun(request.data, request.FILES), mailbox)
    except Exception:
        release_mailgun_token(mailbox, request.data)
        raise
    return Response(answer, status=status.HTTP_200_OK)


class InboundMailboxWebhookView(APIView):
    """Public endpoint where AWS SNS, Postmark or Mailgun POSTs for one
    configured mailbox.

    URL: `/api/cases/inbound/<mailbox_id>/`. The mailbox lookup also acts as
    the org boundary, the URL embeds the per-mailbox UUID so the webhook
    can't be confused for one belonging to a different tenant.

    The request is anonymous, so it carries no org, and `inbound_mailbox` is
    org-scoped: under the non-superuser production role an empty RLS context
    hides the very row that would name the org. The org therefore comes first
    from the unscoped `PortalAccessToken` lookup (registered when the mailbox
    is created, see cases/signals.py), then the context is set, and only then
    is the mailbox read, within that org. `RequireOrgContext` exempts this one
    route by name for the same reason.
    """

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        tags=["InboundEmail"],
        request=inline_serializer(
            name="SNSInboundPayload",
            fields={
                "Type": serializers.CharField(),
                "Message": serializers.CharField(),
                "Signature": serializers.CharField(),
                "SigningCertURL": serializers.CharField(),
            },
        ),
        responses={
            200: inline_serializer(
                name="InboundWebhookResponse",
                fields={
                    "ok": serializers.BooleanField(),
                    "case_id": serializers.CharField(allow_null=True, required=False),
                    "dropped": serializers.BooleanField(required=False),
                    "reason": serializers.CharField(required=False),
                },
            )
        },
    )
    def post(self, request, mailbox_id, *args, **kwargs):
        # Don't leak which UUIDs exist: an unknown id, one whose mailbox is
        # inactive or gone, and one in an org with Tickets off all get this 404.
        not_found = Response(
            {"error": True, "errors": "Mailbox not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
        # `mailbox_id` is already the canonical UUID string: the `uid` path
        # converter normalises every form it accepts (any case, bare hex,
        # braces, URN), and the lookup is keyed on that canonical form.
        org_id = resolve_portal_org(mailbox_id, PortalAccessToken.INBOUND_MAILBOX)
        if org_id is None:
            return not_found
        # No middleware sets a context for an anonymous request, so set it
        # before any ORM read/write touches an org-scoped table.
        set_rls_context(org_id)
        mailbox = (
            InboundMailbox.objects.filter(pk=mailbox_id, org_id=org_id, is_active=True)
            .select_related("org")
            .first()
        )
        if mailbox is None or not module_is_available(
            mailbox.org, mailbox_module(mailbox)
        ):
            return not_found

        # Likewise the org's day: the reopen window a reply is judged against
        # counts days since close on the org's calendar, not UTC's.
        # `GetProfileAndOrg` deactivates it when the request ends.
        activate_org_timezone(mailbox.org)

        if mailbox.provider in ("postmark", "mailgun"):
            return _provider_delivery(request, mailbox)
        if mailbox.provider != "ses":
            # IMAP needs polling, which this webhook cannot do.
            return Response(
                {
                    "error": True,
                    "errors": f"Provider {mailbox.provider!r} not yet supported",
                },
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )

        payload = _sns_payload(request)
        if payload is None:
            return Response(
                {"error": True, "errors": "Body is not valid JSON"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            verify_sns_message(payload)
        except SNSVerificationError as exc:
            logger.warning(
                "SNS verification failed for mailbox=%s: %s", mailbox.id, exc
            )
            return Response(
                {"error": True, "errors": "Signature verification failed"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # A valid signature only proves the message came from *some* SNS topic
        # in *some* AWS account. Pin it to this mailbox's topic, or anyone who
        # learns the mailbox UUID can have AWS sign forged mail for them.
        msg_type = payload.get("Type")
        topic_arn = payload.get("TopicArn") or ""
        pinned = mailbox.topic_arn or ""
        if pinned:
            if topic_arn != pinned:
                logger.warning(
                    "SNS TopicArn mismatch for mailbox=%s: got %r",
                    mailbox.id,
                    topic_arn,
                )
                return _topic_rejected()
        elif (
            msg_type == "SubscriptionConfirmation"
            and topic_account_id(topic_arn) in settings.INBOUND_SNS_ACCOUNT_IDS
        ):
            # No admin-entered ARN, and the topic belongs to an AWS account
            # the operator allows. The confirmation is signature-verified
            # above, so it really came from that account's topic. A topic in
            # any other account pins nothing: SNS lets anyone subscribe this
            # URL, and the first to do so would otherwise own the mailbox.
            mailbox.topic_arn = topic_arn
            mailbox.save(update_fields=["topic_arn"])
            logger.info("Pinned mailbox=%s to TopicArn=%r", mailbox.id, topic_arn)
        else:
            # Unpinned mailbox, and this message can't establish a pin: it is
            # a notification, or its topic is malformed or in an AWS account
            # missing from INBOUND_SNS_ACCOUNT_IDS.
            logger.warning(
                "Rejecting SNS message for unpinned mailbox=%s (type=%r, topic=%r)",
                mailbox.id,
                msg_type,
                topic_arn,
            )
            return _topic_rejected()

        if msg_type == "SubscriptionConfirmation":
            try:
                confirm_subscription(payload)
            except Exception:
                logger.exception("SNS subscription confirmation failed")
                return Response(
                    {"error": True, "errors": "SubscribeURL fetch failed"},
                    status=status.HTTP_502_BAD_GATEWAY,
                )
            return Response({"ok": True, "subscribed": True})

        if msg_type == "UnsubscribeConfirmation":  # pragma: no cover: informational
            logger.info("SNS unsubscribe for mailbox=%s", mailbox.id)
            return Response({"ok": True, "unsubscribed": True})

        if msg_type != "Notification":
            return Response(
                {"error": True, "errors": f"Unsupported SNS Type: {msg_type!r}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # SES's SNS action sends a JSON notification carrying the raw email; a
        # bare raw RFC 5322 string (another publisher on the pinned topic) is
        # accepted too. See `_unwrap_sns_message`.
        raw_message, recipients, verdict = _unwrap_sns_message(
            payload.get("Message") or ""
        )

        if not raw_message:
            return Response(
                {"error": True, "errors": "SNS Message body is empty"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        parsed = parse_raw_email(raw_message)
        parsed.spam_verdict = verdict
        if recipients is None:
            recipients = _header_recipients(parsed)
        if not _addressed_to(mailbox, recipients):
            # The topic also serves other mailboxes and this mail belongs to
            # one of them. Storing anything here, even a dropped audit row,
            # would copy another tenant's mail into this org. A 200 stops SNS
            # retrying; the log names the mailbox only, never an address.
            logger.warning(
                "Dropping SNS notification not addressed to mailbox=%s", mailbox.id
            )
            return Response(
                {"ok": True, "dropped": True, "reason": "not_addressed_to_mailbox"},
                status=status.HTTP_200_OK,
            )
        return Response(_deliver(parsed, mailbox), status=status.HTTP_200_OK)


def _short_hash(address):
    """A stable tag for an address in a log line that must not hold it."""
    return hashlib.sha256(address.encode()).hexdigest()[:12]


def _deliver_generated(parsed, address, reading) -> str:
    """Route one catch-all recipient to its org's active generated mailbox:
    "delivered", "dropped" or "failed".

    The org comes from the unscoped lookup row the mailbox registered on
    create (cases/signals.py), then the mailbox is read under that org's RLS
    context, so a row naming another org finds nothing. A failure is logged
    and counted here, so one org's failure never stops the others.
    """
    try:
        org_id = resolve_portal_org(
            lookup_token(address), PortalAccessToken.INBOUND_MAILBOX
        )
        if org_id is None:
            logger.warning(
                "Dropping catch-all mail for an unknown recipient (%s)",
                _short_hash(address),
            )
            return "dropped"
        set_rls_context(org_id)
        mailbox = (
            InboundMailbox.objects.filter(
                org_id=org_id,
                address__iexact=address,
                is_generated=True,
                is_active=True,
            )
            .select_related("org")
            .first()
        )
        if mailbox is None or not module_is_available(
            mailbox.org, mailbox_module(mailbox)
        ):
            logger.warning(
                "Dropping catch-all mail for recipient (%s): no active mailbox takes it",
                _short_hash(address),
            )
            return "dropped"
        activate_org_timezone(mailbox.org)
        # `ingest` writes a synthetic Message-ID onto the email it is given.
        _deliver(copy.copy(parsed), mailbox, reading)
        return "delivered"
    except Exception:
        logger.exception(
            "Catch-all delivery failed for recipient (%s)", _short_hash(address)
        )
        return "failed"
    finally:
        clear_rls_context()


class InboundCatchAllView(APIView):
    """Public endpoint for every generated address: `/api/cases/inbound/email/`.

    One SES receipt rule for `INBOUND_EMAIL_DOMAIN` publishes to one SNS topic,
    subscribed here. A notification is trusted only when its SNS signature
    verifies and its TopicArn is exactly `INBOUND_EMAIL_TOPIC_ARN`, the same
    two checks the per-mailbox webhook makes against its pin. Each recipient on
    the domain is then delivered to its own org's mailbox; mail for an unknown
    or inactive address is dropped and logged. With either setting empty the
    route answers 404.
    """

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        tags=["InboundEmail"],
        request=inline_serializer(
            name="SNSCatchAllPayload",
            fields={
                "Type": serializers.CharField(),
                "Message": serializers.CharField(),
                "TopicArn": serializers.CharField(),
            },
        ),
        responses={
            200: inline_serializer(
                name="InboundCatchAllResponse",
                fields={
                    "ok": serializers.BooleanField(),
                    "delivered": serializers.IntegerField(required=False),
                    "failed": serializers.IntegerField(required=False),
                },
            )
        },
    )
    def post(self, request, *args, **kwargs):
        domain, topic = generated_domain(), catch_all_topic()
        if not domain or not topic:
            return Response(
                {"error": True, "errors": "Not found"}, status=status.HTTP_404_NOT_FOUND
            )
        payload = _sns_payload(request)
        if payload is None:
            return Response(
                {"error": True, "errors": "Body is not valid JSON"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            verify_sns_message(payload)
        except SNSVerificationError as exc:
            logger.warning("SNS verification failed for the catch-all: %s", exc)
            return _topic_rejected()
        if payload.get("TopicArn") != topic:
            logger.warning(
                "Catch-all refused TopicArn %r", payload.get("TopicArn") or ""
            )
            return _topic_rejected()

        msg_type = payload.get("Type")
        if msg_type == "SubscriptionConfirmation":
            try:
                confirm_subscription(payload)
            except Exception:
                logger.exception("SNS subscription confirmation failed")
                return Response(
                    {"error": True, "errors": "SubscribeURL fetch failed"},
                    status=status.HTTP_502_BAD_GATEWAY,
                )
            return Response({"ok": True, "subscribed": True})
        if msg_type == "UnsubscribeConfirmation":  # pragma: no cover: informational
            return Response({"ok": True, "unsubscribed": True})
        if msg_type != "Notification":
            return Response(
                {"error": True, "errors": f"Unsupported SNS Type: {msg_type!r}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        message = payload.get("Message") or ""
        raw_message, recipients, verdict = _unwrap_sns_message(message)
        if not raw_message:
            try:
                raw_message = stored_email(message, topic)
            except StoredEmailUnavailable as exc:
                logger.warning("Stored email could not be read: %s", exc)
                return Response(
                    {"error": True, "errors": "Stored email could not be read"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
        if not raw_message:
            return Response(
                {"error": True, "errors": "SNS Message body is empty"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        parsed = parse_raw_email(raw_message)
        parsed.spam_verdict = verdict
        if recipients is None:
            recipients = _header_recipients(parsed)
        addresses = dict.fromkeys(
            addr.strip().lower()
            for _, addr in email.utils.getaddresses(recipients)
            if addr.strip() and address_domain(addr) == domain
        )
        reading = read_email(parsed) if addresses else None
        outcomes = Counter(_deliver_generated(parsed, a, reading) for a in addresses)
        failed = outcomes["failed"]
        # A failure answers 500 so SNS redelivers; the recipients that already
        # have the email then answer "already received".
        return Response(
            {"ok": not failed, "delivered": outcomes["delivered"], "failed": failed},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
            if failed
            else status.HTTP_200_OK,
        )


class InboundMailboxListRowSerializer(InboundMailboxSerializer):
    """Schema only: a mailbox as the list returns it, with its 30-day numbers.
    `drops_last_30d` carries a category only once an email was dropped in it."""

    cases_last_30d = serializers.IntegerField(read_only=True)
    last_received_at = serializers.DateTimeField(read_only=True, allow_null=True)
    drops_last_30d = inline_serializer(
        name="InboundMailboxDropsResponse",
        fields={
            category: serializers.IntegerField(required=False)
            for category in sorted({*DROP_CATEGORIES.values(), "other"})
        },
        read_only=True,
    )
    dropped_last_30d = serializers.IntegerField(read_only=True)
    limit_reached = serializers.BooleanField(read_only=True)

    class Meta(InboundMailboxSerializer.Meta):
        fields = (
            *InboundMailboxSerializer.Meta.fields,
            "cases_last_30d",
            "last_received_at",
            "drops_last_30d",
            "dropped_last_30d",
            "limit_reached",
        )


class InboundMailboxListCreateView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["InboundEmail"],
        responses={
            200: inline_serializer(
                name="InboundMailboxListResponse",
                fields={
                    "mailboxes": InboundMailboxListRowSerializer(many=True),
                    "totals": inline_serializer(
                        name="InboundMailboxListTotals",
                        fields={
                            "count": serializers.IntegerField(),
                            "active": serializers.IntegerField(),
                            "cases_last_30d": serializers.IntegerField(),
                        },
                    ),
                    "generated_domain": serializers.CharField(allow_null=True),
                    "daily_limit": serializers.IntegerField(),
                },
            )
        },
    )
    def get(self, request, *args, **kwargs):
        org = request.profile.org
        qs = InboundMailbox.objects.filter(org=org).order_by("address")
        mailboxes = InboundMailboxSerializer(
            qs, many=True, context={"request": request}
        ).data
        cases_by, last_by, total_cases = _mailbox_analytics(org)
        drops_by, full = _mailbox_drops(org)
        active = 0
        for mbx in mailboxes:
            mid = str(mbx["id"])
            mbx["cases_last_30d"] = cases_by.get(mid, 0)
            received = last_by.get(mid)
            mbx["last_received_at"] = received.isoformat() if received else None
            drops = dict(drops_by.get(mid, {}))
            mbx["drops_last_30d"] = drops
            mbx["dropped_last_30d"] = sum(drops.values())
            mbx["limit_reached"] = mid in full
            if mbx["is_active"]:
                active += 1
        totals = {
            "count": len(mailboxes),
            "active": active,
            "cases_last_30d": total_cases,
        }
        return Response(
            {
                "mailboxes": mailboxes,
                "totals": totals,
                # Set when `generate_address` can make an address here.
                "generated_domain": generated_domain() or None,
                "daily_limit": daily_limit(),
            }
        )

    @extend_schema(
        tags=["InboundEmail"],
        request=InboundMailboxSerializer,
        responses={201: InboundMailboxSerializer},
    )
    def post(self, request, *args, **kwargs):
        if not may_configure(request.profile):
            return _admin_required()
        org = request.profile.org
        # No webhook secret is generated here: the field is write-only, so a
        # generated one could never be read back and given to Postmark or
        # Mailgun. An empty column reads honestly as "not configured", and such
        # a mailbox accepts no provider post.
        serializer = InboundMailboxSerializer(
            data=request.data, context={"org": org, "request": request}
        )
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer.save(org=org)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class InboundMailboxDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def _get_object(self, pk, org):
        return InboundMailbox.objects.filter(pk=pk, org=org).first()

    @extend_schema(tags=["InboundEmail"], responses={200: InboundMailboxSerializer})
    def get(self, request, pk, *args, **kwargs):
        obj = self._get_object(pk, request.profile.org)
        if not obj:
            return Response(
                {"error": True, "errors": "Mailbox not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            InboundMailboxSerializer(obj, context={"request": request}).data
        )

    @extend_schema(
        tags=["InboundEmail"],
        request=InboundMailboxSerializer(partial=True),
        responses={200: InboundMailboxSerializer},
    )
    def put(self, request, pk, *args, **kwargs):
        if not may_configure(request.profile):
            return _admin_required()
        org = request.profile.org
        obj = self._get_object(pk, org)
        if not obj:
            return Response(
                {"error": True, "errors": "Mailbox not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = InboundMailboxSerializer(
            obj,
            data=request.data,
            partial=True,
            context={"org": org, "request": request},
        )
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer.save()
        return Response(serializer.data)

    @extend_schema(
        tags=["InboundEmail"],
        responses={
            200: inline_serializer(
                name="MailboxDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, *args, **kwargs):
        if not may_configure(request.profile):
            return _admin_required()
        obj = self._get_object(pk, request.profile.org)
        if not obj:
            return Response(
                {"error": True, "errors": "Mailbox not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        obj.delete()
        return Response({"error": False, "message": "Mailbox deleted"})
