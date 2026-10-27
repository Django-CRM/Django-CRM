"""
Django signals for automatic activity tracking across CRM entities.

This module registers post_save and post_delete signals on all major CRM models
to automatically create Activity records when entities are created, updated, or deleted.
"""

from crum import get_current_request
from django.db.models.signals import post_delete, post_save
from django.dispatch import Signal, receiver

from common.actor import current_actor
from common.models import Activity

pre_merge = Signal()
"""Sent by merge_records before any row moves; arguments: sender (model), keeper, loser."""


def get_entity_name(instance):
    """Get a display name for an entity instance"""
    # Try common name attributes
    if hasattr(instance, "name") and instance.name:
        return str(instance.name)
    if hasattr(instance, "title") and instance.title:
        return str(instance.title)
    if hasattr(instance, "first_name"):
        name = f"{instance.first_name or ''} {instance.last_name or ''}".strip()
        if name:
            return name
    if hasattr(instance, "email") and instance.email:
        return str(instance.email)
    if hasattr(instance, "subject") and instance.subject:
        return str(instance.subject)
    # Fallback to string representation
    return str(instance)[:100]


def create_activity(instance, action, entity_type, metadata=None):
    """Create an activity record for a model instance, merging `metadata` into
    the row's own.

    Credited to the `common.actor` override when one is set, even with no
    request, else to the request's profile; with neither, nothing is written.
    """
    override = current_actor()
    row_metadata = {}
    if override is not None:
        profile, note = override
        row_metadata = {"automation": note}
    else:
        profile = getattr(get_current_request(), "profile", None)
        if not profile:
            return

    # Get org from instance or profile
    org = getattr(instance, "org", None)
    if not org:
        org = getattr(profile, "org", None)
    if not org:
        return

    # Create activity record
    Activity.objects.create(
        user=profile,
        action=action,
        entity_type=entity_type,
        entity_id=instance.id,
        entity_name=get_entity_name(instance),
        metadata={**row_metadata, **(metadata or {})},
        org=org,
    )


# Account signals
@receiver(post_save, sender="accounts.Account")
def account_post_save(sender, instance, created, **kwargs):
    action = "CREATE" if created else "UPDATE"
    create_activity(instance, action, "Account")


@receiver(post_delete, sender="accounts.Account")
def account_post_delete(sender, instance, **kwargs):
    create_activity(instance, "DELETE", "Account")


# Lead signals
@receiver(post_save, sender="leads.Lead")
def lead_post_save(sender, instance, created, **kwargs):
    action = "CREATE" if created else "UPDATE"
    create_activity(instance, action, "Lead")


@receiver(post_delete, sender="leads.Lead")
def lead_post_delete(sender, instance, **kwargs):
    create_activity(instance, "DELETE", "Lead")


# Contact signals
@receiver(post_save, sender="contacts.Contact")
def contact_post_save(sender, instance, created, **kwargs):
    action = "CREATE" if created else "UPDATE"
    create_activity(instance, action, "Contact")


@receiver(post_delete, sender="contacts.Contact")
def contact_post_delete(sender, instance, **kwargs):
    create_activity(instance, "DELETE", "Contact")


# Opportunity signals
@receiver(post_save, sender="opportunity.Opportunity")
def opportunity_post_save(sender, instance, created, **kwargs):
    action = "CREATE" if created else "UPDATE"
    create_activity(instance, action, "Opportunity")


@receiver(post_delete, sender="opportunity.Opportunity")
def opportunity_post_delete(sender, instance, **kwargs):
    create_activity(instance, "DELETE", "Opportunity")


# Cases use a richer signal handler module. See cases/signals.py for
# STATUS_CHANGED / PRIORITY_CHANGED / ASSIGN / COMMENT / LINKED_SOLUTION etc.


# Task signals
@receiver(post_save, sender="tasks.Task")
def task_post_save(sender, instance, created, **kwargs):
    action = "CREATE" if created else "UPDATE"
    create_activity(instance, action, "Task")


@receiver(post_delete, sender="tasks.Task")
def task_post_delete(sender, instance, **kwargs):
    create_activity(instance, "DELETE", "Task")


# Invoice signals
@receiver(post_save, sender="invoices.Invoice")
def invoice_post_save(sender, instance, created, **kwargs):
    action = "CREATE" if created else "UPDATE"
    create_activity(instance, action, "Invoice")


@receiver(post_delete, sender="invoices.Invoice")
def invoice_post_delete(sender, instance, **kwargs):
    create_activity(instance, "DELETE", "Invoice")
