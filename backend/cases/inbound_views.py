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
"""

from __future__ import annotations

import base64
import binascii
import email.utils
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Max
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.inbound.parser import parse_raw_email
from cases.inbound.pipeline import ingest
from cases.inbound.sns import (
    SNSVerificationError,
    confirm_subscription,
    topic_account_id,
    verify_sns_message,
)
from cases.models import EmailMessage, InboundMailbox
from cases.serializer import InboundMailboxSerializer
from common.models import PortalAccessToken
from common.org_time import activate_org_timezone
from common.permissions import HasOrgContext, is_org_admin
from common.portal_tokens import resolve_portal_org
from common.tasks import set_rls_context

logger = logging.getLogger(__name__)

# Window the mailbox page reports ticket counts over.
MAILBOX_WINDOW_DAYS = 30


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
    """Split an SNS Notification's `Message` into (raw email, recipients).

    SES's SNS receipt action publishes a JSON notification: the raw email in
    `content`, and in `receipt.recipients` the envelope RCPT TO addresses the
    receipt rule matched. SES sets those, Bcc included, and the sender cannot
    forge them. A `Message` that is not a JSON object is taken to be the raw
    email itself, and the recipients come back as None: the caller then has
    only the message's own headers to go on.
    """
    try:
        envelope = json.loads(message)
    except (ValueError, TypeError):
        return message, None
    if not isinstance(envelope, dict):
        return message, None
    receipt = envelope.get("receipt")
    recipients = receipt.get("recipients") if isinstance(receipt, dict) else None
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
    return content, [r for r in recipients if isinstance(r, str)]


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


class InboundMailboxWebhookView(APIView):
    """Public endpoint where AWS SNS POSTs for one configured mailbox.

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
        # inactive and one whose mailbox is gone all get this same 404.
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
        if mailbox is None:
            return not_found

        # Likewise the org's day: the reopen window a reply is judged against
        # counts days since close on the org's calendar, not UTC's.
        # `GetProfileAndOrg` deactivates it when the request ends.
        activate_org_timezone(mailbox.org)

        if mailbox.provider != "ses":
            # Other providers wired into the same URL space land here.
            return Response(
                {
                    "error": True,
                    "errors": f"Provider {mailbox.provider!r} not yet supported",
                },
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )

        # SNS posts the JSON body in `request.body`. DRF may have parsed it.
        try:
            payload = (
                request.data
                if isinstance(request.data, dict)
                else json.loads(request.body or b"{}")
            )
        except (ValueError, TypeError):
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
        raw_message, recipients = _unwrap_sns_message(payload.get("Message") or "")

        if not raw_message:
            return Response(
                {"error": True, "errors": "SNS Message body is empty"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        parsed = parse_raw_email(raw_message)
        if recipients is None:
            # No SES envelope, so fall back to the headers a relay stamps with
            # the delivery address, then To and Cc.
            recipients = [
                *parsed.delivered_to,
                *parsed.to_addresses,
                *parsed.cc_addresses,
            ]
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
        result = ingest(parsed, mailbox)

        return Response(
            {
                "ok": True,
                "case_id": str(result.case.id) if result.case else None,
                "dropped": result.dropped,
                "reason": result.drop_reason,
                "created_case": result.created_case,
            },
            status=status.HTTP_200_OK,
        )


class InboundMailboxListCreateView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["InboundEmail"], responses={200: InboundMailboxSerializer(many=True)}
    )
    def get(self, request, *args, **kwargs):
        org = request.profile.org
        qs = InboundMailbox.objects.filter(org=org).order_by("address")
        mailboxes = InboundMailboxSerializer(
            qs, many=True, context={"request": request}
        ).data
        cases_by, last_by, total_cases = _mailbox_analytics(org)
        active = 0
        for mbx in mailboxes:
            mid = str(mbx["id"])
            mbx["cases_last_30d"] = cases_by.get(mid, 0)
            received = last_by.get(mid)
            mbx["last_received_at"] = received.isoformat() if received else None
            if mbx["is_active"]:
                active += 1
        totals = {
            "count": len(mailboxes),
            "active": active,
            "cases_last_30d": total_cases,
        }
        return Response({"mailboxes": mailboxes, "totals": totals})

    @extend_schema(
        tags=["InboundEmail"],
        request=InboundMailboxSerializer,
        responses={201: InboundMailboxSerializer},
    )
    def post(self, request, *args, **kwargs):
        if not is_org_admin(request.profile):
            return _admin_required()
        org = request.profile.org
        # No webhook secret is generated here. This used to mint a
        # `secrets.token_urlsafe(32)` when the body carried none, and nothing
        # ever compared the result: SES delivery is authenticated by the SNS
        # signature plus the `topic_arn` pin, both handled in
        # `InboundMailboxWebhookView.post`. Now that the field is write-only,
        # a generated value could not be read back either, so every mailbox
        # would carry a secret no one holds. An empty column reads honestly as
        # "not configured", which is also what a future provider integration
        # needs in order to tell configured mailboxes from unconfigured ones.
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
        request=InboundMailboxSerializer,
        responses={200: InboundMailboxSerializer},
    )
    def put(self, request, pk, *args, **kwargs):
        if not is_org_admin(request.profile):
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
        if not is_org_admin(request.profile):
            return _admin_required()
        obj = self._get_object(pk, request.profile.org)
        if not obj:
            return Response(
                {"error": True, "errors": "Mailbox not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        obj.delete()
        return Response({"error": False, "message": "Mailbox deleted"})
