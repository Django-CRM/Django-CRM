"""Sending one delivery: signature, retries, give-up, 410, Slack, claims, pruning."""

import hashlib
import hmac
import json
import time
from datetime import timedelta
from unittest import mock

import pytest
import urllib3
from django.utils import timezone

from common.models import Org
from webhooks import ssrf, tasks
from webhooks.emit import envelope, queue_delivery
from webhooks.models import FAILED, PENDING, SLACK, SUCCEEDED, WebhookDelivery


@pytest.fixture
def delivery(endpoint):
    payload = envelope(endpoint.org_id, "lead.created", {"id": "abc", "title": "Hi"})
    return queue_delivery(endpoint, "lead.created", payload)


def _send(delivery_id, status=None, raises=None):
    org_id = WebhookDelivery.objects.values_list("org_id", flat=True).get(
        pk=delivery_id
    )
    with mock.patch("webhooks.tasks.ssrf.post") as post:
        if raises is not None:
            post.side_effect = raises
        else:
            post.return_value = status
        result = tasks.attempt_delivery(delivery_id, org_id)
    return result, post


def _make_due(delivery):
    WebhookDelivery.objects.filter(pk=delivery.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )


class TestSignature:
    def test_known_vector(self):
        body = b'{"a":1}'
        expected = hmac.new(b"whsec_x", b'1700000000.{"a":1}', hashlib.sha256)
        assert tasks.sign("whsec_x", 1700000000, body) == (
            f"t=1700000000,v1={expected.hexdigest()}"
        )

    def test_request_is_signed_over_timestamp_and_exact_body(self, delivery):
        before = int(time.time())
        body, headers = tasks.build_request(delivery)
        after = int(time.time())

        signature = headers["X-BottleCRM-Signature"]
        parts = dict(item.split("=", 1) for item in signature.split(","))
        timestamp = int(parts["t"])
        assert before <= timestamp <= after
        secret = delivery.endpoint.secret.encode()
        expected = hmac.new(
            secret, f"{timestamp}.".encode() + body, hashlib.sha256
        ).hexdigest()
        assert hmac.compare_digest(parts["v1"], expected)
        # A different secret does not verify.
        wrong = hmac.new(b"other", f"{timestamp}.".encode() + body, hashlib.sha256)
        assert parts["v1"] != wrong.hexdigest()

        assert json.loads(body) == delivery.payload
        assert headers["Content-Type"] == "application/json"
        assert headers["User-Agent"] == "BottleCRM-Webhooks"
        assert headers["X-BottleCRM-Event"] == "lead.created"
        assert headers["X-BottleCRM-Delivery"] == str(delivery.id)

    def test_slack_format_sends_text_and_escapes_mentions(self, endpoint):
        endpoint.format = SLACK
        endpoint.save()
        payload = envelope(
            endpoint.org_id, "lead.created", {"title": "<!channel> & <@U1>"}
        )
        delivery = queue_delivery(endpoint, "lead.created", payload)
        body, _ = tasks.build_request(delivery)
        text = json.loads(body)["text"]
        assert "<" not in text and ">" not in text
        assert "&lt;!channel&gt; &amp; &lt;@U1&gt;" in text
        assert text.startswith("BottleCRM: Lead created:")


class TestAttempts:
    def test_a_2xx_succeeds(self, delivery):
        result, post = _send(delivery.pk, status=204)
        assert result.status == SUCCEEDED
        assert result.attempts == 1
        assert result.response_status == 204
        assert result.next_attempt_at is None
        url, body, headers, timeout = post.call_args.args
        assert url == delivery.endpoint.url
        assert timeout == 10

    def test_retry_schedule_then_give_up(self, delivery):
        for attempt, backoff in enumerate(tasks.BACKOFF, start=1):
            result, _ = _send(delivery.pk, status=500)
            assert result.status == PENDING
            assert result.attempts == attempt
            assert result.next_attempt_at - result.last_attempt_at == backoff
            assert result.error == "The endpoint answered HTTP 500."
            _make_due(result)
        result, _ = _send(delivery.pk, status=503)
        assert result.attempts == tasks.MAX_ATTEMPTS == len(tasks.BACKOFF) + 1
        assert result.status == FAILED
        assert result.next_attempt_at is None
        # Settled: nothing further is attempted.
        again, post = _send(delivery.pk, status=200)
        assert again is None
        post.assert_not_called()

    def test_a_410_turns_the_endpoint_off(self, delivery):
        result, _ = _send(delivery.pk, status=410)
        assert result.status == FAILED
        assert result.next_attempt_at is None
        endpoint = delivery.endpoint
        endpoint.refresh_from_db()
        assert endpoint.is_active is False
        assert "410" in endpoint.disabled_reason

    def test_a_redirect_is_a_failure_not_followed(self, delivery):
        result, _ = _send(delivery.pk, status=302)
        assert result.status == PENDING
        assert result.error == "The endpoint answered HTTP 302."

    def test_network_errors_are_described_without_their_own_text(self, endpoint):
        endpoint.url = "https://hooks.example.com/in?token=s3cret"
        endpoint.save()
        payload = envelope(endpoint.org_id, "lead.created", {})
        delivery = queue_delivery(endpoint, "lead.created", payload)
        failure = urllib3.exceptions.NewConnectionError(
            None, f"failed for {endpoint.url}"
        )
        result, _ = _send(delivery.pk, raises=failure)
        assert result.status == PENDING
        assert result.error == "Could not connect to the endpoint."
        assert "s3cret" not in result.error

    def test_timeout_is_described(self, delivery):
        result, _ = _send(
            delivery.pk, raises=urllib3.exceptions.ReadTimeoutError(None, "/", "x")
        )
        assert result.error == "Timed out after 10 seconds."

    def test_a_refused_destination_at_send_time_is_recorded(self, delivery):
        result, _ = _send(
            delivery.pk,
            raises=ssrf.UnsafeDestination(
                "The URL points at a private or reserved network address."
            ),
        )
        assert result.status == PENDING
        assert "private or reserved" in result.error

    def test_an_attempt_that_is_not_due_is_not_made(self, delivery):
        WebhookDelivery.objects.filter(pk=delivery.pk).update(
            next_attempt_at=timezone.now() + timedelta(minutes=5)
        )
        result, post = _send(delivery.pk, status=200)
        assert result is None
        post.assert_not_called()

    def test_a_claimed_attempt_is_not_made_twice(self, delivery):
        first, _ = _send(delivery.pk, status=500)
        # The immediate send and the sweeper both fire for the same retry.
        _make_due(first)
        with mock.patch("webhooks.tasks.ssrf.post", return_value=500) as post:
            tasks.attempt_delivery(delivery.pk, delivery.org_id)
            tasks.attempt_delivery(delivery.pk, delivery.org_id)
        assert post.call_count == 1

    def test_another_orgs_id_cannot_claim_the_delivery(self, delivery, org_b):
        """The claim is scoped to the org the task was queued for, so a
        delivery never goes out under another tenant's context."""
        with mock.patch("webhooks.tasks.ssrf.post", return_value=200) as post:
            assert tasks.attempt_delivery(delivery.pk, org_b.id) is None
        post.assert_not_called()
        delivery.refresh_from_db()
        assert delivery.status == PENDING
        assert delivery.attempts == 0

    def test_a_turned_off_endpoint_is_not_called(self, delivery):
        delivery.endpoint.is_active = False
        delivery.endpoint.save()
        result, post = _send(delivery.pk, status=200)
        post.assert_not_called()
        assert result.status == FAILED

    def test_the_task_sets_the_delivery_org_context_first(self, delivery):
        calls = []
        with (
            mock.patch(
                "webhooks.tasks.set_rls_context",
                side_effect=lambda org: calls.append(("set", org)),
            ),
            mock.patch(
                "webhooks.tasks.attempt_delivery",
                side_effect=lambda pk, org: calls.append(("attempt", pk, org)),
            ),
            mock.patch(
                "webhooks.tasks.clear_rls_context",
                side_effect=lambda: calls.append(("clear",)),
            ),
        ):
            tasks.deliver_webhook(str(delivery.pk), str(delivery.org_id))
        assert calls == [
            ("set", str(delivery.org_id)),
            ("attempt", str(delivery.pk), str(delivery.org_id)),
            ("clear",),
        ]


class TestSweepAndPrune:
    def test_only_due_pending_deliveries_are_dispatched(self, endpoint):
        payload = envelope(endpoint.org_id, "lead.created", {})
        due = queue_delivery(endpoint, "lead.created", payload)
        later = queue_delivery(endpoint, "lead.created", payload)
        done = queue_delivery(endpoint, "lead.created", payload)
        WebhookDelivery.objects.filter(pk=later.pk).update(
            next_attempt_at=timezone.now() + timedelta(hours=1)
        )
        WebhookDelivery.objects.filter(pk=done.pk).update(
            status=SUCCEEDED, next_attempt_at=None
        )
        with mock.patch("webhooks.tasks.deliver_webhook.delay") as delay:
            assert tasks.dispatch_due_deliveries() == 1
        delay.assert_called_once_with(str(due.pk), str(endpoint.org_id))

    def test_old_deliveries_are_pruned(self, endpoint):
        payload = envelope(endpoint.org_id, "lead.created", {})
        old = queue_delivery(endpoint, "lead.created", payload)
        recent = queue_delivery(endpoint, "lead.created", payload)
        WebhookDelivery.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=tasks.RETENTION_DAYS + 1)
        )
        assert tasks.prune_webhook_deliveries() == 1
        assert list(WebhookDelivery.objects.values_list("pk", flat=True)) == [recent.pk]

    def test_one_due_delivery_among_three_orgs_is_queued_once(self, endpoint, org_b):
        """Without the org filter, a database that does not enforce RLS
        (SQLite, a superuser role) queued it once per org, each under the
        wrong org id but one."""
        Org.objects.create(name="Test Organization C")
        payload = envelope(endpoint.org_id, "lead.created", {})
        due = queue_delivery(endpoint, "lead.created", payload)
        with mock.patch("webhooks.tasks.deliver_webhook.delay") as delay:
            assert tasks.dispatch_due_deliveries() == 1
        delay.assert_called_once_with(str(due.pk), str(endpoint.org_id))

    def test_prune_deletes_only_within_the_org_being_walked(self, endpoint, org_b):
        payload = envelope(endpoint.org_id, "lead.created", {})
        old = queue_delivery(endpoint, "lead.created", payload)
        WebhookDelivery.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=tasks.RETENTION_DAYS + 1)
        )
        with mock.patch.object(tasks.Org.objects, "values_list") as orgs:
            orgs.return_value.iterator.return_value = iter([org_b.id])
            assert tasks.prune_webhook_deliveries() == 0
        assert WebhookDelivery.objects.filter(pk=old.pk).exists()
        assert tasks.prune_webhook_deliveries() == 1


def test_a_broker_outage_leaves_the_delivery_for_the_sweeper(
    endpoint, django_capture_on_commit_callbacks
):
    payload = envelope(endpoint.org_id, "lead.created", {})
    with mock.patch(
        "webhooks.emit.deliver_webhook.delay", side_effect=ConnectionError("down")
    ):
        with django_capture_on_commit_callbacks(execute=True):
            delivery = queue_delivery(endpoint, "lead.created", payload)
    delivery.refresh_from_db()
    assert delivery.status == PENDING
    with mock.patch("webhooks.tasks.deliver_webhook.delay") as delay:
        assert tasks.dispatch_due_deliveries() == 1
    delay.assert_called_once_with(str(delivery.pk), str(endpoint.org_id))
