"""Sending webhook deliveries, retrying them, and pruning the log.

A worker runs no middleware, so every task sets the RLS context for the org it
is working in before any query, and clears it afterwards so the next borrower
of a pooled connection does not inherit it.

RETRIES
A failed attempt (network error, timeout, refused destination, any status other
than 2xx or 410) is retried after `BACKOFF[n]`, and after the last one the
delivery is marked failed. The retry is picked up by `dispatch_due_deliveries`,
which beat runs every minute, rather than by a Celery countdown: a countdown of
hours outlives the Redis broker's visibility timeout and is redelivered, and a
message lost with the broker would strand the row as pending forever. The row
itself is the schedule.

Every attempt first claims its row with a conditional UPDATE, so the immediate
send queued at commit and the sweeper cannot both send the same attempt. The
claim pushes `next_attempt_at` out by `CLAIM`, longer than the request timeout,
so a worker that dies mid-attempt leaves the row to be picked up again.
"""

import hashlib
import hmac
import json
import logging
import socket
import time
from datetime import timedelta

import urllib3
from celery import shared_task
from django.utils import timezone

from common.models import Org
from common.tasks import clear_rls_context, set_rls_context
from webhooks import events, ssrf
from webhooks.models import FAILED, PENDING, SLACK, SUCCEEDED, WebhookDelivery

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10
BACKOFF = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
)
# The first attempt plus one per backoff step.
MAX_ATTEMPTS = len(BACKOFF) + 1
CLAIM = timedelta(minutes=2)
RETENTION_DAYS = 30
USER_AGENT = "BottleCRM-Webhooks"


def sign(secret, timestamp, body):
    """`t=<unix>,v1=<hex HMAC-SHA256 of "<unix>.<body>">`."""
    digest = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def build_request(delivery):
    endpoint = delivery.endpoint
    if endpoint.format == SLACK:
        document = {"text": events.slack_text(delivery.payload)}
    else:
        document = delivery.payload
    body = json.dumps(document, separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-BottleCRM-Event": delivery.event,
        "X-BottleCRM-Delivery": str(delivery.id),
        "X-BottleCRM-Signature": sign(endpoint.secret, int(time.time()), body),
    }
    return body, headers


def describe_failure(exc):
    """A fixed sentence per failure class. Never the exception's own text."""
    if isinstance(exc, ssrf.UnsafeDestination):
        return str(exc)
    if isinstance(exc, urllib3.exceptions.SSLError):
        return "The TLS handshake or certificate check failed."
    # Before the timeout check: urllib3 makes NewConnectionError a subclass of
    # ConnectTimeoutError, so a refused connection would read as a timeout.
    if isinstance(exc, urllib3.exceptions.NewConnectionError):
        return "Could not connect to the endpoint."
    if isinstance(exc, urllib3.exceptions.TimeoutError):
        return f"Timed out after {TIMEOUT_SECONDS} seconds."
    if isinstance(exc, (urllib3.exceptions.HTTPError, socket.error)):
        return "Could not connect to the endpoint."
    return "The delivery failed."


def attempt_delivery(delivery_id, org_id):
    """Make one attempt at a pending delivery of `org_id` that is due. Returns
    the updated delivery, or None if another worker holds it, it is not due, or
    it is not that org's."""
    now = timezone.now()
    claimed = WebhookDelivery.objects.filter(
        pk=delivery_id, org_id=org_id, status=PENDING, next_attempt_at__lte=now
    ).update(next_attempt_at=now + CLAIM)
    if not claimed:
        return None
    delivery = WebhookDelivery.objects.select_related("endpoint").get(pk=delivery_id)
    endpoint = delivery.endpoint

    if not endpoint.is_active:
        WebhookDelivery.objects.filter(pk=delivery_id).update(
            status=FAILED, next_attempt_at=None, error="The endpoint is turned off."
        )
        delivery.refresh_from_db()
        return delivery

    body, headers = build_request(delivery)
    status_code, error = None, ""
    try:
        status_code = ssrf.post(endpoint.url, body, headers, TIMEOUT_SECONDS)
    except Exception as exc:
        error = describe_failure(exc)

    attempts = delivery.attempts + 1
    fields = {
        "attempts": attempts,
        "last_attempt_at": now,
        "response_status": status_code,
        "next_attempt_at": None,
    }
    if status_code is not None and 200 <= status_code < 300:
        fields.update(status=SUCCEEDED, error="")
    elif status_code == 410:
        reason = "The endpoint answered 410 Gone, so it was turned off."
        fields.update(status=FAILED, error=reason)
        type(endpoint).objects.filter(pk=endpoint.pk).update(
            is_active=False, disabled_reason=reason
        )
    else:
        if status_code is not None:
            error = f"The endpoint answered HTTP {status_code}."
        fields["error"] = error
        if attempts < MAX_ATTEMPTS:
            fields.update(status=PENDING, next_attempt_at=now + BACKOFF[attempts - 1])
        else:
            fields["status"] = FAILED
    WebhookDelivery.objects.filter(pk=delivery_id).update(**fields)
    delivery.refresh_from_db()
    return delivery


@shared_task
def deliver_webhook(delivery_id, org_id):
    set_rls_context(org_id)
    try:
        attempt_delivery(delivery_id, org_id)
    finally:
        clear_rls_context()


@shared_task
def dispatch_due_deliveries():
    """Queue every pending delivery whose next attempt is due, org by org.

    `webhook_delivery` is org-scoped, so it is walked through the unscoped
    `organization` table with the context set for each, the shape
    `cases.tasks.scan_for_breached_cases` documents. The query filters on the
    org as well: RLS is the safety net, and where it is not enforced (SQLite, a
    superuser role) the context alone would queue every org's due deliveries
    once per org, each under the wrong org id.
    """
    now = timezone.now()
    queued = 0
    try:
        for org_id in Org.objects.values_list("id", flat=True).iterator():
            set_rls_context(org_id)
            due = WebhookDelivery.objects.filter(
                org_id=org_id, status=PENDING, next_attempt_at__lte=now
            ).values_list("id", flat=True)[:500]
            for delivery_id in due:
                deliver_webhook.delay(str(delivery_id), str(org_id))
                queued += 1
    finally:
        clear_rls_context()
    return queued


@shared_task
def prune_webhook_deliveries(days=RETENTION_DAYS):
    """Delete deliveries older than `days` days, org by org."""
    cutoff = timezone.now() - timedelta(days=days)
    deleted = 0
    try:
        for org_id in Org.objects.values_list("id", flat=True).iterator():
            set_rls_context(org_id)
            removed, _ = WebhookDelivery.objects.filter(
                org_id=org_id, created_at__lt=cutoff
            ).delete()
            deleted += removed
    finally:
        clear_rls_context()
    if deleted:
        logger.info("Pruned %s webhook deliveries older than %s days", deleted, days)
    return deleted
