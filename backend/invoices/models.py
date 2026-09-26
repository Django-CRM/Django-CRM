import datetime
import secrets
from decimal import ROUND_HALF_UP, Decimal

from django.db import connection, models, transaction
from django.db.models import IntegerField, Max
from django.db.models.functions import Cast, Substr
from django.utils import timezone
from django.utils.timesince import timesince
from django.utils.translation import gettext_lazy as _

from accounts.models import Account
from common.base import AssignableMixin, BaseModel
from common.models import Org, Profile, Teams
from common.utils import COUNTRIES, CURRENCY_CODES
from contacts.models import Contact
from opportunity.models import Opportunity

# =============================================================================
# CONSTANTS
# =============================================================================

INVOICE_STATUS = (
    ("Draft", "Draft"),
    ("Sent", "Sent"),
    ("Viewed", "Viewed"),
    ("Paid", "Paid"),
    ("Partially_Paid", "Partially Paid"),
    ("Overdue", "Overdue"),
    ("Pending", "Pending"),
    ("Cancelled", "Cancelled"),
)

# Statuses where the money is still owed: billed to the customer, not yet
# settled and not written off. Draft is excluded because nobody has been asked
# to pay yet, Pending because it is not billed either.
#
# Shared so that "what this account owes us" on an account page and the
# accounts-receivable aging report cannot answer the same question differently.
# Note this deliberately includes "Overdue", `mark_overdue_invoices` flips the
# status on a daily schedule, so anything that only looked for "Overdue" would
# report nothing at all on a day the task has not run yet.
UNPAID_STATUSES = ("Sent", "Viewed", "Partially_Paid", "Overdue")

PAYMENT_TERMS = (
    ("DUE_ON_RECEIPT", "Due on Receipt"),
    ("NET_15", "Net 15"),
    ("NET_30", "Net 30"),
    ("NET_45", "Net 45"),
    ("NET_60", "Net 60"),
    ("CUSTOM", "Custom"),
)

PAYMENT_METHODS = (
    ("CASH", "Cash"),
    ("CHECK", "Check"),
    ("CREDIT_CARD", "Credit Card"),
    ("BANK_TRANSFER", "Bank Transfer"),
    ("PAYPAL", "PayPal"),
    ("STRIPE", "Stripe"),
    ("OTHER", "Other"),
)

RECURRING_FREQUENCIES = (
    ("WEEKLY", "Weekly"),
    ("BIWEEKLY", "Bi-weekly"),
    ("MONTHLY", "Monthly"),
    ("QUARTERLY", "Quarterly"),
    ("SEMI_ANNUALLY", "Semi-annually"),
    ("YEARLY", "Yearly"),
    ("CUSTOM", "Custom"),
)

DISCOUNT_TYPES = (
    ("PERCENTAGE", "Percentage (%)"),
    ("FIXED", "Fixed Amount"),
)

CENT = Decimal("0.01")


def line_gross(quantity, unit_price):
    """Quantity x unit price, to the cent."""
    return (Decimal(quantity) * Decimal(unit_price)).quantize(CENT, ROUND_HALF_UP)


def line_discount(gross, discount_type, discount_value):
    """The discount a line takes off its ``gross`` (quantity x unit price).

    PERCENTAGE takes that share of the gross. Any other type, blank included,
    takes ``discount_value`` as a flat amount: that is how these lines and
    ``OpportunityLineItem`` have always read it, and keeping the two the same
    is what makes an invoice raised from a deal total what the deal does.
    Rounded to the cent the way the database stores it.
    """
    value = Decimal(discount_value or 0)
    amount = gross * value / Decimal("100") if discount_type == "PERCENTAGE" else value
    return amount.quantize(CENT, ROUND_HALF_UP)


class LineAmounts:
    """What a line adds to its document, shared by the three line models.

    ``net_amount`` is the one definition: quantity x unit price, less the
    line's own discount. A document's subtotal is the sum of its lines'
    net amounts; the document's discount, tax and shipping then apply to
    that sum. A line's own ``tax_rate`` is shown beside the line and is not
    added to the document: the document's ``tax_rate`` is what is charged.
    """

    @property
    def net_amount(self):
        gross = line_gross(self.quantity, self.unit_price)
        return gross - line_discount(gross, self.discount_type, self.discount_value)


def totals_follow_lines(document, update_fields):
    """Whether ``save()`` recomputes a document's totals from its lines.

    Only on a full save of a document that is new or is still a Draft in the
    database. An issued document keeps the totals it was issued with: the
    portal's first view, a send, the overdue and expiry sweeps and a reminder
    all full-save one, and none of them may move what the customer was
    billed. An explicit edit through the API (the document serializers and
    the line-item views) calls ``recalculate_totals()`` itself. A save
    limited to ``update_fields`` never writes the totals, so recomputing them
    there only let a payment write an ``amount_due`` from a new total beside
    the old stored ``total_amount``.
    """
    if update_fields is not None:
        return False
    if document._state.adding:
        return True
    stored = (
        type(document)
        .objects.filter(pk=document.pk)
        .values_list("status", flat=True)
        .first()
    )
    return stored == "Draft"


ESTIMATE_STATUS = (
    ("Draft", "Draft"),
    ("Sent", "Sent"),
    ("Viewed", "Viewed"),
    ("Accepted", "Accepted"),
    ("Declined", "Declined"),
    ("Expired", "Expired"),
)

REMINDER_FREQUENCIES = (
    ("ONCE", "Once"),
    ("WEEKLY", "Weekly until paid"),
    ("CUSTOM", "Custom schedule"),
)


# =============================================================================
# INVOICE TEMPLATE
# =============================================================================


class InvoiceTemplate(BaseModel):
    """Custom invoice templates for PDF generation"""

    name = models.CharField(_("Template Name"), max_length=100)
    logo = models.ImageField(
        _("Logo"), upload_to="invoice_templates/logos/", blank=True, null=True
    )
    primary_color = models.CharField(
        _("Primary Color"), max_length=7, default="#3B82F6"
    )
    secondary_color = models.CharField(
        _("Secondary Color"), max_length=7, default="#1E40AF"
    )
    template_html = models.TextField(_("Custom HTML"), blank=True)
    template_css = models.TextField(_("Custom CSS"), blank=True)
    default_notes = models.TextField(_("Default Notes"), blank=True)
    default_terms = models.TextField(_("Default Terms"), blank=True)
    footer_text = models.TextField(_("Footer Text"), blank=True)
    is_default = models.BooleanField(_("Is Default"), default=False)
    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="invoice_templates"
    )

    class Meta:
        verbose_name = "Invoice Template"
        verbose_name_plural = "Invoice Templates"
        db_table = "invoice_template"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["org", "-created_at"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        from django.db import transaction

        # Ensure only one default template per org (with transaction for race condition safety)
        if self.is_default:
            with transaction.atomic():
                InvoiceTemplate.objects.filter(org=self.org, is_default=True).exclude(
                    pk=self.pk
                ).select_for_update().update(is_default=False)
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)


# =============================================================================
# PRODUCT CATALOG
# =============================================================================


class Product(BaseModel):
    """Product Catalog for Line Items"""

    name = models.CharField(_("Product Name"), max_length=255)
    description = models.TextField(_("Description"), blank=True, null=True)
    sku = models.CharField(_("SKU"), max_length=100, blank=True, null=True)
    price = models.DecimalField(_("Price"), max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(
        _("Currency"), max_length=3, choices=CURRENCY_CODES, blank=True, null=True
    )
    category = models.CharField(_("Category"), max_length=100, blank=True, null=True)
    is_active = models.BooleanField(_("Is Active"), default=True)
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="products")

    class Meta:
        verbose_name = "Product"
        verbose_name_plural = "Products"
        db_table = "product"
        ordering = ("name",)
        unique_together = [["sku", "org"]]
        indexes = [
            models.Index(fields=["org", "is_active"]),
        ]

    def __str__(self):
        return self.name


def _next_number(model, field, prefix, org_id):
    """Return the next ``<prefix>NNNN`` in one org's sequence for ``field``.

    Numbers are a per-org sequence, unique on ``(org, field)``. Call this in
    the transaction that inserts the row: the advisory lock serializes
    allocation per model and org until that transaction ends, so a concurrent
    create in the same org waits, then counts this row. A row lock cannot do
    that, because the day's first number has no row to lock. SQLite, the
    test-only backend, serializes writers itself and has no advisory locks.
    """
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                [f"{model._meta.db_table}:{org_id}"],
            )
    result = (
        model.objects.filter(org_id=org_id, **{f"{field}__startswith": prefix})
        .annotate(seq_num=Cast(Substr(field, len(prefix) + 1), IntegerField()))
        .aggregate(max_seq=Max("seq_num"))
    )
    return f"{prefix}{(result['max_seq'] or 0) + 1:04d}"


# =============================================================================
# INVOICE
# =============================================================================


class Invoice(AssignableMixin, BaseModel):
    """
    Invoice model with full CRM integration.

    CRM Integration Rules:
    - account: MANDATORY - The legal entity responsible for payment
    - contact: MANDATORY (via serializer) - The "Bill To" contact
    - opportunity: Optional but recommended for ROI/commission tracking
    """

    # Core Invoice Info
    invoice_title = models.CharField(_("Invoice Title"), max_length=100)
    invoice_number = models.CharField(_("Invoice Number"), max_length=50)
    status = models.CharField(
        _("Status"), choices=INVOICE_STATUS, max_length=20, default="Draft"
    )

    # CRM Integration (MANDATORY Account and Contact - enforced via serializer)
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invoices",
        help_text="The legal entity responsible for payment (REQUIRED via validation)",
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
        help_text="The 'Bill To' contact (REQUIRED via validation)",
    )
    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
        help_text="The sales opportunity this invoice relates to (optional)",
    )

    # Client Details (denormalized for PDF generation and history)
    client_name = models.CharField(_("Client Name"), max_length=255, default="")
    client_email = models.EmailField(_("Client Email"), default="")
    client_phone = models.CharField(_("Client Phone"), max_length=25, blank=True)

    # Billing Address (flat fields - company "From" address)
    billing_address_line = models.CharField(
        _("Billing Address"), max_length=255, blank=True
    )
    billing_city = models.CharField(_("Billing City"), max_length=100, blank=True)
    billing_state = models.CharField(_("Billing State"), max_length=100, blank=True)
    billing_postcode = models.CharField(
        _("Billing Postal Code"), max_length=20, blank=True
    )
    billing_country = models.CharField(
        _("Billing Country"), max_length=3, choices=COUNTRIES, blank=True
    )

    # Client Address (flat fields - "Bill To" address)
    client_address_line = models.CharField(
        _("Client Address"), max_length=255, blank=True
    )
    client_city = models.CharField(_("Client City"), max_length=100, blank=True)
    client_state = models.CharField(_("Client State"), max_length=100, blank=True)
    client_postcode = models.CharField(
        _("Client Postal Code"), max_length=20, blank=True
    )
    client_country = models.CharField(
        _("Client Country"), max_length=3, choices=COUNTRIES, blank=True
    )

    # Financial - Line Items Summary
    subtotal = models.DecimalField(
        _("Subtotal"), max_digits=12, decimal_places=2, default=0
    )
    discount_type = models.CharField(
        _("Discount Type"), max_length=20, choices=DISCOUNT_TYPES, blank=True
    )
    discount_value = models.DecimalField(
        _("Discount Value"), max_digits=12, decimal_places=2, default=0
    )
    discount_amount = models.DecimalField(
        _("Discount Amount"), max_digits=12, decimal_places=2, default=0
    )
    tax_rate = models.DecimalField(
        _("Tax Rate (%)"), max_digits=5, decimal_places=2, default=0
    )
    tax_amount = models.DecimalField(
        _("Tax Amount"), max_digits=12, decimal_places=2, default=0
    )
    shipping_amount = models.DecimalField(
        _("Shipping"), max_digits=12, decimal_places=2, default=0
    )
    total_amount = models.DecimalField(
        _("Total Amount"), max_digits=12, decimal_places=2, default=0
    )
    currency = models.CharField(
        _("Currency"), max_length=3, choices=CURRENCY_CODES, default="USD"
    )

    # Payment Tracking
    amount_paid = models.DecimalField(
        _("Amount Paid"), max_digits=12, decimal_places=2, default=0
    )
    amount_due = models.DecimalField(
        _("Amount Due"), max_digits=12, decimal_places=2, default=0
    )

    # Dates & Terms
    issue_date = models.DateField(_("Issue Date"), default=datetime.date.today)
    due_date = models.DateField(_("Due Date"), blank=True, null=True)
    payment_terms = models.CharField(
        _("Payment Terms"), max_length=20, choices=PAYMENT_TERMS, default="NET_30"
    )

    # Status Timestamps
    sent_at = models.DateTimeField(_("Sent At"), null=True, blank=True)
    viewed_at = models.DateTimeField(_("Viewed At"), null=True, blank=True)
    paid_at = models.DateTimeField(_("Paid At"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("Cancelled At"), null=True, blank=True)

    # Payment Reminders
    reminder_enabled = models.BooleanField(_("Reminders Enabled"), default=True)
    reminder_days_before = models.PositiveIntegerField(
        _("Remind Days Before Due"), default=3
    )
    reminder_days_after = models.PositiveIntegerField(
        _("Remind Days After Due"), default=7
    )
    reminder_frequency = models.CharField(
        _("Reminder Frequency"),
        max_length=20,
        choices=REMINDER_FREQUENCIES,
        default="ONCE",
    )
    last_reminder_sent = models.DateTimeField(
        _("Last Reminder Sent"), null=True, blank=True
    )
    reminder_count = models.PositiveIntegerField(_("Reminders Sent"), default=0)

    # Client Portal
    public_token = models.CharField(
        _("Public Token"), max_length=64, unique=True, blank=True
    )
    public_link_enabled = models.BooleanField(_("Public Link Enabled"), default=True)

    # Template
    template = models.ForeignKey(
        InvoiceTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )

    # Notes & Terms
    notes = models.TextField(_("Notes"), blank=True)
    terms = models.TextField(_("Terms & Conditions"), blank=True)
    details = models.TextField(_("Internal Details"), blank=True, null=True)

    # Additional Metadata
    billing_period = models.CharField(
        _("Billing Period"), max_length=100, blank=True, null=True
    )
    po_number = models.CharField(_("PO Number"), max_length=100, blank=True, null=True)

    custom_fields = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-org schema extension; values are validated against common.CustomFieldDefinition.",
    )

    # Assignment
    assigned_to = models.ManyToManyField(Profile, related_name="invoice_assigned_to")
    teams = models.ManyToManyField(Teams, related_name="invoices_teams")

    is_email_sent = models.BooleanField(default=False)

    # Organization
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="invoices")

    class Meta:
        verbose_name = "Invoice"
        verbose_name_plural = "Invoices"
        db_table = "invoice"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["org", "-created_at"]),
            models.Index(fields=["org", "status"]),
            models.Index(fields=["account"]),
            models.Index(fields=["due_date"]),
            models.Index(fields=["public_token"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["org", "invoice_number"],
                name="unique_invoice_number_per_org",
            ),
        ]

    def __str__(self):
        return f"{self.invoice_number}"

    def save(self, *args, **kwargs):
        # One transaction from number allocation to insert, so the lock
        # _next_number takes is still held when this row lands.
        with transaction.atomic():
            # Generate invoice number if not set
            if not self.invoice_number:
                self.invoice_number = self.generate_invoice_number()

            # Generate public token if not set (with collision check)
            if not self.public_token:
                token = secrets.token_urlsafe(32)
                while Invoice.objects.filter(public_token=token).exists():
                    token = secrets.token_urlsafe(32)
                self.public_token = token

            # Calculate due date from payment terms if not set
            if self.issue_date and not self.due_date:
                self.due_date = self.calculate_due_date()

            if totals_follow_lines(self, kwargs.get("update_fields")):
                self.recalculate_totals()
            else:
                self.amount_due = self.total_amount - self.amount_paid

            super().save(*args, **kwargs)

            # Keep the unscoped token→org lookup in step with the token we just
            # minted, so the anonymous portal view can resolve the org under RLS.
            from common.portal_tokens import register_portal_token

            register_portal_token(self.public_token, self.org_id, "invoice", self.id)

    def generate_invoice_number(self):
        """Next number in this org's sequence: INV-YYYYMMDD-XXXX"""
        prefix = f"INV-{datetime.datetime.now():%Y%m%d}-"
        return _next_number(Invoice, "invoice_number", prefix, self.org_id)

    def calculate_due_date(self):
        """Calculate due date based on payment terms"""
        from datetime import timedelta

        if not self.issue_date:
            return None

        term_days = {
            "DUE_ON_RECEIPT": 0,
            "NET_15": 15,
            "NET_30": 30,
            "NET_45": 45,
            "NET_60": 60,
        }

        days = term_days.get(self.payment_terms, 30)
        return self.issue_date + timedelta(days=days)

    def recalculate_totals(self):
        """Recalculate invoice totals from line items.

        The subtotal is the sum of each line's ``net_amount`` (after the line's
        own discount); see ``LineAmounts``.
        """
        line_items = self.line_items.all() if self.pk else []
        self.subtotal = sum((item.net_amount for item in line_items), Decimal("0"))

        # Calculate discount, never more than the subtotal: the serializers
        # refuse one that is, but removing a line later can still shrink the
        # subtotal below a flat discount.
        if self.discount_type == "PERCENTAGE":
            discount = self.subtotal * (self.discount_value / Decimal("100"))
        else:
            discount = self.discount_value
        self.discount_amount = min(discount, self.subtotal)

        # Calculate tax on discounted amount
        taxable = self.subtotal - self.discount_amount
        self.tax_amount = taxable * (self.tax_rate / Decimal("100"))

        # Calculate total
        self.total_amount = taxable + self.tax_amount + self.shipping_amount

        # Calculate amount due
        self.amount_due = self.total_amount - self.amount_paid

    def formatted_total_amount(self):
        currency = self.currency or "USD"
        return f"{currency} {self.total_amount}"

    def formatted_amount_due(self):
        currency = self.currency or "USD"
        return f"{currency} {self.amount_due}"

    @property
    def is_overdue(self):
        if self.due_date and self.status not in ["Paid", "Cancelled"]:
            return timezone.localdate() > self.due_date
        return False

    @property
    def public_url(self):
        """Generate the public viewing URL"""
        return f"/portal/invoice/{self.public_token}"

    @property
    def created_on_arrow(self):
        return timesince(self.created_at) + " ago"


# =============================================================================
# INVOICE LINE ITEM
# =============================================================================


class InvoiceLineItem(LineAmounts, BaseModel):
    """Line Item for Invoices with per-item discount and tax support"""

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="line_items"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoice_line_items",
    )
    name = models.CharField(_("Item Name"), max_length=255, blank=True, null=True)
    description = models.CharField(_("Description"), max_length=500, blank=True)
    quantity = models.DecimalField(
        _("Quantity"), max_digits=10, decimal_places=2, default=1
    )
    unit_price = models.DecimalField(
        _("Unit Price"), max_digits=12, decimal_places=2, default=0
    )

    # Per-item discount
    discount_type = models.CharField(
        _("Discount Type"), max_length=20, choices=DISCOUNT_TYPES, blank=True
    )
    discount_value = models.DecimalField(
        _("Discount Value"), max_digits=12, decimal_places=2, default=0
    )
    discount_amount = models.DecimalField(
        _("Discount Amount"), max_digits=12, decimal_places=2, default=0
    )

    # Per-item tax
    tax_rate = models.DecimalField(
        _("Tax Rate (%)"), max_digits=5, decimal_places=2, default=0
    )
    tax_amount = models.DecimalField(
        _("Tax Amount"), max_digits=12, decimal_places=2, default=0
    )

    # Computed amounts
    subtotal = models.DecimalField(
        _("Subtotal"), max_digits=12, decimal_places=2, default=0
    )
    total = models.DecimalField(_("Total"), max_digits=12, decimal_places=2, default=0)

    # Ordering
    order = models.PositiveIntegerField(_("Order"), default=0)

    # Organization
    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="invoice_line_items"
    )

    class Meta:
        verbose_name = "Invoice Line Item"
        verbose_name_plural = "Invoice Line Items"
        db_table = "invoice_line_item"
        ordering = ("order",)
        indexes = [
            models.Index(fields=["org", "order"]),
        ]

    def __str__(self):
        display_name = self.name or self.description or "Item"
        return f"{self.invoice.invoice_number} - {display_name}"

    def save(self, *args, **kwargs):
        # subtotal is the gross (quantity x unit price); what the line adds to
        # the invoice is net_amount, the gross less discount_amount.
        self.subtotal = line_gross(self.quantity, self.unit_price)
        self.discount_amount = line_discount(
            self.subtotal, self.discount_type, self.discount_value
        )
        taxable = self.net_amount
        self.tax_amount = taxable * (self.tax_rate / Decimal("100"))
        self.total = taxable + self.tax_amount

        # Inherit org from invoice if not set
        if not self.org_id and self.invoice_id:
            self.org_id = self.invoice.org_id

        super().save(*args, **kwargs)

    @property
    def formatted_unit_price(self):
        if self.invoice and self.invoice.currency:
            return f"{self.invoice.currency} {self.unit_price}"
        return str(self.unit_price)

    @property
    def formatted_total(self):
        if self.invoice and self.invoice.currency:
            return f"{self.invoice.currency} {self.total}"
        return str(self.total)


# =============================================================================
# PAYMENT
# =============================================================================


class Payment(BaseModel):
    """Track payments against invoices"""

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="payments"
    )
    amount = models.DecimalField(_("Amount"), max_digits=12, decimal_places=2)
    payment_date = models.DateField(_("Payment Date"))
    payment_method = models.CharField(
        _("Payment Method"), max_length=20, choices=PAYMENT_METHODS
    )
    reference_number = models.CharField(
        _("Reference Number"), max_length=100, blank=True
    )
    notes = models.TextField(_("Notes"), blank=True)
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="payments")

    class Meta:
        verbose_name = "Payment"
        verbose_name_plural = "Payments"
        db_table = "payment"
        ordering = ("-payment_date",)
        indexes = [
            models.Index(fields=["org", "-payment_date"]),
            models.Index(fields=["invoice"]),
        ]

    def __str__(self):
        return f"{self.invoice.invoice_number} - {self.amount}"

    def save(self, *args, **kwargs):
        # Inherit org from invoice if not set
        if not self.org_id and self.invoice_id:
            self.org_id = self.invoice.org_id

        super().save(*args, **kwargs)

        # Update invoice payment totals
        self.update_invoice_payment()

    def delete(self, *args, **kwargs):
        invoice = self.invoice
        super().delete(*args, **kwargs)
        # Recalculate after deletion
        self.update_invoice_payment(invoice)

    def update_invoice_payment(self, invoice=None):
        """Update the invoice's amount_paid and status"""
        invoice = invoice or self.invoice
        total_paid = (
            invoice.payments.aggregate(total=models.Sum("amount"))["total"] or 0
        )
        invoice.amount_paid = total_paid
        invoice.amount_due = invoice.total_amount - total_paid

        # A cancelled invoice's status is not payment-derived. Without this the
        # block below would move it to Paid or Partially_Paid and quietly undo
        # the cancellation. `PaymentCreateSerializer.validate` stops the API
        # from getting here, but the totals still have to be right for any
        # other caller (a deleted payment, a data fix, a management command).
        if invoice.status == "Cancelled":
            invoice.save(update_fields=["amount_paid", "amount_due"])
            return

        # Update status based on payment
        if invoice.amount_due <= 0:
            invoice.status = "Paid"
            if not invoice.paid_at:
                from django.utils import timezone

                invoice.paid_at = timezone.now()
        elif total_paid > 0:
            invoice.status = "Partially_Paid"

        invoice.save(update_fields=["amount_paid", "amount_due", "status", "paid_at"])


# =============================================================================
# ESTIMATE
# =============================================================================


class Estimate(AssignableMixin, BaseModel):
    """Estimates/Quotes - can be converted to Invoice"""

    # Core Info
    estimate_number = models.CharField(_("Estimate Number"), max_length=50)
    title = models.CharField(_("Title"), max_length=100)
    status = models.CharField(
        _("Status"), max_length=20, choices=ESTIMATE_STATUS, default="Draft"
    )

    # CRM Integration (same rules as Invoice - enforced via serializer)
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="estimates",
        help_text="The legal entity (REQUIRED via validation)",
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="estimates",
        help_text="The contact person (REQUIRED via validation)",
    )
    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="estimates",
    )

    # Client Details
    client_name = models.CharField(_("Client Name"), max_length=255, default="")
    client_email = models.EmailField(_("Client Email"), default="")
    client_phone = models.CharField(_("Client Phone"), max_length=25, blank=True)

    # Client Address
    client_address_line = models.CharField(
        _("Client Address"), max_length=255, blank=True
    )
    client_city = models.CharField(_("Client City"), max_length=100, blank=True)
    client_state = models.CharField(_("Client State"), max_length=100, blank=True)
    client_postcode = models.CharField(
        _("Client Postal Code"), max_length=20, blank=True
    )
    client_country = models.CharField(
        _("Client Country"), max_length=3, choices=COUNTRIES, blank=True
    )

    # Financial
    subtotal = models.DecimalField(
        _("Subtotal"), max_digits=12, decimal_places=2, default=0
    )
    discount_type = models.CharField(
        _("Discount Type"), max_length=20, choices=DISCOUNT_TYPES, blank=True
    )
    discount_value = models.DecimalField(
        _("Discount Value"), max_digits=12, decimal_places=2, default=0
    )
    discount_amount = models.DecimalField(
        _("Discount Amount"), max_digits=12, decimal_places=2, default=0
    )
    tax_rate = models.DecimalField(
        _("Tax Rate (%)"), max_digits=5, decimal_places=2, default=0
    )
    tax_amount = models.DecimalField(
        _("Tax Amount"), max_digits=12, decimal_places=2, default=0
    )
    total_amount = models.DecimalField(
        _("Total Amount"), max_digits=12, decimal_places=2, default=0
    )
    currency = models.CharField(
        _("Currency"), max_length=3, choices=CURRENCY_CODES, default="USD"
    )

    # Dates
    issue_date = models.DateField(_("Issue Date"), default=datetime.date.today)
    expiry_date = models.DateField(_("Expiry Date"), null=True, blank=True)

    # Status Timestamps
    sent_at = models.DateTimeField(_("Sent At"), null=True, blank=True)
    viewed_at = models.DateTimeField(_("Viewed At"), null=True, blank=True)
    accepted_at = models.DateTimeField(_("Accepted At"), null=True, blank=True)
    declined_at = models.DateTimeField(_("Declined At"), null=True, blank=True)

    # Acceptance audit trail. Accepting authorises the quote's price, so we
    # record who did it. The anonymous accept endpoint captures the name and
    # email they entered plus the request IP/user-agent as best-effort evidence.
    accepted_by_name = models.CharField(
        _("Accepted By Name"), max_length=255, blank=True, default=""
    )
    accepted_by_email = models.EmailField(
        _("Accepted By Email"), blank=True, default=""
    )
    accepted_ip = models.GenericIPAddressField(
        _("Accepted From IP"), null=True, blank=True
    )
    accepted_user_agent = models.TextField(
        _("Accepted User Agent"), blank=True, default=""
    )

    # Conversion
    converted_to_invoice = models.ForeignKey(
        Invoice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_estimate",
    )

    # Client Portal
    public_token = models.CharField(
        _("Public Token"), max_length=64, unique=True, blank=True
    )
    public_link_enabled = models.BooleanField(_("Public Link Enabled"), default=True)

    # Notes
    notes = models.TextField(_("Notes"), blank=True)
    terms = models.TextField(_("Terms & Conditions"), blank=True)

    custom_fields = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-org schema extension; values are validated against common.CustomFieldDefinition.",
    )

    # Assignment
    assigned_to = models.ManyToManyField(Profile, related_name="estimate_assigned_to")
    teams = models.ManyToManyField(Teams, related_name="estimate_teams")

    # Organization
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="estimates")

    class Meta:
        verbose_name = "Estimate"
        verbose_name_plural = "Estimates"
        db_table = "estimate"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["org", "-created_at"]),
            models.Index(fields=["org", "status"]),
            models.Index(fields=["account"]),
            models.Index(fields=["expiry_date"]),
            models.Index(fields=["public_token"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["org", "estimate_number"],
                name="unique_estimate_number_per_org",
            ),
        ]

    def __str__(self):
        return f"{self.estimate_number}"

    def save(self, *args, **kwargs):
        # One transaction from number allocation to insert; see Invoice.save.
        with transaction.atomic():
            if not self.estimate_number:
                self.estimate_number = self.generate_estimate_number()

            # Generate public token if not set (with collision check)
            if not self.public_token:
                token = secrets.token_urlsafe(32)
                while Estimate.objects.filter(public_token=token).exists():
                    token = secrets.token_urlsafe(32)
                self.public_token = token

            if totals_follow_lines(self, kwargs.get("update_fields")):
                self.recalculate_totals()
            super().save(*args, **kwargs)

            # Keep the unscoped token→org lookup in step with the token we just
            # minted, so the anonymous portal view can resolve the org under RLS.
            from common.portal_tokens import register_portal_token

            register_portal_token(self.public_token, self.org_id, "estimate", self.id)

    def generate_estimate_number(self):
        """Next number in this org's sequence: EST-YYYYMMDD-XXXX"""
        prefix = f"EST-{datetime.datetime.now():%Y%m%d}-"
        return _next_number(Estimate, "estimate_number", prefix, self.org_id)

    def recalculate_totals(self):
        """Recalculate estimate totals from line items; the subtotal is the
        sum of each line's ``net_amount``, as on an invoice."""
        line_items = self.line_items.all() if self.pk else []
        self.subtotal = sum((item.net_amount for item in line_items), Decimal("0"))

        # Capped at the subtotal, as on an invoice.
        if self.discount_type == "PERCENTAGE":
            discount = self.subtotal * (self.discount_value / Decimal("100"))
        else:
            discount = self.discount_value
        self.discount_amount = min(discount, self.subtotal)

        taxable = self.subtotal - self.discount_amount
        self.tax_amount = taxable * (self.tax_rate / Decimal("100"))
        self.total_amount = taxable + self.tax_amount

    @property
    def is_expired(self):
        if self.expiry_date and self.status not in ["Accepted", "Declined"]:
            return timezone.localdate() > self.expiry_date
        return False

    @property
    def public_url(self):
        return f"/portal/estimate/{self.public_token}"


class EstimateLineItem(LineAmounts, BaseModel):
    """Line item for Estimates - mirrors InvoiceLineItem structure"""

    estimate = models.ForeignKey(
        Estimate, on_delete=models.CASCADE, related_name="line_items"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="estimate_line_items",
    )
    name = models.CharField(_("Item Name"), max_length=255, blank=True, null=True)
    description = models.CharField(_("Description"), max_length=500, blank=True)
    quantity = models.DecimalField(
        _("Quantity"), max_digits=10, decimal_places=2, default=1
    )
    unit_price = models.DecimalField(
        _("Unit Price"), max_digits=12, decimal_places=2, default=0
    )

    discount_type = models.CharField(
        _("Discount Type"), max_length=20, choices=DISCOUNT_TYPES, blank=True
    )
    discount_value = models.DecimalField(
        _("Discount Value"), max_digits=12, decimal_places=2, default=0
    )
    discount_amount = models.DecimalField(
        _("Discount Amount"), max_digits=12, decimal_places=2, default=0
    )

    tax_rate = models.DecimalField(
        _("Tax Rate (%)"), max_digits=5, decimal_places=2, default=0
    )
    tax_amount = models.DecimalField(
        _("Tax Amount"), max_digits=12, decimal_places=2, default=0
    )

    subtotal = models.DecimalField(
        _("Subtotal"), max_digits=12, decimal_places=2, default=0
    )
    total = models.DecimalField(_("Total"), max_digits=12, decimal_places=2, default=0)

    order = models.PositiveIntegerField(_("Order"), default=0)
    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="estimate_line_items"
    )

    class Meta:
        verbose_name = "Estimate Line Item"
        verbose_name_plural = "Estimate Line Items"
        db_table = "estimate_line_item"
        ordering = ("order",)
        indexes = [
            models.Index(fields=["org", "order"]),
        ]

    def __str__(self):
        display_name = self.name or self.description or "Item"
        return f"{self.estimate.estimate_number} - {display_name}"

    def save(self, *args, **kwargs):
        # As InvoiceLineItem.save: subtotal is the gross, net_amount what counts.
        self.subtotal = line_gross(self.quantity, self.unit_price)
        self.discount_amount = line_discount(
            self.subtotal, self.discount_type, self.discount_value
        )
        taxable = self.net_amount
        self.tax_amount = taxable * (self.tax_rate / Decimal("100"))
        self.total = taxable + self.tax_amount

        if not self.org_id and self.estimate_id:
            self.org_id = self.estimate.org_id

        super().save(*args, **kwargs)


# =============================================================================
# RECURRING INVOICE
# =============================================================================


class RecurringInvoice(AssignableMixin, BaseModel):
    """Template for auto-generating invoices on a schedule"""

    # Core Info
    title = models.CharField(_("Title"), max_length=100)
    is_active = models.BooleanField(_("Is Active"), default=True)

    # CRM Integration (same rules as Invoice - enforced via serializer)
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="recurring_invoices",
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recurring_invoices",
    )
    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recurring_invoices",
    )

    # Client Details
    client_name = models.CharField(_("Client Name"), max_length=255, default="")
    client_email = models.EmailField(_("Client Email"), default="")

    # Schedule
    frequency = models.CharField(
        _("Frequency"), max_length=20, choices=RECURRING_FREQUENCIES, default="MONTHLY"
    )
    custom_days = models.PositiveIntegerField(
        _("Custom Days Interval"), null=True, blank=True
    )
    start_date = models.DateField(_("Start Date"), default=datetime.date.today)
    end_date = models.DateField(_("End Date"), null=True, blank=True)
    next_generation_date = models.DateField(
        _("Next Generation Date"), default=datetime.date.today
    )

    # Settings
    payment_terms = models.CharField(
        _("Payment Terms"), max_length=20, choices=PAYMENT_TERMS, default="NET_30"
    )
    auto_send = models.BooleanField(_("Auto Send"), default=False)
    currency = models.CharField(
        _("Currency"), max_length=3, choices=CURRENCY_CODES, default="USD"
    )

    # Financial
    subtotal = models.DecimalField(
        _("Subtotal"), max_digits=12, decimal_places=2, default=0
    )
    discount_type = models.CharField(
        _("Discount Type"), max_length=20, choices=DISCOUNT_TYPES, blank=True
    )
    discount_value = models.DecimalField(
        _("Discount Value"), max_digits=12, decimal_places=2, default=0
    )
    tax_rate = models.DecimalField(
        _("Tax Rate (%)"), max_digits=5, decimal_places=2, default=0
    )
    total_amount = models.DecimalField(
        _("Total Amount"), max_digits=12, decimal_places=2, default=0
    )

    # Notes
    notes = models.TextField(_("Notes"), blank=True)
    terms = models.TextField(_("Terms & Conditions"), blank=True)

    custom_fields = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-org schema extension; values are validated against common.CustomFieldDefinition.",
    )

    # Statistics
    invoices_generated = models.PositiveIntegerField(_("Invoices Generated"), default=0)

    # Assignment
    assigned_to = models.ManyToManyField(
        Profile, related_name="recurring_invoice_assigned_to"
    )
    teams = models.ManyToManyField(Teams, related_name="recurring_invoice_teams")

    # Organization
    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="recurring_invoices"
    )

    class Meta:
        verbose_name = "Recurring Invoice"
        verbose_name_plural = "Recurring Invoices"
        db_table = "recurring_invoice"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["org", "-created_at"]),
            models.Index(fields=["org", "is_active"]),
            models.Index(fields=["next_generation_date"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.frequency})"

    def calculate_next_date(self):
        """Calculate the next generation date based on frequency"""
        from datetime import timedelta

        from dateutil.relativedelta import relativedelta

        current = self.next_generation_date

        if self.frequency == "WEEKLY":
            return current + timedelta(weeks=1)
        if self.frequency == "BIWEEKLY":
            return current + timedelta(weeks=2)
        if self.frequency == "MONTHLY":
            return current + relativedelta(months=1)
        if self.frequency == "QUARTERLY":
            return current + relativedelta(months=3)
        if self.frequency == "SEMI_ANNUALLY":
            return current + relativedelta(months=6)
        if self.frequency == "YEARLY":
            return current + relativedelta(years=1)
        if self.frequency == "CUSTOM" and self.custom_days:
            return current + timedelta(days=self.custom_days)

        return current + relativedelta(months=1)


class RecurringInvoiceLineItem(LineAmounts, BaseModel):
    """Line item template for recurring invoices"""

    recurring_invoice = models.ForeignKey(
        RecurringInvoice, on_delete=models.CASCADE, related_name="line_items"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recurring_invoice_line_items",
    )
    name = models.CharField(_("Item Name"), max_length=255, blank=True, null=True)
    description = models.CharField(_("Description"), max_length=500, blank=True)
    quantity = models.DecimalField(
        _("Quantity"), max_digits=10, decimal_places=2, default=1
    )
    unit_price = models.DecimalField(
        _("Unit Price"), max_digits=12, decimal_places=2, default=0
    )

    discount_type = models.CharField(
        _("Discount Type"), max_length=20, choices=DISCOUNT_TYPES, blank=True
    )
    discount_value = models.DecimalField(
        _("Discount Value"), max_digits=12, decimal_places=2, default=0
    )
    tax_rate = models.DecimalField(
        _("Tax Rate (%)"), max_digits=5, decimal_places=2, default=0
    )

    order = models.PositiveIntegerField(_("Order"), default=0)
    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="recurring_invoice_line_items"
    )

    class Meta:
        verbose_name = "Recurring Invoice Line Item"
        verbose_name_plural = "Recurring Invoice Line Items"
        db_table = "recurring_invoice_line_item"
        ordering = ("order",)

    def __str__(self):
        return f"{self.recurring_invoice.title} - {self.description}"

    def save(self, *args, **kwargs):
        if not self.org_id and self.recurring_invoice_id:
            self.org_id = self.recurring_invoice.org_id
        super().save(*args, **kwargs)


# =============================================================================
# INVOICE HISTORY (Audit Trail)
# =============================================================================


class InvoiceHistory(BaseModel):
    """
    Audit trail for Invoice changes.
    Tracks updates made to the original invoice object.
    """

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="invoice_history"
    )
    invoice_title = models.CharField(_("Invoice Title"), max_length=100)
    invoice_number = models.CharField(_("Invoice Number"), max_length=50)
    status = models.CharField(
        _("Status"), choices=INVOICE_STATUS, max_length=20, default="Draft"
    )

    # Snapshot of key fields at time of change
    client_name = models.CharField(_("Client Name"), max_length=255, blank=True)
    client_email = models.EmailField(_("Client Email"), blank=True)
    total_amount = models.DecimalField(
        _("Total Amount"), max_digits=12, decimal_places=2, default=0
    )
    amount_due = models.DecimalField(
        _("Amount Due"), max_digits=12, decimal_places=2, default=0
    )
    currency = models.CharField(
        _("Currency"), max_length=3, choices=CURRENCY_CODES, blank=True, null=True
    )
    due_date = models.DateField(_("Due Date"), blank=True, null=True)

    # Change tracking
    updated_by = models.ForeignKey(
        Profile,
        related_name="invoice_history_updated_by",
        on_delete=models.SET_NULL,
        null=True,
    )
    details = models.TextField(_("Change Details"), blank=True, null=True)

    # Organization
    org = models.ForeignKey(
        Org, on_delete=models.CASCADE, related_name="invoice_histories"
    )

    class Meta:
        verbose_name = "Invoice History"
        verbose_name_plural = "Invoice Histories"
        db_table = "invoice_history"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["org", "-created_at"]),
            models.Index(fields=["invoice"]),
        ]

    def __str__(self):
        return f"{self.invoice_number} - {self.created_at}"

    def save(self, *args, **kwargs):
        if not self.org_id and self.invoice_id:
            self.org_id = self.invoice.org_id
        super().save(*args, **kwargs)

    @property
    def created_on_arrow(self):
        return timesince(self.created_at) + " ago"
