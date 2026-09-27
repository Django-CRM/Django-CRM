"""Who may pin an inbound mailbox to an SNS topic.

The webhook used to pin whatever TopicArn the first signature-valid
SubscriptionConfirmation carried. SNS lets any AWS account subscribe any HTTPS
endpoint to its own topic, and any org member can read mailbox ids from
`GET /api/cases/mailboxes/`, so an attacker could subscribe an unpinned
mailbox's URL to their own topic, win the pin, and then open tickets as any
sender while the real topic was refused.

A mailbox now pins only (a) the ARN an admin entered, or (b) while it has none,
a confirmation whose topic belongs to an AWS account in
`INBOUND_SNS_ACCOUNT_IDS`. With neither, every message is refused.
"""

from __future__ import annotations

import base64
import functools
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from rest_framework.test import APIClient

from cases.inbound import sns
from cases.inbound.sns import topic_account_id, verify_sns_message
from cases.models import Case, EmailMessage
from cases.tests.test_inbound_email import _make_mailbox, _raw_email
from cases.tests.test_inbound_sns import _make_cert, _pem, _signed_payload

MAILBOXES_URL = "/api/cases/mailboxes/"
BACKEND_DIR = Path(__file__).resolve().parents[2]

OWNER_ACCOUNT = "123456789012"
ATTACKER_ACCOUNT = "999999999999"
OWNER_TOPIC = f"arn:aws:sns:us-east-1:{OWNER_ACCOUNT}:acme-inbound"
OWNER_OTHER_TOPIC = f"arn:aws:sns:eu-west-1:{OWNER_ACCOUNT}:other-inbound"
ATTACKER_TOPIC = f"arn:aws:sns:us-east-1:{ATTACKER_ACCOUNT}:attacker-topic"
REJECTED = {"error": True, "errors": "Signature verification failed"}


@pytest.fixture
def anon():
    return APIClient()


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _real_verification(signing_key):
    """The production verifier with only the certificate download stubbed, so
    every signature below is genuinely checked. A valid signature is exactly
    what an attacker's own topic gets from AWS."""
    cert = _make_cert(signing_key)
    return patch(
        "cases.inbound_views.verify_sns_message",
        functools.partial(verify_sns_message, fetch_cert=lambda url: _pem(cert)),
    )


def _signed_confirmation(key, topic_arn):
    payload = {
        "Type": "SubscriptionConfirmation",
        "MessageId": "c-1",
        "Token": "tok",
        "TopicArn": topic_arn,
        "Message": "You have chosen to subscribe to the topic.",
        "SubscribeURL": "https://sns.us-east-1.amazonaws.com/?Action=ConfirmSubscription",
        "Timestamp": "2026-05-09T12:00:00.000Z",
        "SignatureVersion": "1",
        "SigningCertURL": (
            "https://sns.us-east-1.amazonaws.com/SimpleNotificationService-x.pem"
        ),
    }
    string_to_sign = sns._build_string_to_sign(payload, sns._SUBSCRIPTION_KEYS)
    payload["Signature"] = base64.b64encode(
        key.sign(string_to_sign, padding.PKCS1v15(), hashes.SHA1())
    ).decode()
    return payload


def _post(client, mailbox, payload):
    return client.post(f"/api/cases/inbound/{mailbox.id}/", payload, format="json")


def _confirm(client, mailbox, key, topic_arn):
    """Post a signed confirmation; return (response, whether SNS was called)."""
    with (
        _real_verification(key),
        patch("cases.inbound_views.confirm_subscription") as confirm,
    ):
        response = _post(client, mailbox, _signed_confirmation(key, topic_arn))
    return response, confirm.called


def _notify(client, mailbox, key, topic_arn, **email):
    payload = _signed_payload(key, topic_arn=topic_arn, Message=_raw_email(**email))
    with _real_verification(key):
        return _post(client, mailbox, payload)


# ---------------------------------------------------------------------------
# The reviewer's reproduction, kept as a regression test
# ---------------------------------------------------------------------------


class TestAttackerCannotWinThePin:
    def test_reviewer_reproduction(self, anon, user_client, org_a, signing_key):
        """Any member reads the id, the attacker subscribes it to their own
        topic, then mails in as the CEO. Every step must now fail, and the
        real topic entered by an admin must still work afterwards."""
        mailbox = _make_mailbox(org_a, topic_arn="")
        listed = user_client.get(MAILBOXES_URL).json()["mailboxes"]
        assert [row["id"] for row in listed] == [str(mailbox.id)]

        response, subscribed = _confirm(anon, mailbox, signing_key, ATTACKER_TOPIC)
        assert response.status_code == 403
        assert response.json() == REJECTED
        assert subscribed is False, "must not fetch an attacker's SubscribeURL"
        mailbox.refresh_from_db()
        assert mailbox.topic_arn == ""

        forged = _notify(
            anon, mailbox, signing_key, ATTACKER_TOPIC, from_="CEO <ceo@acme.com>"
        )
        assert forged.status_code == 403
        assert Case.objects.filter(org=org_a).count() == 0
        assert EmailMessage.objects.filter(org=org_a).count() == 0

        mailbox.topic_arn = OWNER_TOPIC
        mailbox.save(update_fields=["topic_arn"])
        real = _notify(anon, mailbox, signing_key, OWNER_TOPIC)
        assert real.status_code == 200, real.content
        assert Case.objects.filter(org=org_a).count() == 1

    def test_empty_setting_refuses_every_confirmation(
        self, anon, org_a, signing_key, settings
    ):
        settings.INBOUND_SNS_ACCOUNT_IDS = frozenset()
        mailbox = _make_mailbox(org_a, topic_arn="")

        for topic in (ATTACKER_TOPIC, OWNER_TOPIC):
            response, subscribed = _confirm(anon, mailbox, signing_key, topic)
            assert response.status_code == 403
            assert subscribed is False
        mailbox.refresh_from_db()
        assert mailbox.topic_arn == ""

    def test_a_different_allowed_account_does_not_admit_the_attacker(
        self, anon, org_a, signing_key, settings
    ):
        settings.INBOUND_SNS_ACCOUNT_IDS = frozenset({OWNER_ACCOUNT})
        mailbox = _make_mailbox(org_a, topic_arn="")

        response, subscribed = _confirm(anon, mailbox, signing_key, ATTACKER_TOPIC)

        assert response.status_code == 403
        assert response.json() == REJECTED
        assert subscribed is False
        mailbox.refresh_from_db()
        assert mailbox.topic_arn == ""


# ---------------------------------------------------------------------------
# The two ways a pin is allowed to happen
# ---------------------------------------------------------------------------


class TestAllowedPins:
    def test_a_confirmation_from_an_allowed_account_pins(
        self, anon, org_a, signing_key, settings
    ):
        settings.INBOUND_SNS_ACCOUNT_IDS = frozenset({ATTACKER_ACCOUNT, OWNER_ACCOUNT})
        mailbox = _make_mailbox(org_a, topic_arn="")

        response, subscribed = _confirm(anon, mailbox, signing_key, OWNER_TOPIC)

        assert response.status_code == 200, response.content
        assert subscribed is True
        mailbox.refresh_from_db()
        assert mailbox.topic_arn == OWNER_TOPIC

        # Pinned now: the same account's other topic is refused, and so is a
        # later confirmation trying to move the pin.
        response, subscribed = _confirm(anon, mailbox, signing_key, OWNER_OTHER_TOPIC)
        assert response.status_code == 403
        assert subscribed is False
        mailbox.refresh_from_db()
        assert mailbox.topic_arn == OWNER_TOPIC

    def test_an_admin_entered_arn_accepts_exactly_that_topic(
        self, anon, admin_client, org_a, signing_key, settings
    ):
        # The account is even allowed; the entered ARN still wins.
        settings.INBOUND_SNS_ACCOUNT_IDS = frozenset({OWNER_ACCOUNT})
        mailbox = _make_mailbox(org_a, topic_arn="")
        put = admin_client.put(
            f"{MAILBOXES_URL}{mailbox.id}/", {"topic_arn": OWNER_TOPIC}, format="json"
        )
        assert put.status_code == 200, put.content

        for topic in (ATTACKER_TOPIC, OWNER_OTHER_TOPIC):
            response, subscribed = _confirm(anon, mailbox, signing_key, topic)
            assert response.status_code == 403
            assert subscribed is False
            assert _notify(anon, mailbox, signing_key, topic).status_code == 403
        assert Case.objects.filter(org=org_a).count() == 0

        response, subscribed = _confirm(anon, mailbox, signing_key, OWNER_TOPIC)
        assert response.status_code == 200, response.content
        assert subscribed is True
        assert _notify(anon, mailbox, signing_key, OWNER_TOPIC).status_code == 200
        assert Case.objects.filter(org=org_a).count() == 1
        mailbox.refresh_from_db()
        assert mailbox.topic_arn == OWNER_TOPIC

    def test_an_admin_entered_arn_works_with_no_allowed_account(
        self, anon, org_a, signing_key, settings
    ):
        settings.INBOUND_SNS_ACCOUNT_IDS = frozenset()
        mailbox = _make_mailbox(org_a, topic_arn=OWNER_TOPIC)

        response, subscribed = _confirm(anon, mailbox, signing_key, OWNER_TOPIC)

        assert response.status_code == 200, response.content
        assert subscribed is True

    def test_a_notification_before_any_pin_is_refused(
        self, anon, org_a, signing_key, settings
    ):
        """Only a confirmation can pin, even from an allowed account."""
        settings.INBOUND_SNS_ACCOUNT_IDS = frozenset({OWNER_ACCOUNT})
        mailbox = _make_mailbox(org_a, topic_arn="")

        response = _notify(anon, mailbox, signing_key, OWNER_TOPIC)

        assert response.status_code == 403
        assert response.json() == REJECTED
        assert Case.objects.filter(org=org_a).count() == 0
        mailbox.refresh_from_db()
        assert mailbox.topic_arn == ""


# ---------------------------------------------------------------------------
# Strict ARN parsing
# ---------------------------------------------------------------------------

# Wrong shapes. The API refuses each one with a 400.
BAD_SHAPES = [
    f"arn:aws-cn:sns:cn-north-1:{OWNER_ACCOUNT}:t",  # China partition
    f"arn:aws-us-gov:sns:us-gov-west-1:{OWNER_ACCOUNT}:t",  # GovCloud partition
    f"arn:aws:sqs:us-east-1:{OWNER_ACCOUNT}:t",  # not SNS
    f"arn:aws:sns:us-east-1:{OWNER_ACCOUNT}:t.fifo",  # SES cannot publish to FIFO
    f"arn:aws:sns:us-east-1:{OWNER_ACCOUNT}",  # no topic name
    f"arn:aws:sns:us-east-1:{OWNER_ACCOUNT}:t:extra",
    f"arn:aws:sns::{OWNER_ACCOUNT}:t",  # no region
    "arn:aws:sns:us-east-1:12345678901:t",  # 11 digits
    "arn:aws:sns:us-east-1:1234567890123:t",  # 13 digits
    "arn:aws:sns:us-east-1:12345678901x:t",
    f"arn:aws:sns:us-east-1:{OWNER_ACCOUNT}:" + "t" * 257,
]
# A well-formed ARN with whitespace around it. The webhook compares bytes and
# refuses these; the API trims them like every other text field, so it does not.
MALFORMED_ARNS = [
    *BAD_SHAPES,
    f"{OWNER_TOPIC}\n",  # `$` would match before this newline; fullmatch does not
    f" {OWNER_TOPIC}",
    "",
]


class TestStrictArnParsing:
    def test_a_well_formed_arn_yields_its_account(self):
        assert topic_account_id(OWNER_TOPIC) == OWNER_ACCOUNT
        assert topic_account_id(ATTACKER_TOPIC) == ATTACKER_ACCOUNT
        assert (
            topic_account_id(f"arn:aws:sns:ap-southeast-2:{OWNER_ACCOUNT}:A_b-9")
            == OWNER_ACCOUNT
        )

    @pytest.mark.parametrize("arn", [*MALFORMED_ARNS, None, 123, [OWNER_TOPIC]])
    def test_anything_else_yields_none(self, arn):
        assert topic_account_id(arn) is None

    @pytest.mark.parametrize("arn", MALFORMED_ARNS)
    def test_a_malformed_arn_in_a_confirmation_pins_nothing(
        self, anon, org_a, signing_key, settings, arn
    ):
        settings.INBOUND_SNS_ACCOUNT_IDS = frozenset({OWNER_ACCOUNT})
        mailbox = _make_mailbox(org_a, topic_arn="")

        response, subscribed = _confirm(anon, mailbox, signing_key, arn)

        assert response.status_code == 403
        assert subscribed is False
        mailbox.refresh_from_db()
        assert mailbox.topic_arn == ""


# ---------------------------------------------------------------------------
# The setting is checked when the process starts
# ---------------------------------------------------------------------------


def _import_settings(account_ids, env_type):
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in (
            "SECRET_KEY",
            "ENV_TYPE",
            "FRONTEND_URL",
            "DOMAIN_NAME",
            "INBOUND_SNS_ACCOUNT_IDS",
        )
    }
    env["SECRET_KEY"] = "k" * 48
    env["ENV_TYPE"] = env_type
    env["FRONTEND_URL"] = "https://app.example.com"
    env["DOMAIN_NAME"] = "https://api.example.com"
    env["INBOUND_SNS_ACCOUNT_IDS"] = account_ids
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import crm.settings as s; print(sorted(s.INBOUND_SNS_ACCOUNT_IDS))",
        ],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


class TestSettingValidation:
    @pytest.mark.parametrize(
        "value",
        [
            "12345678901",
            "1234567890123",
            "arn:aws:sns:us-east-1:123456789012:t",
            "123456789012,abc",
            "１２３４５６７８９０１２",  # full-width digits are not ASCII
        ],
    )
    @pytest.mark.parametrize("env_type", ["production", "dev"])
    def test_a_malformed_id_refuses_to_start(self, value, env_type):
        result = _import_settings(value, env_type)
        assert result.returncode != 0
        assert "INBOUND_SNS_ACCOUNT_IDS" in result.stderr

    def test_ids_are_split_on_commas_and_trimmed(self):
        result = _import_settings(" 123456789012 , 210987654321,", "production")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "['123456789012', '210987654321']"

    def test_unset_allows_no_account(self):
        result = _import_settings("", "production")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "[]"


# ---------------------------------------------------------------------------
# Admin entry of the Topic ARN through the API
# ---------------------------------------------------------------------------


class TestAdminEntersTheArn:
    def test_admin_can_set_change_and_clear_it(self, admin_client, org_a):
        mailbox = _make_mailbox(org_a, topic_arn="")
        url = f"{MAILBOXES_URL}{mailbox.id}/"

        for value in (OWNER_TOPIC, OWNER_OTHER_TOPIC, ""):
            response = admin_client.put(url, {"topic_arn": value}, format="json")
            assert response.status_code == 200, response.content
            assert response.json()["topic_arn"] == value
            assert response.json()["has_topic_arn"] is bool(value)
            mailbox.refresh_from_db()
            assert mailbox.topic_arn == value

    def test_admin_can_set_it_on_create(self, admin_client, org_a):
        response = admin_client.post(
            MAILBOXES_URL,
            {"address": "new@acme.com", "provider": "ses", "topic_arn": OWNER_TOPIC},
            format="json",
        )
        assert response.status_code == 201, response.content
        assert response.json()["topic_arn"] == OWNER_TOPIC
        assert response.json()["has_topic_arn"] is True

    @pytest.mark.parametrize("arn", BAD_SHAPES)
    def test_a_malformed_arn_is_a_400(self, admin_client, org_a, arn):
        mailbox = _make_mailbox(org_a, topic_arn=OWNER_TOPIC)

        response = admin_client.put(
            f"{MAILBOXES_URL}{mailbox.id}/", {"topic_arn": arn}, format="json"
        )

        assert response.status_code == 400
        assert "topic_arn" in response.json()["errors"]
        mailbox.refresh_from_db()
        assert mailbox.topic_arn == OWNER_TOPIC

    def test_a_malformed_arn_on_create_is_a_400(self, admin_client, org_a):
        response = admin_client.post(
            MAILBOXES_URL,
            {"address": "new@acme.com", "provider": "ses", "topic_arn": "nope"},
            format="json",
        )
        assert response.status_code == 400
        assert "topic_arn" in response.json()["errors"]

    def test_admin_reads_it(self, admin_client, org_a):
        _make_mailbox(org_a, topic_arn=OWNER_TOPIC)
        row = admin_client.get(MAILBOXES_URL).json()["mailboxes"][0]
        assert row["topic_arn"] == OWNER_TOPIC
        assert row["has_topic_arn"] is True

    def test_member_reads_only_whether_it_is_set(self, user_client, org_a):
        mailbox = _make_mailbox(org_a, topic_arn=OWNER_TOPIC)
        row = user_client.get(MAILBOXES_URL).json()["mailboxes"][0]
        assert "topic_arn" not in row
        assert row["has_topic_arn"] is True
        detail = user_client.get(f"{MAILBOXES_URL}{mailbox.id}/").json()
        assert "topic_arn" not in detail
        assert detail["has_topic_arn"] is True

    @pytest.mark.parametrize("value", [ATTACKER_TOPIC, ""])
    def test_member_cannot_write_it(self, user_client, org_a, value):
        mailbox = _make_mailbox(org_a, topic_arn=OWNER_TOPIC)

        response = user_client.put(
            f"{MAILBOXES_URL}{mailbox.id}/", {"topic_arn": value}, format="json"
        )

        assert response.status_code == 403
        mailbox.refresh_from_db()
        assert mailbox.topic_arn == OWNER_TOPIC

    def test_member_cannot_create_one_with_it(self, user_client, org_a):
        response = user_client.post(
            MAILBOXES_URL,
            {"address": "x@acme.com", "provider": "ses", "topic_arn": ATTACKER_TOPIC},
            format="json",
        )
        assert response.status_code == 403
