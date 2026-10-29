from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from common.base import SAMPLE_DATA_HELP_TEXT, AssignableMixin, BaseModel
from common.models import Org, Profile, Tags, Teams
from common.utils import COUNTRIES, CURRENCY_CODES, INDCHOICES
from common.validators import flexible_phone_validator
from contacts.models import Contact

# Cleanup notes:
# - Removed 'created_on_arrow' property (frontend computes its own timestamps)
# - Removed 'contact_values' property (unused)


class Account(AssignableMixin, BaseModel):
    """
    Account model for CRM - Streamlined for modern sales workflow
    Based on Twenty CRM and Salesforce patterns
    """

    # Core Account Information
    name = models.CharField(_("Account Name"), max_length=255)
    email = models.EmailField(_("Email"), blank=True, null=True)
    phone = models.CharField(
        _("Phone"),
        max_length=25,
        null=True,
        blank=True,
        validators=[flexible_phone_validator],
    )
    website = models.URLField(_("Website"), blank=True, null=True)

    # Business Information
    industry = models.CharField(
        _("Industry"), max_length=255, choices=INDCHOICES, blank=True, null=True
    )
    number_of_employees = models.PositiveIntegerField(
        _("Number of Employees"), blank=True, null=True
    )
    annual_revenue = models.DecimalField(
        _("Annual Revenue"), max_digits=15, decimal_places=2, blank=True, null=True
    )
    currency = models.CharField(
        _("Currency"), max_length=3, choices=CURRENCY_CODES, blank=True, null=True
    )

    # Address (flat fields like Lead and Contact models)
    address_line = models.CharField(_("Address"), max_length=255, blank=True, null=True)
    city = models.CharField(_("City"), max_length=255, blank=True, null=True)
    state = models.CharField(_("State"), max_length=255, blank=True, null=True)
    postcode = models.CharField(_("Postal Code"), max_length=64, blank=True, null=True)
    country = models.CharField(
        _("Country"), max_length=3, choices=COUNTRIES, blank=True, null=True
    )

    # Assignment
    assigned_to = models.ManyToManyField(Profile, related_name="account_assigned_users")
    teams = models.ManyToManyField(Teams, related_name="account_teams")
    contacts = models.ManyToManyField(
        "contacts.Contact", related_name="account_contacts"
    )

    # Tags
    tags = models.ManyToManyField(Tags, related_name="account_tags", blank=True)

    # Notes
    description = models.TextField(_("Notes"), blank=True, null=True)

    custom_fields = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-org schema extension; values are validated against common.CustomFieldDefinition.",
    )

    # System Fields
    is_active = models.BooleanField(default=True)
    is_sample = models.BooleanField(default=False, help_text=SAMPLE_DATA_HELP_TEXT)
    org = models.ForeignKey(
        Org,
        on_delete=models.CASCADE,
        related_name="accounts",
    )

    class Meta:
        verbose_name = "Account"
        verbose_name_plural = "Accounts"
        db_table = "accounts"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["industry"]),
            models.Index(fields=["org", "-created_at"]),
            models.Index(
                fields=["org", "updated_at", "id"], name="accounts_org_upd_idx"
            ),
        ]
        constraints = [
            # Case-insensitive unique account name per organization
            models.UniqueConstraint(
                Lower("name"),
                "org",
                name="unique_account_name_per_org",
            ),
            # Annual revenue must be non-negative
            models.CheckConstraint(
                condition=Q(annual_revenue__gte=0) | Q(annual_revenue__isnull=True),
                name="account_revenue_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.name}"


class AccountEmail(BaseModel):
    from_account = models.ForeignKey(
        Account, related_name="sent_email", on_delete=models.SET_NULL, null=True
    )
    recipients = models.ManyToManyField(Contact, related_name="recieved_email")
    message_subject = models.TextField(null=True)
    message_body = models.TextField(null=True)
    timezone = models.CharField(max_length=100, default="UTC")
    scheduled_date_time = models.DateTimeField(null=True)
    scheduled_later = models.BooleanField(default=False)
    from_email = models.EmailField()
    rendered_message_body = models.TextField(null=True)
    org = models.ForeignKey(
        Org,
        on_delete=models.CASCADE,
        related_name="account_emails",
    )

    class Meta:
        verbose_name = "Account Email"
        verbose_name_plural = "Account Emails"
        db_table = "account_email"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["org", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.message_subject}"

    def save(self, *args, **kwargs):
        """
        Ensure org follows the parent account; fallback to the first recipient's org.

        This guards against missing org assignment, which would violate multi-tenant isolation.
        """
        if not self.org_id:
            if self.from_account and self.from_account.org_id:
                self.org_id = self.from_account.org_id
            else:
                # For unsaved m2m, recipients may not be available until after save
                recipient = self.recipients.first() if self.pk else None
                if recipient and recipient.org_id:
                    self.org_id = recipient.org_id
        if not self.org_id:
            raise ValidationError("Organization is required for AccountEmail")
        super().save(*args, **kwargs)


class AccountEmailLog(BaseModel):
    """this model is used to track if the email is sent or not"""

    email = models.ForeignKey(
        AccountEmail, related_name="email_log", on_delete=models.SET_NULL, null=True
    )
    contact = models.ForeignKey(
        Contact, related_name="contact_email_log", on_delete=models.SET_NULL, null=True
    )
    is_sent = models.BooleanField(default=False)
    org = models.ForeignKey(
        Org,
        on_delete=models.CASCADE,
        related_name="account_email_logs",
    )

    class Meta:
        verbose_name = "EmailLog"
        verbose_name_plural = "EmailLogs"
        db_table = "emailLogs"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["org", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.email.message_subject}"

    def save(self, *args, **kwargs):
        """
        Align log org with parent email (preferred) or contact org.
        """
        if not self.org_id:
            if self.email and self.email.org_id:
                self.org_id = self.email.org_id
            elif self.contact and self.contact.org_id:
                self.org_id = self.contact.org_id
        if not self.org_id:
            raise ValidationError("Organization is required for AccountEmailLog")
        super().save(*args, **kwargs)
