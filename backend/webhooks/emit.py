"""Turning saved records into queued webhook deliveries.

WHY EVENTS WAIT FOR THE END OF THE REQUEST

A view saves a record and only then sets its many-to-many fields (who it is
assigned to), and some views save the same record twice. An event built at
`post_save` would describe a record nobody is assigned to yet, and a create
would arrive as a create and an update. So inside a request, `record` only
notes what happened, keyed by record, and `WebhookEventsMiddleware` calls
`flush` once the view has returned. Each record is then read back from the
database once: a row that is gone (its transaction rolled back, or it was
deleted later in the request) produces no created/updated event, and a delete
whose row is still there was rolled back and produces nothing either.

Outside a request (a Celery task, a management command) there is nothing to
wait for, so the event is queued when the surrounding transaction commits.

Endpoints are always looked up by the record's own `org_id`, so an event can
only ever reach an endpoint of the org that owns the record.
"""

import logging
import uuid

from crum import get_current_request
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from webhooks import events
from webhooks.models import WebhookDelivery, WebhookEndpoint
from webhooks.tasks import deliver_webhook

logger = logging.getLogger(__name__)

CREATED = "created"
UPDATED = "updated"
DELETED = "deleted"
COMMENT_ADDED = "comment_added"
WON = "won"
LOST = "lost"
PAID = "paid"

_BUFFER_ATTR = "_webhook_events"

# model label -> (event prefix, payload builder)
SPECS = {
    "leads.lead": ("lead", events.lead_data),
    "contacts.contact": ("contact", events.contact_data),
    "accounts.account": ("account", events.account_data),
    "opportunity.opportunity": ("deal", events.deal_data),
    "cases.case": ("ticket", events.ticket_data),
    "common.comment": ("ticket", events.comment_data),
    "invoices.invoice": ("invoice", events.invoice_data),
    "tasks.task": ("task", events.task_data),
}


class _Entry:
    """What happened to one record. The pk and org are copied at once because
    Django sets a deleted instance's pk to None when the delete finishes."""

    def __init__(self, instance):
        self.model = type(instance)
        self.pk = instance.pk
        self.org_id = instance.org_id
        self.actions = []
        self.snapshot = None

    def add(self, action, snapshot):
        if action not in self.actions:
            self.actions.append(action)
        if snapshot is not None:
            self.snapshot = snapshot


def record(instance, action, snapshot=None):
    """Note that `action` happened to `instance`. `snapshot` is the payload of
    a deleted record, built before the row went."""
    if not instance.org_id:
        return
    request = get_current_request()
    if request is None:
        entry = _Entry(instance)
        entry.add(action, snapshot)
        transaction.on_commit(lambda: publish_entries([entry]))
        return
    buffer = request.__dict__.setdefault(_BUFFER_ATTR, {})
    key = (instance._meta.label_lower, instance.pk)
    if key not in buffer:
        buffer[key] = _Entry(instance)
    buffer[key].add(action, snapshot)


def flush(request):
    buffer = request.__dict__.pop(_BUFFER_ATTR, None)
    if buffer:
        publish_entries(buffer.values())


def publish_entries(entries):
    endpoints_by_org = {}
    for entry in entries:
        try:
            _publish_entry(entry, endpoints_by_org)
        except Exception:
            # A failure to queue a webhook must not turn a saved record into a
            # failed request. Logged without the payload.
            logger.exception(
                "Could not queue webhook for %s %s", entry.model.__name__, entry.pk
            )


def _publish_entry(entry, endpoints_by_org):
    if entry.org_id not in endpoints_by_org:
        endpoints_by_org[entry.org_id] = list(
            WebhookEndpoint.objects.filter(org_id=entry.org_id, is_active=True)
        )
    endpoints = endpoints_by_org[entry.org_id]
    if not endpoints:
        return
    prefix, build = SPECS[entry.model._meta.label_lower]
    exists = entry.model._base_manager.filter(pk=entry.pk)

    if DELETED in entry.actions:
        if entry.snapshot is None or exists.exists():
            return
        _fan_out(entry.org_id, f"{prefix}.deleted", entry.snapshot, endpoints)
        return

    candidates = []
    if CREATED in entry.actions:
        candidates.append(CREATED)
    elif UPDATED in entry.actions:
        candidates.append(UPDATED)
    candidates += [a for a in entry.actions if a not in (CREATED, UPDATED)]
    wanted = [
        a for a in candidates if any(f"{prefix}.{a}" in e.events for e in endpoints)
    ]
    if not wanted:
        return
    fresh = exists.first()
    if fresh is None:
        return
    data = None
    for action in wanted:
        if not _still_true(action, fresh):
            continue
        if data is None:
            data = build(fresh)
        _fan_out(entry.org_id, f"{prefix}.{action}", data, endpoints)


def _still_true(action, fresh):
    """Re-check a transition against the row as committed."""
    if action == COMMENT_ADDED:
        return not fresh.is_internal
    if action in (WON, LOST):
        return deal_kind(fresh.pipeline_id, fresh.stage) == action
    if action == PAID:
        return fresh.status == "Paid"
    return True


def deal_kind(pipeline_id, stage):
    """The kind (open/won/lost) of a deal's stage, or None if unknown."""
    from opportunity.models import DealStage

    return (
        DealStage.objects.filter(pipeline_id=pipeline_id, code=stage)
        .values_list("kind", flat=True)
        .first()
    )


def is_ticket_comment(comment):
    from cases.models import Case

    return comment.content_type_id == ContentType.objects.get_for_model(Case).id


def envelope(org_id, event, data):
    return {
        "id": str(uuid.uuid4()),
        "event": event,
        "created_at": timezone.now().isoformat(),
        "org_id": str(org_id),
        "data": events.to_json(data),
    }


def _fan_out(org_id, event, data, endpoints):
    payload = envelope(org_id, event, data)
    for endpoint in endpoints:
        if event in endpoint.events:
            queue_delivery(endpoint, event, payload)


def queue_delivery(endpoint, event, payload):
    """Record a pending delivery and send it once the transaction commits."""
    delivery = WebhookDelivery.objects.create(
        org_id=endpoint.org_id,
        endpoint=endpoint,
        event=event,
        event_id=payload["id"],
        payload=payload,
        next_attempt_at=timezone.now(),
    )
    delivery_id, org_id = str(delivery.id), str(endpoint.org_id)
    transaction.on_commit(lambda: _send(delivery_id, org_id))
    return delivery


def _send(delivery_id, org_id):
    # The row is already pending and due, so if the broker is unreachable the
    # every-minute sweeper sends it. Refusing the save, or skipping the other
    # endpoints of the same event, would lose more than it protects.
    try:
        deliver_webhook.delay(delivery_id, org_id)
    except Exception:
        logger.exception("Could not queue webhook delivery %s", delivery_id)
