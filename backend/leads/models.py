from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.base import SAMPLE_DATA_HELP_TEXT, AssignableMixin, BaseModel
from common.models import Org, Profile, Tags, Teams
from common.utils import (
    COUNTRIES,
    CURRENCY_CODES,
    INDCHOICES,
    LEAD_SOURCE,
    LEAD_STATUS,
)
from common.validators import flexible_phone_validator
from contacts.models import Contact

CAMPAIGN_STATUS_CHOICES = [
    ("draft", "Draft"),
    ("active", "Active"),
    ("paused", "Paused"),
    ("completed", "Completed"),
    ("archived", "Archived"),
]

CAMPAIGN_CHANNEL_CHOICES = [
    ("organic_search", "Organic search"),
    ("paid_search", "Paid search"),
    ("organic_social", "Organic social"),
    ("paid_social", "Paid social"),
    ("email", "Email"),
    ("referral", "Referral"),
    ("event", "Event"),
    ("direct", "Direct"),
    ("partner", "Partner"),
    ("other", "Other"),
]

ATTRIBUTION_TOUCH_CHOICES = [
    ("first", "First touch"),
    ("assist", "Assisted touch"),
    ("last", "Last touch"),
]

LAWFUL_BASIS_CHOICES = [
    ("consent", "Consent"),
    ("legitimate_interest", "Legitimate interest"),
    ("contract", "Contract or pre-contractual steps"),
    ("legal_obligation", "Legal obligation"),
]

DATA_SUBJECT_REQUEST_CHOICES = [
    ("access", "Access"),
    ("correction", "Correction"),
    ("deletion", "Deletion"),
]

DATA_SUBJECT_REQUEST_STATUS_CHOICES = [
    ("submitted", "Submitted"),
    ("verified", "Identity verified"),
    ("rejected", "Rejected without execution"),
]

DATA_SUBJECT_EVENT_CHOICES = [
    ("submitted", "Submitted"),
    ("identity_verified", "Identity verified"),
    ("legal_hold_placed", "Legal hold placed"),
    ("legal_hold_released", "Legal hold released"),
    ("rejected", "Rejected without execution"),
    ("export_prepared", "Encrypted export prepared"),
]

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
    source = models.CharField(
        _("Source"), max_length=255, blank=True, null=True, choices=LEAD_SOURCE
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
        ]
        constraints = [
            # Case-insensitive unique email per organization (when email is not null)
            models.UniqueConstraint(
                Lower("email"),
                "org",
                name="unique_lead_email_per_org",
                condition=Q(email__isnull=False) & ~Q(email=""),
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


class LeadConversion(BaseModel):
    """Immutable provenance for the records produced by one lead conversion.

    The ledger stores only relational identifiers and creation facts.  It does
    not duplicate names, e-mail addresses, notes or provider payloads.
    """

    lead = models.OneToOneField(
        Lead,
        on_delete=models.PROTECT,
        related_name="conversion",
    )
    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.PROTECT,
        related_name="lead_conversion_records",
    )
    contact = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.PROTECT,
        related_name="lead_conversion_records",
        null=True,
        blank=True,
    )
    opportunity = models.ForeignKey(
        "opportunity.Opportunity",
        on_delete=models.PROTECT,
        related_name="lead_conversion_records",
        null=True,
        blank=True,
    )
    org = models.ForeignKey(
        Org,
        on_delete=models.CASCADE,
        related_name="lead_conversions",
    )
    account_created = models.BooleanField()
    contact_created = models.BooleanField(default=False)
    opportunity_created = models.BooleanField(default=False)
    conversion_method = models.CharField(max_length=32, default="crm_service_v1")

    class Meta:
        db_table = "lead_conversion"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["org", "-created_at"])]

    def clean(self):
        super().clean()
        related = [self.lead, self.account, self.contact, self.opportunity]
        if any(item is not None and item.org_id != self.org_id for item in related):
            raise ValidationError(
                "Conversion relations must belong to one organization."
            )
        if self.contact_created and self.contact_id is None:
            raise ValidationError({"contact_created": "Created contact is required."})
        if self.opportunity_created and self.opportunity_id is None:
            raise ValidationError(
                {"opportunity_created": "Created opportunity is required."}
            )

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Lead conversion provenance is append-only.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Lead conversion provenance is append-only.")


class MarketingCampaign(BaseModel):
    """Organization-owned campaign used for CRM attribution and reporting.

    This intentionally stores no provider credentials or external payloads.
    ``code`` is the stable internal identifier used by Portal adapters.
    """

    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="marketing_campaigns"
    )
    code = models.CharField(max_length=80)
    name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=16, choices=CAMPAIGN_STATUS_CHOICES, default="draft"
    )
    primary_channel = models.CharField(max_length=24, choices=CAMPAIGN_CHANNEL_CHOICES)
    starts_at = models.DateTimeField(blank=True, null=True)
    ends_at = models.DateTimeField(blank=True, null=True)
    is_sample = models.BooleanField(default=False, help_text=SAMPLE_DATA_HELP_TEXT)

    class Meta:
        db_table = "marketing_campaign"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["org", "status", "-created_at"]),
            models.Index(fields=["org", "primary_channel", "-created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                Lower("code"),
                "org",
                name="unique_campaign_code_per_org",
            ),
            models.CheckConstraint(
                condition=(
                    Q(ends_at__isnull=True)
                    | Q(starts_at__isnull=True)
                    | Q(ends_at__gte=models.F("starts_at"))
                ),
                name="campaign_end_not_before_start",
            ),
        ]

    def __str__(self):
        return f"{self.code}: {self.name}"

    def clean(self):
        super().clean()
        self.code = (self.code or "").strip().lower()
        if self.starts_at and self.ends_at and self.ends_at < self.starts_at:
            raise ValidationError({"ends_at": _("Campaign end cannot precede start")})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class LeadAttributionTouch(BaseModel):
    """Sanitized, organization-scoped acquisition touch for one lead.

    Tracking fields are deliberately bounded strings rather than raw request
    URLs, headers or provider responses. Consent evidence remains an opaque
    reference; the sensitive evidence itself belongs in an access-controlled
    evidence store.
    """

    lead = models.ForeignKey(
        Lead, on_delete=models.CASCADE, related_name="attribution_touches"
    )
    campaign = models.ForeignKey(
        MarketingCampaign,
        on_delete=models.SET_NULL,
        related_name="lead_touches",
        blank=True,
        null=True,
    )
    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="lead_attribution_touches"
    )
    touch_type = models.CharField(max_length=8, choices=ATTRIBUTION_TOUCH_CHOICES)
    occurred_at = models.DateTimeField()
    source = models.CharField(max_length=100)
    medium = models.CharField(max_length=100, blank=True)
    campaign_key = models.CharField(max_length=160, blank=True)
    content_key = models.CharField(max_length=160, blank=True)
    term_key = models.CharField(max_length=160, blank=True)
    landing_page_ref = models.CharField(max_length=128, blank=True)
    referrer_domain = models.CharField(max_length=253, blank=True)
    lawful_basis = models.CharField(max_length=32, choices=LAWFUL_BASIS_CHOICES)
    privacy_notice_version = models.CharField(max_length=64)
    consent_evidence_ref = models.CharField(max_length=128, blank=True)
    idempotency_key_digest = models.CharField(max_length=64)
    request_digest = models.CharField(max_length=64)

    class Meta:
        db_table = "lead_attribution_touch"
        ordering = ("occurred_at", "created_at")
        indexes = [
            models.Index(fields=["org", "occurred_at"]),
            models.Index(fields=["lead", "touch_type", "occurred_at"]),
            models.Index(fields=["campaign", "occurred_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["lead"],
                condition=Q(touch_type="first"),
                name="unique_first_touch_per_lead",
            ),
            models.UniqueConstraint(
                fields=["lead"],
                condition=Q(touch_type="last"),
                name="unique_last_touch_per_lead",
            ),
            models.CheckConstraint(
                condition=(~Q(lawful_basis="consent") | ~Q(consent_evidence_ref="")),
                name="consent_requires_evidence_ref",
            ),
            models.UniqueConstraint(
                fields=["org", "idempotency_key_digest"],
                name="unique_attribution_idempotency_per_org",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        for field_name in (
            "source",
            "medium",
            "campaign_key",
            "content_key",
            "term_key",
            "landing_page_ref",
            "referrer_domain",
        ):
            value = getattr(self, field_name, "") or ""
            if any(marker in value for marker in ("@", "://", "?", "#", "\n", "\r")):
                errors[field_name] = _(
                    "Tracking fields cannot contain URLs, query strings, email addresses or control characters"
                )
        if self.lead_id and self.org_id and self.lead.org_id != self.org_id:
            errors["org"] = _("Attribution organization must match the lead")
        if self.campaign_id and self.org_id and self.campaign.org_id != self.org_id:
            errors["campaign"] = _("Campaign organization must match attribution")
        if self.occurred_at and self.occurred_at > timezone.now():
            errors["occurred_at"] = _("Attribution touch cannot be in the future")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class DataSubjectRequest(BaseModel):
    """Intake ledger for LGPD requests; it never executes the requested action."""

    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="data_subject_requests"
    )
    subject_ref_digest = models.CharField(max_length=64)
    request_type = models.CharField(max_length=16, choices=DATA_SUBJECT_REQUEST_CHOICES)
    status = models.CharField(
        max_length=16, choices=DATA_SUBJECT_REQUEST_STATUS_CHOICES, default="submitted"
    )
    version = models.PositiveIntegerField(default=1, editable=False)
    due_at = models.DateTimeField()
    legal_hold = models.BooleanField(default=False, editable=False)
    idempotency_key_digest = models.CharField(max_length=64)
    request_digest = models.CharField(max_length=64)

    class Meta:
        db_table = "data_subject_request"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["org", "status", "due_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["org", "idempotency_key_digest"],
                name="unique_dsr_idempotency_per_org",
            ),
        ]


class DataSubjectRequestEvent(BaseModel):
    """Append-only, sanitized audit fact for a privacy-request transition."""

    request = models.ForeignKey(
        DataSubjectRequest, on_delete=models.PROTECT, related_name="events"
    )
    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="data_subject_request_events"
    )
    sequence = models.PositiveIntegerField()
    event_type = models.CharField(max_length=24, choices=DATA_SUBJECT_EVENT_CHOICES)
    reason_code = models.CharField(max_length=32, blank=True)
    evidence_ref_digest = models.CharField(max_length=64, blank=True)
    actor_ref_digest = models.CharField(max_length=64)

    class Meta:
        db_table = "data_subject_request_event"
        ordering = ("sequence",)
        constraints = [
            models.UniqueConstraint(
                fields=["request", "sequence"], name="unique_dsr_event_sequence"
            )
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Privacy request events are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Privacy request events are append-only.")


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

    def __str__(self):
        return f"{self.pipeline.name} - {self.name}"

    def save(self, *args, **kwargs):
        if not self.org_id and self.pipeline_id:
            self.org_id = self.pipeline.org_id
        super().save(*args, **kwargs)
