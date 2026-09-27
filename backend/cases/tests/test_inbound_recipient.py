"""The inbound webhook ingests only mail addressed to its own mailbox.

Signature and TopicArn checks prove a notification came from the mailbox's
topic, not that the mail was meant for the mailbox. One topic can fan out to
many mailboxes (a platform-wide SES receipt rule, with every mailbox pinned to
a topic in the platform account), and before this check every subscribed org
ingested every org's mail as tickets.

The recipients come from SES's envelope (`receipt.recipients`, the RCPT TO
addresses, Bcc included, which the sender cannot forge) when the SNS Message is
SES's JSON notification, and from Delivered-To, X-Original-To, To and Cc when
it is a bare raw email. Every signature below is genuinely verified.
"""

from __future__ import annotations

import base64
import json
import logging

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from rest_framework.test import APIClient

from cases.inbound.parser import parse_raw_email
from cases.models import Case, EmailMessage
from cases.tests.test_inbound_email import SNS_TOPIC, _make_mailbox, _raw_email
from cases.tests.test_inbound_sns import _signed_payload
from cases.tests.test_inbound_topic_pin import _post, _real_verification

NOT_ADDRESSED = {"ok": True, "dropped": True, "reason": "not_addressed_to_mailbox"}


@pytest.fixture
def anon():
    return APIClient()


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _ses_json(raw, recipients, *, encode=False):
    """SES's SNS-action notification, shaped as AWS documents it."""
    return json.dumps(
        {
            "notificationType": "Received",
            "receipt": {
                "recipients": recipients,
                "action": {"type": "SNS", "topicArn": SNS_TOPIC},
            },
            "mail": {"destination": recipients, "messageId": "ses-1"},
            "content": base64.b64encode(raw.encode()).decode() if encode else raw,
        }
    )


def _deliver(client, mailbox, key, message):
    payload = _signed_payload(key, topic_arn=SNS_TOPIC, Message=message)
    with _real_verification(key):
        return _post(client, mailbox, payload)


def _counts(org):
    return (
        Case.objects.filter(org=org).count(),
        EmailMessage.objects.filter(org=org).count(),
    )


@pytest.mark.django_db
class TestSesEnvelope:
    def test_mail_for_the_mailbox_is_ingested(self, anon, org_a, signing_key):
        mailbox = _make_mailbox(org_a)
        response = _deliver(
            anon, mailbox, signing_key, _ses_json(_raw_email(), ["support@acme.com"])
        )
        assert response.status_code == 200, response.content
        assert response.json()["created_case"] is True
        assert _counts(org_a) == (1, 1)

    def test_mail_for_another_orgs_mailbox_is_dropped_and_stores_nothing(
        self, anon, org_a, org_b, signing_key, caplog
    ):
        """The leak itself: both mailboxes on one topic, mail for org_b posted
        to org_a's webhook. Nothing may land in either org."""
        mailbox_a = _make_mailbox(org_a)
        _make_mailbox(org_b, address="help@beta.com")
        raw = _raw_email(to="help@beta.com", subject="Beta payroll question")

        with caplog.at_level(logging.WARNING, logger="cases.inbound_views"):
            response = _deliver(
                anon, mailbox_a, signing_key, _ses_json(raw, ["help@beta.com"])
            )

        assert response.status_code == 200, response.content
        assert response.json() == NOT_ADDRESSED
        assert _counts(org_a) == (0, 0)
        assert _counts(org_b) == (0, 0)
        logged = " ".join(r.getMessage() for r in caplog.records)
        assert str(mailbox_a.id) in logged
        assert "beta.com" not in logged and "acme.com" not in logged

    def test_a_fanned_out_notification_lands_only_in_the_addressed_org(
        self, anon, org_a, org_b, signing_key
    ):
        mailbox_a = _make_mailbox(org_a)
        mailbox_b = _make_mailbox(org_b, address="help@beta.com")
        message = _ses_json(_raw_email(to="help@beta.com"), ["help@beta.com"])

        dropped = _deliver(anon, mailbox_a, signing_key, message)
        ingested = _deliver(anon, mailbox_b, signing_key, message)

        assert dropped.json() == NOT_ADDRESSED
        assert ingested.json()["created_case"] is True
        assert _counts(org_a) == (0, 0)
        assert _counts(org_b) == (1, 1)
        assert EmailMessage.objects.get(org=org_b).mailbox_id == mailbox_b.id

    def test_bcc_delivery_is_ingested_although_to_and_cc_name_others(
        self, anon, org_a, signing_key
    ):
        mailbox = _make_mailbox(org_a)
        raw = _raw_email(
            to="someone@elsewhere.com", extra_headers="Cc: other@elsewhere.com"
        )
        response = _deliver(
            anon, mailbox, signing_key, _ses_json(raw, ["support@acme.com"])
        )
        assert response.status_code == 200, response.content
        assert response.json()["created_case"] is True
        assert _counts(org_a) == (1, 1)

    def test_a_to_header_naming_the_mailbox_does_not_override_the_envelope(
        self, anon, org_a, signing_key
    ):
        """Headers are the sender's to write. With an SES envelope present, a
        To naming this mailbox must not pull in mail SES delivered elsewhere."""
        mailbox = _make_mailbox(org_a)
        raw = _raw_email(to="support@acme.com")
        response = _deliver(
            anon, mailbox, signing_key, _ses_json(raw, ["help@beta.com"])
        )
        assert response.json() == NOT_ADDRESSED
        assert _counts(org_a) == (0, 0)

    def test_match_ignores_case_whitespace_and_display_names(
        self, anon, org_a, signing_key
    ):
        mailbox = _make_mailbox(org_a, address="Support@Acme.com")
        response = _deliver(
            anon,
            mailbox,
            signing_key,
            _ses_json(_raw_email(), ["  Help Desk <SUPPORT@acme.COM> "]),
        )
        assert response.status_code == 200, response.content
        assert response.json()["created_case"] is True

    def test_a_plus_address_is_a_different_mailbox(self, anon, org_a, signing_key):
        mailbox = _make_mailbox(org_a)
        response = _deliver(
            anon,
            mailbox,
            signing_key,
            _ses_json(_raw_email(), ["support+billing@acme.com"]),
        )
        assert response.json() == NOT_ADDRESSED
        assert _counts(org_a) == (0, 0)

    def test_an_envelope_without_recipients_is_dropped(self, anon, org_a, signing_key):
        """Fail closed: a JSON envelope with no `receipt.recipients` does not
        fall back to the forgeable headers."""
        mailbox = _make_mailbox(org_a)
        message = json.dumps({"notificationType": "Received", "content": _raw_email()})
        response = _deliver(anon, mailbox, signing_key, message)
        assert response.json() == NOT_ADDRESSED
        assert _counts(org_a) == (0, 0)

    def test_base64_content_is_decoded(self, anon, org_a, signing_key):
        """The SNS action's Encoding option may be Base64. Read as-is, the
        content has no headers and was recorded as a missing-Message-ID drop."""
        mailbox = _make_mailbox(org_a)
        raw = _raw_email(subject="Café login", body="Ça ne marche pas.")
        response = _deliver(
            anon,
            mailbox,
            signing_key,
            _ses_json(raw, ["support@acme.com"], encode=True),
        )
        assert response.status_code == 200, response.content
        assert response.json()["created_case"] is True
        row = EmailMessage.objects.get(org=org_a)
        assert row.message_id == "m1@example.com"
        assert row.subject == "Café login"
        assert "Ça ne marche pas." in row.body_text

    def test_base64_content_addressed_elsewhere_is_still_dropped(
        self, anon, org_a, signing_key
    ):
        mailbox = _make_mailbox(org_a)
        response = _deliver(
            anon,
            mailbox,
            signing_key,
            _ses_json(_raw_email(), ["help@beta.com"], encode=True),
        )
        assert response.json() == NOT_ADDRESSED
        assert _counts(org_a) == (0, 0)


@pytest.mark.django_db
class TestRawMessageHeaders:
    @pytest.mark.parametrize(
        "to, extra",
        [
            ("support@acme.com", ""),
            ("someone@elsewhere.com", "Cc: Support <SUPPORT@acme.com>"),
            ("someone@elsewhere.com", "Delivered-To: support@acme.com"),
            ("someone@elsewhere.com", "X-Original-To: support@acme.com"),
        ],
        ids=["to", "cc", "delivered-to", "x-original-to"],
    )
    def test_a_matching_header_is_ingested(self, anon, org_a, signing_key, to, extra):
        mailbox = _make_mailbox(org_a)
        raw = _raw_email(to=to, extra_headers=extra)
        response = _deliver(anon, mailbox, signing_key, raw)
        assert response.status_code == 200, response.content
        assert response.json()["created_case"] is True
        assert _counts(org_a) == (1, 1)

    def test_no_matching_header_is_dropped_and_stores_nothing(
        self, anon, org_a, org_b, signing_key
    ):
        mailbox = _make_mailbox(org_a)
        raw = _raw_email(
            to="help@beta.com",
            extra_headers="Cc: other@beta.com\r\nDelivered-To: help@beta.com",
        )
        response = _deliver(anon, mailbox, signing_key, raw)
        assert response.status_code == 200, response.content
        assert response.json() == NOT_ADDRESSED
        assert _counts(org_a) == (0, 0)
        assert _counts(org_b) == (0, 0)


def test_parser_collects_every_delivered_to_and_x_original_to():
    parsed = parse_raw_email(
        _raw_email(
            extra_headers=(
                "Delivered-To: First@Relay.test\r\n"
                "Delivered-To: second@relay.test\r\n"
                "X-Original-To: Desk <orig@relay.test>"
            )
        )
    )
    assert parsed.delivered_to == [
        "first@relay.test",
        "second@relay.test",
        "orig@relay.test",
    ]
