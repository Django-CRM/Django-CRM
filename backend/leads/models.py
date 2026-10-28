from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.base import SAMPLE_DATA_HELP_TEXT, AssignableMixin, BaseModel, BaseOrgModel
from common.models import Org, Profile, Tags, Teams
from common.phones import set_phone_e164
from common.utils import (
    COUNTRIES,
    CURRENCY_CODES,
    INDCHOICES,
    LEAD_STATUS,
)
from common.validators import flexible_phone_validator
from contacts.models import Contact

# Cleanup notes:
# - Removed 'created_from_site' flag (over-engineered)
# - Removed conversion tracking fields (converted_account, converted_contact,
#   converted_opportunity, conversion_date) - never populated, conversion just sets status
# - Removed 'created_on_arrow' property (frontend computes its own timestamps)


class Lead(AssignableMixin, BaseModel):
    """
    Lead model for CRM - Streamlined for modern sales workflow
    Based on Twenty CRM and Salesforce patterns
    """

    # Core Lead Information
    title = models.CharField(
        _("Title"),
        max_length=255,
        blank=True,
        null=True,
        help_text="Lead name/subject (e.g., 'Enterprise Deal', 'Website Inquiry')",
    )
    salutation = models.CharField(
        _("Salutation"),
        max_length=64,
        blank=True,
        null=True,
        help_text="e.g., Mr, Mrs, Ms, Dr",
    )
    first_name = models.CharField(_("First name"), null=True, max_length=255)
    last_name = models.CharField(_("Last name"), null=True, max_length=255)
    email = models.EmailField(null=True, blank=True)
    phone = models.CharField(
        _("Phone"),
        max_length=25,
        null=True,
        blank=True,
        validators=[flexible_phone_validator],
    )
    phone_e164 = models.CharField(max_length=32, blank=True, default="", db_index=True)
    job_title = models.CharField(
        _("Job Title"),
        max_length=255,
        blank=True,
        null=True,
        help_text="Person's job title (e.g., 'VP of Sales', 'CTO')",
    )
    website = models.CharField(_("Website"), max_length=255, blank=True, null=True)
    linkedin_url = models.URLField(
        _("LinkedIn URL"), max_length=500, blank=True, null=True
    )

    # Sales Pipeline
    status = models.CharField(
        _("Status"), max_length=255, blank=True, null=True, choices=LEAD_STATUS
    )
    # A key from the org's LeadSource list (leads.sources).
    source = models.CharField(_("Source"), max_length=255, blank=True, null=True)
    source_detail = models.CharField(
        _("Sub-source"), max_length=100, blank=True, default=""
    )
    source_ref = models.CharField(
        _("Reference"), max_length=255, blank=True, default=""
    )
    industry = models.CharField(
        _("Industry"), max_length=255, choices=INDCHOICES, blank=True, null=True
    )
    rating = models.CharField(
        _("Rating"),
        max_length=10,
        blank=True,
        null=True,
        choices=[("HOT", "Hot"), ("WARM", "Warm"), ("COLD", "Cold")],
    )
    opportunity_amount = models.DecimalField(
        _("Deal Value"), decimal_places=2, max_digits=12, blank=True, null=True
    )
    currency = models.CharField(
        _("Currency"), max_length=3, choices=CURRENCY_CODES, blank=True, null=True
    )
    probability = models.IntegerField(
        _("Win Probability %"), default=0, blank=True, null=True
    )
    close_date = models.DateField(_("Expected Close Date"), default=None, null=True)

    # Address
    address_line = models.CharField(_("Address"), max_length=255, blank=True, null=True)
    city = models.CharField(_("City"), max_length=255, blank=True, null=True)
    state = models.CharField(_("State"), max_length=255, blank=True, null=True)
    postcode = models.CharField(_("Postal Code"), max_length=64, blank=True, null=True)
    country = models.CharField(
        _("Country"), max_length=3, choices=COUNTRIES, blank=True, null=True
    )

    # Assignment
    assigned_to = models.ManyToManyField(Profile, related_name="lead_assigned_users")
    teams = models.ManyToManyField(Teams, related_name="lead_teams")

    # Activity Tracking
    last_contacted = models.DateField(_("Last Contacted"), blank=True, null=True)
    next_follow_up = models.DateField(_("Next Follow-up"), blank=True, null=True)
    description = models.TextField(_("Notes"), blank=True, null=True)

    # System Fields
    is_active = models.BooleanField(default=True)
    is_sample = models.BooleanField(default=False, help_text=SAMPLE_DATA_HELP_TEXT)
    tags = models.ManyToManyField(Tags, related_name="lead_tags", blank=True)
    contacts = models.ManyToManyField(Contact, related_name="lead_contacts")
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="leads")
    company_name = models.CharField(
        _("Company Name"), max_length=255, blank=True, null=True
    )

    # Kanban/Pipeline support
    stage = models.ForeignKey(
        "LeadStage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
        help_text="Current pipeline stage (null = use status-based kanban)",
    )
    kanban_order = models.DecimalField(
        _("Kanban Order"),
        max_digits=15,
        decimal_places=6,
        default=0,
        help_text="Order within the kanban column for drag-drop",
    )

    custom_fields = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-org schema extension; values are validated against common.CustomFieldDefinition.",
    )

    # Where an arrival from outside came from ("intake:<id>", "email:<mailbox
    # id>", "webform:<id>") and that sender's own id for it, so a retry is
    # recognised. Server-set only.
    external_source = models.CharField(max_length=100, blank=True, default="")
    external_id = models.CharField(max_length=255, blank=True, default="")
    last_enquiry_at = models.DateTimeField(null=True, blank=True)

    # Recomputed from the lead's outbound interactions after every change to
    # them (`interactions.service`). Server-set only.
    first_response_at = models.DateTimeField(null=True, blank=True)
    first_response_by = models.ForeignKey(
        Profile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="first_responses",
    )
    last_outbound_at = models.DateTimeField(null=True, blank=True)
    # When `leads.tasks.remind_waiting_leads` last reminded anyone about this
    # lead; it reminds once per arrival. Server-set only.
    waiting_reminded_at = models.DateTimeField(null=True, blank=True)
    # When a save last made the lead won or lost (`leads.outcomes.stamp`); at
    # most one is set. Server-set only.
    won_at = models.DateTimeField(null=True, blank=True)
    lost_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Lead"
        verbose_name_plural = "Leads"
        db_table = "lead"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["source"]),
            models.Index(fields=["org", "-created_at"]),
            models.Index(fields=["stage", "kanban_order"]),
            models.Index(fields=["status", "kanban_order"]),
            # The unanswered list: open statuses, then no outbound or an old one.
            models.Index(fields=["org", "status", "last_outbound_at"]),
            models.Index(fields=["org", "won_at"]),
            models.Index(fields=["org", "lost_at"]),
        ]
        constraints = [
            # Case-insensitive unique email per organization (when email is not null)
            models.UniqueConstraint(
                Lower("email"),
                "org",
                name="unique_lead_email_per_org",
                condition=Q(email__isnull=False) & ~Q(email=""),
            ),
            models.UniqueConstraint(
                fields=["org", "external_source", "external_id"],
                name="unique_lead_external_id_per_org",
                condition=~Q(external_id=""),
            ),
            # Probability must be 0-100
            models.CheckConstraint(
                condition=Q(probability__gte=0) & Q(probability__lte=100),
                name="lead_probability_range",
            ),
            # Opportunity amount must be non-negative
            models.CheckConstraint(
                condition=Q(opportunity_amount__gte=0)
                | Q(opportunity_amount__isnull=True),
                name="lead_amount_non_negative",
            ),
        ]

    def __str__(self):
        name_parts = [self.salutation, self.first_name, self.last_name]
        return " ".join(part for part in name_parts if part) or f"Lead {self.id}"

    @classmethod
    def from_db(cls, db, field_names, values, *, fetch_mode=None):
        # Imported in each method: `leads.outcomes` reads this module's models.
        from leads.outcomes import remember

        lead = super().from_db(db, field_names, values, fetch_mode=fetch_mode)
        remember(lead)
        return lead

    def refresh_from_db(self, using=None, fields=None, from_queryset=None):
        from leads.outcomes import remember

        super().refresh_from_db(using=using, fields=fields, from_queryset=from_queryset)
        remember(self, fields)

    def save(self, *args, **kwargs):
        from leads.outcomes import remember, stamp

        stamp(self, kwargs)
        set_phone_e164(self, kwargs)
        super().save(*args, **kwargs)
        remember(self, kwargs.get("update_fields"))

    def clean(self):
        """Validate lead data."""
        super().clean()
        errors = {}

        # Email required for conversion (need contact info to create Contact)
        if self.status == "converted" and not self.email:
            errors["email"] = _("Email is required to convert lead")

        if errors:
            raise ValidationError(errors)

    @property
    def days_since_last_contact(self) -> int:
        """Return the number of days since last contact or creation."""
        if self.last_contacted:
            return (timezone.localdate() - self.last_contacted).days
        if self.created_at:
            return (timezone.localdate() - timezone.localdate(self.created_at)).days
        return 0

    @property
    def is_stale(self) -> bool:
        """Check if lead is stale (>30 days without contact and not closed/converted)."""
        if self.status in ["converted", "closed"]:
            return False
        return self.days_since_last_contact > 30

    @property
    def days_until_follow_up(self) -> int | None:
        """Return the number of days until next follow-up (negative if overdue)."""
        if not self.next_follow_up:
            return None
        return (self.next_follow_up - timezone.localdate()).days

    @property
    def is_follow_up_overdue(self) -> bool:
        """Check if follow-up date has passed."""
        if not self.next_follow_up:
            return False
        return timezone.localdate() > self.next_follow_up


class LeadPipeline(BaseModel):
    """
    Custom pipeline for organizing leads into stages (Kanban columns).
    Each organization can have multiple pipelines (e.g., Inbound, Outbound, Enterprise).
    """

    name = models.CharField(_("Pipeline Name"), max_length=255)
    description = models.TextField(_("Description"), blank=True, null=True)
    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="lead_pipelines"
    )
    is_default = models.BooleanField(
        default=False,
        help_text=(
            "Sorts this pipeline first in listings (see Meta.ordering) and is "
            "limited to one per org. It does NOT route new records: a lead "
            "created without a stage keeps stage=NULL, which is the supported "
            "status-based kanban mode."
        ),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Lead Pipeline"
        verbose_name_plural = "Lead Pipelines"
        db_table = "lead_pipeline"
        ordering = ("-is_default", "name")
        indexes = [
            models.Index(fields=["org", "-created_at"]),
        ]
        constraints = [
            # Only one default pipeline per org
            models.UniqueConstraint(
                fields=["org"],
                condition=models.Q(is_default=True),
                name="unique_default_pipeline_per_org",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.org.name})"


class LeadStage(BaseModel):
    """
    Stage within a Lead Pipeline (Kanban column).
    """

    STAGE_TYPE_CHOICES = [
        ("open", "Open"),  # Active stages (assigned, in process)
        ("won", "Won"),  # Converted/closed-won
        ("lost", "Lost"),  # Recycled/closed-lost
    ]

    pipeline = models.ForeignKey(
        LeadPipeline, on_delete=models.CASCADE, related_name="stages"
    )
    name = models.CharField(_("Stage Name"), max_length=100)
    order = models.PositiveIntegerField(default=0)
    color = models.CharField(max_length=7, default="#6B7280")  # Hex color

    # Business logic fields
    stage_type = models.CharField(
        max_length=10, choices=STAGE_TYPE_CHOICES, default="open"
    )
    maps_to_status = models.CharField(
        _("Maps to Status"),
        max_length=255,
        blank=True,
        null=True,
        choices=LEAD_STATUS,
        help_text="When lead enters this stage, also update Lead.status",
    )
    win_probability = models.IntegerField(
        _("Default Win Probability %"),
        default=0,
        help_text="Default probability when lead enters this stage",
    )

    # Kanban features
    wip_limit = models.PositiveIntegerField(
        _("WIP Limit"),
        null=True,
        blank=True,
        help_text="Maximum leads allowed in this stage (null = unlimited)",
    )

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="lead_stages")

    class Meta:
        verbose_name = "Lead Stage"
        verbose_name_plural = "Lead Stages"
        db_table = "lead_stage"
        ordering = ("order",)
        unique_together = ("pipeline", "name")
        indexes = [
            models.Index(fields=["org", "order"]),
            models.Index(fields=["pipeline", "order"]),
        ]
        constraints = [
            # Copied onto Lead.probability by the board move, so it has to fit
            # lead_probability_range or every move into this stage is a 500.
            models.CheckConstraint(
                condition=Q(win_probability__gte=0) & Q(win_probability__lte=100),
                name="lead_stage_win_probability_range",
            ),
        ]

    def __str__(self):
        return f"{self.pipeline.name} - {self.name}"

    def save(self, *args, **kwargs):
        if not self.org_id and self.pipeline_id:
            self.org_id = self.pipeline.org_id
        super().save(*args, **kwargs)


class LeadSource(BaseOrgModel):
    """One entry in an org's list of lead sources. `Lead.source`, `Contact.source`
    and `Opportunity.lead_source` hold its `key`. A retired source (`is_active`
    False) stays on the records that have it and cannot be picked for new ones.
    The defaults are created on first use: `leads.sources.org_sources`."""

    key = models.CharField(max_length=64)
    label = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    order = models.IntegerField(default=0)

    class Meta:
        db_table = "lead_source"
        ordering = ("order", "label")
        indexes = [models.Index(fields=["org", "-created_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["org", "key"], name="unique_lead_source_key_per_org"
            ),
        ]

    def __str__(self):
        return self.label


class LeadIntake(BaseOrgModel):
    """A lead webhook. Its id is the public URL's and never changes. Its key can
    be replaced, the old one kept in `previous_key` until
    `previous_key_expires_at`. Keys are stored as written, like outbound webhook
    secrets, because a signing preset can only be checked with the key itself.
    """

    PRESET_GENERIC = "generic"
    PRESET_CHOICES = (
        (PRESET_GENERIC, "Generic"),
        ("google_ads", "Google Ads lead form"),
        ("tally", "Tally"),
        ("gravity_forms", "Gravity Forms"),
    )

    name = models.CharField(max_length=100)
    preset = models.CharField(
        max_length=32, choices=PRESET_CHOICES, default=PRESET_GENERIC
    )
    key = models.CharField(max_length=64)
    previous_key = models.CharField(max_length=64, blank=True, default="")
    previous_key_expires_at = models.DateTimeField(null=True, blank=True)
    # A list of {"from": "<dot path>", "to": "<lead field>"}.
    mapping = models.JSONField(default=list, blank=True)
    default_source = models.CharField(max_length=64, blank=True, default="")
    source_detail = models.CharField(max_length=100, blank=True, default="")
    tags = models.ManyToManyField(Tags, blank=True, related_name="lead_intakes")
    assignee = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_lead_intakes",
    )
    is_active = models.BooleanField(default=True)
    last_received_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "lead_intake"
        ordering = ("name",)
        indexes = [models.Index(fields=["org", "-created_at"])]

    def __str__(self):
        return self.name

    def drop_old_events(self):
        """Delete this webhook's deliveries older than `EVENT_DAYS`. They hold
        personal data, and no scheduler runs, so each post and each admin read
        of the webhook calls this."""
        cutoff = timezone.now() - timedelta(days=LeadIntakeEvent.EVENT_DAYS)
        LeadIntakeEvent.objects.filter(
            org_id=self.org_id, intake=self, received_at__lt=cutoff
        ).delete()


class LeadIntakeEvent(BaseOrgModel):
    """One delivery to a `LeadIntake`, whatever its outcome."""

    EVENT_DAYS = 30

    RESULT_CHOICES = (
        ("created", "Created"),
        ("merged", "Merged"),
        ("duplicate", "Duplicate"),
        ("test", "Test"),
        ("rejected", "Rejected"),
    )

    intake = models.ForeignKey(
        LeadIntake, on_delete=models.CASCADE, related_name="events"
    )
    received_at = models.DateTimeField(default=timezone.now)
    result = models.CharField(max_length=16, choices=RESULT_CHOICES)
    lead = models.ForeignKey(
        Lead,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="intake_events",
    )
    external_id = models.CharField(max_length=255, blank=True, default="")
    error = models.TextField(blank=True, default="")
    payload = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "lead_intake_event"
        ordering = ("-received_at",)
        indexes = [
            models.Index(fields=["org", "-created_at"]),
            models.Index(fields=["intake", "received_at"]),
        ]

    def __str__(self):
        return f"{self.result} at {self.received_at}"
