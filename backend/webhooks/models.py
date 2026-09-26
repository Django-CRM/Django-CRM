"""Outbound webhooks: where an org's events are sent, and what happened to each send."""

import secrets

from django.db import models

from common.base import BaseOrgModel

JSON = "json"
SLACK = "slack"
FORMATS = ((JSON, "Signed JSON"), (SLACK, "Slack incoming webhook"))

PENDING = "pending"
SUCCEEDED = "succeeded"
FAILED = "failed"
DELIVERY_STATUSES = ((PENDING, "Pending"), (SUCCEEDED, "Succeeded"), (FAILED, "Failed"))


def generate_secret():
    """A signing secret. Only ever minted here, never taken from a request."""
    return f"whsec_{secrets.token_urlsafe(32)}"


class WebhookEndpoint(BaseOrgModel):
    """A URL the org's events are POSTed to.

    `secret` is stored as written because every delivery has to sign with it.
    It leaves the server in exactly two responses: the create, and a rotate.
    """

    url = models.URLField(max_length=500)
    description = models.CharField(max_length=255, blank=True, default="")
    # Event names from `webhooks.events.EVENT_NAMES`; the serializer refuses
    # anything else.
    events = models.JSONField(default=list)
    format = models.CharField(max_length=10, choices=FORMATS, default=JSON)
    is_active = models.BooleanField(default=True)
    # Why the server turned the endpoint off (an HTTP 410), shown beside the
    # switch. Cleared when an admin turns it back on.
    disabled_reason = models.CharField(max_length=255, blank=True, default="")
    secret = models.CharField(max_length=100, default=generate_secret, editable=False)

    class Meta:
        db_table = "webhook_endpoint"
        ordering = ("-created_at",)
        # Restated: a subclass Meta replaces BaseOrgModel's, index included.
        indexes = [models.Index(fields=["org", "-created_at"])]

    def __str__(self):
        return self.url

    @property
    def secret_hint(self):
        return f"whsec_...{self.secret[-4:]}"


class WebhookDelivery(BaseOrgModel):
    """One event sent to one endpoint, across all of its attempts.

    A redelivery is a new row carrying the same `event_id` and payload, so the
    log keeps what happened the first time.
    """

    endpoint = models.ForeignKey(
        WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries"
    )
    event = models.CharField(max_length=64)
    event_id = models.UUIDField()
    payload = models.JSONField()
    status = models.CharField(max_length=10, choices=DELIVERY_STATUSES, default=PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    # A short, fixed description of the failure. Never an exception's own
    # text, which can carry the URL and whatever secret its query string holds.
    error = models.CharField(max_length=255, blank=True, default="")
    # When the next attempt is due; null once the delivery has settled.
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "webhook_delivery"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["org", "-created_at"]),
            models.Index(fields=["endpoint", "-created_at"]),
            models.Index(fields=["status", "next_attempt_at"]),
        ]

    def __str__(self):
        return f"{self.event} -> {self.endpoint_id} ({self.status})"
