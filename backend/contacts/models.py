from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from common.base import SAMPLE_DATA_HELP_TEXT, AssignableMixin, BaseModel
from common.models import Org, Profile, Tags, Teams
from common.phones import set_phone_e164
from common.utils import COUNTRIES
from common.validators import flexible_phone_validator


class Contact(AssignableMixin, BaseModel):
    """
    Contact model for CRM - Streamlined for modern sales workflow
    Based on Twenty CRM and Salesforce patterns
    """

    # Core Contact Information
    first_name = models.CharField(_("First name"), max_length=255)
    last_name = models.CharField(_("Last name"), max_length=255)
    email = models.EmailField(_("Email"), blank=True, null=True)
    phone = models.CharField(
        _("Phone"),
        max_length=25,
        null=True,
        blank=True,
        validators=[flexible_phone_validator],
    )
    phone_e164 = models.CharField(max_length=32, blank=True, default="", db_index=True)

    # Professional Information
    organization = models.CharField(_("Company"), max_length=255, blank=True, null=True)
    title = models.CharField(_("Job Title"), max_length=255, blank=True, null=True)
    department = models.CharField(
        _("Department"), max_length=255, blank=True, null=True
    )

    # Communication Preferences
    do_not_call = models.BooleanField(_("Do Not Call"), default=False)
    linkedin_url = models.URLField(_("LinkedIn URL"), blank=True, null=True)

    # Address (flat fields like Lead model)
    address_line = models.CharField(_("Address"), max_length=255, blank=True, null=True)
    city = models.CharField(_("City"), max_length=255, blank=True, null=True)
    state = models.CharField(_("State"), max_length=255, blank=True, null=True)
    postcode = models.CharField(_("Postal Code"), max_length=64, blank=True, null=True)
    country = models.CharField(
        _("Country"), max_length=3, choices=COUNTRIES, blank=True, null=True
    )

    # Assignment
    assigned_to = models.ManyToManyField(Profile, related_name="contact_assigned_users")
    teams = models.ManyToManyField(Teams, related_name="contact_teams")

    # Tags
    tags = models.ManyToManyField(Tags, related_name="contact_tags", blank=True)

    # Notes
    description = models.TextField(_("Notes"), blank=True, null=True)

    # Where the person came from, copied from the lead at conversion. `source`
    # is a key from the org's LeadSource list.
    source = models.CharField(_("Source"), max_length=255, blank=True, default="")
    source_detail = models.CharField(
        _("Sub-source"), max_length=100, blank=True, default=""
    )
    source_ref = models.CharField(
        _("Reference"), max_length=255, blank=True, default=""
    )

    # System Fields
    is_active = models.BooleanField(default=True)
    is_sample = models.BooleanField(default=False, help_text=SAMPLE_DATA_HELP_TEXT)
    auto_created = models.BooleanField(
        default=False,
        help_text="True when the contact was created by an inbound channel "
        "(e.g. email-to-ticket) rather than a human; flagged for admin review.",
    )
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="contacts")

    # Account relationship (optional - contact can exist without an account)
    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="primary_contacts",
        help_text="Primary account this contact belongs to",
    )

    custom_fields = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-org schema extension; values are validated against common.CustomFieldDefinition.",
    )

    class Meta:
        verbose_name = "Contact"
        verbose_name_plural = "Contacts"
        db_table = "contacts"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["org", "-created_at"]),
            models.Index(
                fields=["org", "updated_at", "id"], name="contacts_org_upd_idx"
            ),
        ]
        constraints = [
            # Case-insensitive unique email per organization (when email is not null)
            models.UniqueConstraint(
                Lower("email"),
                "org",
                name="unique_contact_email_per_org",
                condition=Q(email__isnull=False) & ~Q(email=""),
            ),
        ]

    def __str__(self):
        return self.first_name

    def save(self, *args, **kwargs):
        set_phone_e164(self, kwargs)
        super().save(*args, **kwargs)
