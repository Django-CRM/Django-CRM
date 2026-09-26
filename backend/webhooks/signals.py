"""Receivers that feed `webhooks.emit`. Connected by `WebhooksConfig.ready`.

Kept apart from `common/signals.py` and `cases/signals.py` so emitting a
webhook never depends on, or changes, the activity feed's receivers.

Queryset `update()` and `bulk_create()` send no signals, so records changed
that way produce no webhook. That is Django's rule, not one made here.
"""

from django.db.models.signals import post_delete, post_save, pre_delete, pre_save

from webhooks import emit

SAVED = (
    "leads.Lead",
    "contacts.Contact",
    "accounts.Account",
    "opportunity.Opportunity",
    "cases.Case",
    "invoices.Invoice",
    "tasks.Task",
)
# Tickets and invoices have no delete event in the catalogue.
DELETED = (
    "leads.Lead",
    "contacts.Contact",
    "accounts.Account",
    "opportunity.Opportunity",
    "tasks.Task",
)


def on_save(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    emit.record(instance, emit.CREATED if created else emit.UPDATED)
    transition = getattr(instance, "_webhook_transition", None)
    if transition:
        emit.record(instance, transition)
        instance._webhook_transition = None


def on_pre_delete(sender, instance, **kwargs):
    # Built before the delete, while the assignment rows still exist.
    prefix, build = emit.SPECS[instance._meta.label_lower]
    instance._webhook_snapshot = build(instance)


def on_delete(sender, instance, **kwargs):
    emit.record(instance, emit.DELETED, getattr(instance, "_webhook_snapshot", None))


def deal_pre_save(sender, instance, raw=False, **kwargs):
    """Note a move into a won or lost stage, for `deal.won` / `deal.lost`."""
    instance._webhook_transition = None
    if raw:
        return
    old = None
    if not instance._state.adding:
        old = (
            sender._base_manager.filter(pk=instance.pk)
            .values_list("stage", "pipeline_id")
            .first()
        )
    new = (instance.stage, instance.pipeline_id)
    if old == new:
        return
    new_kind = emit.deal_kind(instance.pipeline_id, instance.stage)
    if new_kind not in (emit.WON, emit.LOST):
        return
    if old is None or emit.deal_kind(old[1], old[0]) != new_kind:
        instance._webhook_transition = new_kind


def invoice_pre_save(sender, instance, raw=False, **kwargs):
    """Note an invoice becoming Paid, for `invoice.paid`."""
    instance._webhook_transition = None
    if raw or instance.status != "Paid":
        return
    old = None
    if not instance._state.adding:
        old = (
            sender._base_manager.filter(pk=instance.pk)
            .values_list("status", flat=True)
            .first()
        )
    if old != "Paid":
        instance._webhook_transition = emit.PAID


def comment_saved(sender, instance, created, raw=False, **kwargs):
    """A public reply on a ticket. Internal notes are never sent anywhere."""
    if raw or not created or instance.is_internal:
        return
    if emit.is_ticket_comment(instance):
        emit.record(instance, emit.COMMENT_ADDED)


def connect():
    for label in SAVED:
        post_save.connect(on_save, sender=label, dispatch_uid=f"webhooks_save_{label}")
    for label in DELETED:
        pre_delete.connect(
            on_pre_delete, sender=label, dispatch_uid=f"webhooks_pre_delete_{label}"
        )
        post_delete.connect(
            on_delete, sender=label, dispatch_uid=f"webhooks_delete_{label}"
        )
    pre_save.connect(
        deal_pre_save, sender="opportunity.Opportunity", dispatch_uid="webhooks_deal"
    )
    pre_save.connect(
        invoice_pre_save, sender="invoices.Invoice", dispatch_uid="webhooks_invoice"
    )
    post_save.connect(
        comment_saved, sender="common.Comment", dispatch_uid="webhooks_comment"
    )


connect()
