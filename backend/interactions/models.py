from django.db import models
from django.db.models import Q
from django.utils import timezone

from common.base import BaseOrgModel

KIND_CHOICES = (
    ("call", "Call"),
    ("meeting", "Meeting"),
    ("email", "Email"),
    ("whatsapp", "WhatsApp"),
    ("sms", "SMS"),
    ("enquiry", "Enquiry"),
)
# Every kind but `enquiry`. An outbound one is a response, whoever logged it.
OUTBOUND_KINDS = frozenset({"call", "meeting", "email", "whatsapp", "sms"})
DIRECTION_CHOICES = (("inbound", "Inbound"), ("outbound", "Outbound"))
CALL_OUTCOMES = (
    ("connected", "Connected"),
    ("no_answer", "No answer"),
    ("voicemail", "Voicemail"),
    ("busy", "Busy"),
    ("wrong_number", "Wrong number"),
)
# Each value is also the name of the link it points through.
HOME_CHOICES = (
    ("lead", "Lead"),
    ("contact", "Contact"),
    ("account", "Account"),
    ("opportunity", "Deal"),
    ("case", "Ticket"),
)


def delete_or_unlink(collector, field, sub_objs, using):
    """`on_delete` for the record links: deleting a record deletes the
    interactions it is home to and only unlinks the others. Running inside the
    deletion collector covers queryset and cascaded deletes as well."""
    models.CASCADE(collector, field, sub_objs.filter(home=field.name), using)
    models.SET_NULL(collector, field, sub_objs.exclude(home=field.name), using)


# Hands the rows over as an unevaluated queryset, saving a SELECT per delete.
delete_or_unlink.lazy_sub_objs = True


def _record_link(model):
    return models.ForeignKey(
        model,
        null=True,
        blank=True,
        on_delete=delete_or_unlink,
        related_name="interactions",
    )


class Interaction(BaseOrgModel):
    """A call, meeting, email, WhatsApp message or SMS logged on a record, or an
    enquiry a lead made.

    `home` names the record it was logged on (for an enquiry, the lead or the
    contact it became). Who may see it follows that record alone; the other
    links only add it to those records' timelines.
    """

    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    direction = models.CharField(
        max_length=8, choices=DIRECTION_CHOICES, default="outbound"
    )
    occurred_at = models.DateTimeField(default=timezone.now)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    duration_estimated = models.BooleanField(default=False)
    outcome = models.CharField(
        max_length=16, choices=CALL_OUTCOMES, blank=True, default=""
    )
    subject = models.CharField(max_length=200, blank=True, default="")
    body = models.TextField(max_length=10_000, blank=True, default="")
    # Empty when the system or a connector wrote it.
    logged_by = models.ForeignKey(
        "common.Profile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="logged_interactions",
    )
    participants = models.JSONField(default=list, blank=True)

    lead = _record_link("leads.Lead")
    contact = _record_link("contacts.Contact")
    account = _record_link("accounts.Account")
    opportunity = _record_link("opportunity.Opportunity")
    case = _record_link("cases.Case")
    home = models.CharField(max_length=16, choices=HOME_CHOICES)

    # `manual`, `phone_tap`, `intake`, `email`, `webform` or a connector's key.
    origin = models.CharField(max_length=32, default="manual")
    external_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "interaction"
        ordering = ("-occurred_at", "-created_at")
        indexes = [
            models.Index(fields=["org", "-created_at"]),
            models.Index(
                fields=["org", "updated_at", "id"], name="interaction_org_upd_idx"
            ),
            models.Index(fields=["org", "lead", "occurred_at"]),
            models.Index(fields=["org", "contact", "occurred_at"]),
            models.Index(fields=["org", "opportunity", "occurred_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["org", "origin", "external_id"],
                condition=~Q(external_id=""),
                name="unique_interaction_external_id_per_org",
            ),
            models.CheckConstraint(
                condition=(
                    Q(home="lead", lead__isnull=False)
                    | Q(home="contact", contact__isnull=False)
                    | Q(home="account", account__isnull=False)
                    | Q(home="opportunity", opportunity__isnull=False)
                    | Q(home="case", case__isnull=False)
                ),
                name="interaction_home_is_linked",
            ),
        ]
