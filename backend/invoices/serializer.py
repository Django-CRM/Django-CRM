import re
from decimal import Decimal

from rest_framework import serializers

from accounts.access import visible_accounts_qs
from accounts.serializer import AccountPickerSerializer
from common.serializer import (
    OrganizationSerializer,
    ProfileSerializer,
    TeamsSerializer,
    UserSerializer,
)
from contacts.access import visible_contacts_qs
from contacts.models import Contact
from contacts.serializer import ContactPickerSerializer
from invoices.models import (
    Estimate,
    EstimateLineItem,
    Invoice,
    InvoiceHistory,
    InvoiceLineItem,
    InvoiceTemplate,
    Payment,
    Product,
    RecurringInvoice,
    RecurringInvoiceLineItem,
    line_discount,
    line_gross,
)
from opportunity.access import visible_deals_qs
from opportunity.models import Opportunity

# =============================================================================
# MINIMAL OPPORTUNITY SERIALIZER (to avoid circular import)
# =============================================================================


class OpportunityMinimalSerializer(serializers.ModelSerializer):
    """Minimal Opportunity serializer for invoice context - avoids circular import"""

    class Meta:
        model = Opportunity
        fields = (
            "id",
            "name",
            "amount",
            "stage",
            "currency",
        )


# =============================================================================
# PRODUCT SERIALIZERS
# =============================================================================


class ProductSerializer(serializers.ModelSerializer):
    """Serializer for Product catalog"""

    used_on = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "description",
            "sku",
            "price",
            "currency",
            "category",
            "is_active",
            "used_on",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def get_used_on(self, obj):
        """Distinct invoices this product is a line item on.

        Line items denormalise their own name and unit price, so retiring or
        even deleting a product never rewrites a historic invoice (the FK is
        SET_NULL); this count is why a retired product is still worth listing.
        `ProductListView` annotates `used_on_count` to avoid an N+1 across the
        list, so read that when present and fall back to a query on the single
        detail views.
        """
        annotated = getattr(obj, "used_on_count", None)
        if annotated is not None:
            return annotated
        return obj.invoice_line_items.values("invoice_id").distinct().count()


class ProductCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating Products"""

    class Meta:
        model = Product
        fields = (
            "name",
            "description",
            "sku",
            "price",
            "currency",
            "category",
            "is_active",
        )

    def create(self, validated_data):
        request = self.context.get("request")
        if request and hasattr(request, "profile"):
            validated_data["org"] = request.profile.org
            # Default currency from org if not provided
            if not validated_data.get("currency") and request.profile.org:
                validated_data["currency"] = getattr(
                    request.profile.org, "default_currency", "USD"
                )
        return super().create(validated_data)


# =============================================================================
# INVOICE TEMPLATE SERIALIZERS
# =============================================================================


class InvoiceTemplateSerializer(serializers.ModelSerializer):
    """Full template serializer, EXPOSES the raw ``template_html`` /
    ``template_css``.

    Those two fields are org-authored HTML/CSS rendered into a PDF server-side;
    handing them to a browser is a stored-XSS vector. This serializer must only
    ever back an admin-only editor fetch. No list, detail, create/update, or
    nested response uses it. Those all use ``InvoiceTemplateListSerializer``
    (below), which never returns the markup. Do not wire this to a wide-read or
    non-admin surface.
    """

    class Meta:
        model = InvoiceTemplate
        fields = (
            "id",
            "name",
            "logo",
            "primary_color",
            "secondary_color",
            "template_html",
            "template_css",
            "default_notes",
            "default_terms",
            "footer_text",
            "is_default",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class InvoiceTemplateListSerializer(serializers.ModelSerializer):
    """Safe template representation, never returns the raw markup.

    ``template_html`` / ``template_css`` are org-authored HTML/CSS that
    WeasyPrint renders into a PDF server-side. The moment either reaches a
    browser DOM (an accidental ``{@html}``) a PDF setting becomes stored XSS, so
    they are kept off every list/detail/create/nested response. Callers get
    booleans and a byte count describing the markup, never the markup itself.
    This is the serializer every read path uses.
    """

    has_logo = serializers.SerializerMethodField()
    has_custom_html = serializers.SerializerMethodField()
    has_custom_css = serializers.SerializerMethodField()
    custom_html_bytes = serializers.SerializerMethodField()
    used_on_invoices = serializers.SerializerMethodField()
    updated_by = serializers.SerializerMethodField()

    class Meta:
        model = InvoiceTemplate
        fields = (
            "id",
            "name",
            "is_default",
            "primary_color",
            "secondary_color",
            "has_logo",
            "has_custom_html",
            "has_custom_css",
            "custom_html_bytes",
            "default_notes",
            "default_terms",
            "footer_text",
            "used_on_invoices",
            "created_at",
            "updated_at",
            "updated_by",
        )

    def get_has_logo(self, obj):
        return bool(obj.logo)

    def get_has_custom_html(self, obj):
        return bool(obj.template_html)

    def get_has_custom_css(self, obj):
        return bool(obj.template_css)

    def get_custom_html_bytes(self, obj):
        return len((obj.template_html or "").encode("utf-8"))

    def get_used_on_invoices(self, obj):
        # ``InvoiceTemplateListView`` annotates this to avoid an N+1; fall back
        # to a direct count for single-object (detail/create) and nested uses.
        annotated = getattr(obj, "used_on_invoices_count", None)
        if annotated is not None:
            return annotated
        return obj.invoices.count()

    def get_updated_by(self, obj):
        user = obj.updated_by or obj.created_by
        if not user:
            return None
        return (user.name or "").strip() or user.email


HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _validate_hex_color(value):
    """Refuse anything that is not ``#RRGGBB``.

    Both colour fields are substituted straight into the PDF stylesheet
    (``pdf.py`` does ``css_content.replace("#3B82F6", template.primary_color)``),
    so a value the browser will not parse does not fail loudly, it silently
    prints every invoice in the org with a broken accent colour. The model is
    ``max_length=7`` and nothing else, which accepted ``"purple"`` happily.

    All three clients already constrain this: both web forms use
    ``input type="color"`` and the phone picks from swatches, so each one can
    only ever submit six hex digits. That made this the last field where the
    only thing standing between a typo and a broken PDF was a client, which is
    the shape ``API Validation & Authorization`` exists to rule out.
    """
    value = (value or "").strip()
    if not HEX_COLOR.match(value):
        raise serializers.ValidationError(
            "Use a six digit hex colour, for example #3B82F6."
        )
    return value


class InvoiceTemplateCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating Invoice Templates"""

    class Meta:
        model = InvoiceTemplate
        fields = (
            "name",
            "logo",
            "primary_color",
            "secondary_color",
            "template_html",
            "template_css",
            "default_notes",
            "default_terms",
            "footer_text",
            "is_default",
        )

    def validate_primary_color(self, value):
        return _validate_hex_color(value)

    def validate_secondary_color(self, value):
        return _validate_hex_color(value)

    def create(self, validated_data):
        request = self.context.get("request")
        if request and hasattr(request, "profile"):
            validated_data["org"] = request.profile.org
        return super().create(validated_data)


# =============================================================================
# LINE ITEM SERIALIZERS
# =============================================================================


def _net_amount_field():
    """A line's ``net_amount`` (``invoices.models.LineAmounts``): what it adds
    to the document's subtotal, and the figure both clients show as the
    line's amount. ``total`` also carries the line's own tax, which the
    document does not charge."""
    return serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)


class InvoiceLineItemSerializer(serializers.ModelSerializer):
    """Serializer for Invoice Line Items"""

    product_name = serializers.CharField(source="product.name", read_only=True)
    formatted_unit_price = serializers.CharField(read_only=True)
    formatted_total = serializers.CharField(read_only=True)
    net_amount = _net_amount_field()

    class Meta:
        model = InvoiceLineItem
        fields = (
            "id",
            "product",
            "product_name",
            "name",
            "description",
            "quantity",
            "unit_price",
            "discount_type",
            "discount_value",
            "discount_amount",
            "net_amount",
            "tax_rate",
            "tax_amount",
            "subtotal",
            "total",
            "order",
            "formatted_unit_price",
            "formatted_total",
        )
        read_only_fields = (
            "id",
            "discount_amount",
            "tax_amount",
            "subtotal",
            "total",
        )


class LineAmountsMixin:
    """The two numbers on a written line that both clients already constrain.

    The web builder and the mobile line sheet refuse a quantity of zero or
    less, and neither offers a negative price, but the API took both, so a
    direct call could write a line that silently reduces the bill. Shared by
    the three line-item create serializers below.
    """

    def validate_quantity(self, value):
        if value <= 0:
            raise serializers.ValidationError("Quantity must be more than zero.")
        return value

    def validate_unit_price(self, value):
        if value < 0:
            raise serializers.ValidationError("Unit price cannot be negative.")
        return value

    def validate(self, attrs):
        """A line discount takes something off the line and never more.

        It counts toward the document's total (``LineAmounts.net_amount``), so
        a negative one would raise the bill, over 100% or over the line's
        amount would make the line pay the customer. On a partial update the
        fields not sent are the stored ones. Only the line's own instance is
        read: nested under a document this runs with no instance at all.
        """
        attrs = super().validate(attrs)
        stored = self.instance if isinstance(self.instance, self.Meta.model) else None

        def value_of(name, default):
            return attrs.get(name, getattr(stored, name, default))

        kind = value_of("discount_type", "")
        value = value_of("discount_value", Decimal("0")) or Decimal("0")
        if value < 0:
            raise serializers.ValidationError(
                {"discount_value": "A discount cannot be negative."}
            )
        if kind == "PERCENTAGE":
            if value > 100:
                raise serializers.ValidationError(
                    {"discount_value": "A percentage discount cannot exceed 100."}
                )
        elif value > value_of("quantity", Decimal("1")) * value_of(
            "unit_price", Decimal("0")
        ):
            raise serializers.ValidationError(
                {"discount_value": "A discount cannot exceed the line's amount."}
            )
        return attrs


class InvoiceLineItemCreateSerializer(LineAmountsMixin, serializers.ModelSerializer):
    """Serializer for creating/updating Invoice Line Items"""

    class Meta:
        model = InvoiceLineItem
        fields = (
            "product",
            "name",
            "description",
            "quantity",
            "unit_price",
            "discount_type",
            "discount_value",
            "tax_rate",
            "order",
        )

    def __init__(self, *args, **kwargs):
        request_obj = kwargs.pop("request_obj", None)
        super().__init__(*args, **kwargs)
        self.org = None
        if request_obj and hasattr(request_obj, "profile"):
            self.org = request_obj.profile.org

    def validate_product(self, value):
        """Reject a product belonging to another organization.

        Only the standalone routes (`InvoiceLineItemListView.post` and
        `InvoiceLineItemDetailView.put`) pass `request_obj`, and they are the
        only callers that can: nested under `InvoiceCreateSerializer` this runs
        as a child with no org context, which is why the nested path is guarded
        by `validate_line_item_products` on the parent instead.
        """
        if value is not None and self.org and value.org_id != self.org.id:
            raise serializers.ValidationError(
                "Product not found or does not belong to your organization"
            )
        return value


class EstimateLineItemCreateSerializer(LineAmountsMixin, serializers.ModelSerializer):
    """Serializer for creating/updating Estimate Line Items"""

    class Meta:
        model = EstimateLineItem
        fields = (
            "product",
            "name",
            "description",
            "quantity",
            "unit_price",
            "discount_type",
            "discount_value",
            "tax_rate",
            "order",
        )


class RecurringInvoiceLineItemCreateSerializer(
    LineAmountsMixin, serializers.ModelSerializer
):
    """Serializer for creating/updating Recurring Invoice Line Items"""

    class Meta:
        model = RecurringInvoiceLineItem
        fields = (
            "product",
            "name",
            "description",
            "quantity",
            "unit_price",
            "discount_type",
            "discount_value",
            "tax_rate",
            "order",
        )


def validate_line_item_products(line_items, org):
    """Reject line items that reference a product from another organization.

    The three line-item create serializers above expose `product` as an
    auto-generated PrimaryKeyRelatedField over an unscoped queryset, so a
    product UUID belonging to any org resolves. Their parent serializer is the
    first place with org context, which is why this is called from each
    parent's `validate()` instead of living on the line-item serializer.

    One function rather than three copies: invoices had the check and
    estimates and recurring invoices did not, which is exactly how a guard
    goes missing when it is written per call site. Guards create and update,
    since `validate()` runs on both.
    """
    if org is None:
        return
    for item in line_items or []:
        product = item.get("product")
        if product is not None and product.org_id != org.id:
            raise serializers.ValidationError(
                {
                    "line_items": (
                        "A line item references a product from another organization."
                    )
                }
            )


# The document fields its totals are computed from, besides its lines. An edit
# that sends none of them and no lines leaves an issued document's stored
# totals alone.
TOTALS_INPUTS = frozenset(
    ("discount_type", "discount_value", "tax_rate", "shipping_amount")
)


def validate_document_discount(attrs, instance):
    """A document's own discount: not negative, not over 100%, and a flat one
    not more than the subtotal it comes off.

    Shared by the invoice, estimate and recurring invoice serializers. On a
    partial update the side not sent is the stored one: the lines sent in this
    request, or else the document's stored lines. Runs only when the request
    touches the discount or the lines, so an edit that leaves both alone is
    never refused over a stored value.
    """
    if not {"discount_type", "discount_value", "line_items"} & attrs.keys():
        return

    def value_of(name, default):
        return attrs.get(name, getattr(instance, name, default))

    kind = value_of("discount_type", "")
    value = value_of("discount_value", Decimal("0")) or Decimal("0")
    if value < 0:
        raise serializers.ValidationError(
            {"discount_value": "A discount cannot be negative."}
        )
    if kind == "PERCENTAGE":
        if value > 100:
            raise serializers.ValidationError(
                {"discount_value": "A percentage discount cannot exceed 100."}
            )
        return
    if "line_items" in attrs:
        subtotal = Decimal("0")
        for item in attrs["line_items"]:
            gross = line_gross(item.get("quantity", 1), item.get("unit_price", 0))
            subtotal += gross - line_discount(
                gross, item.get("discount_type", ""), item.get("discount_value", 0)
            )
    elif instance is not None:
        subtotal = sum(
            (item.net_amount for item in instance.line_items.all()), Decimal("0")
        )
    else:
        subtotal = Decimal("0")
    if value > subtotal:
        raise serializers.ValidationError(
            {"discount_value": "A discount cannot exceed the subtotal."}
        )


def _validate_document_link(value, visible, stored_id, label):
    """Shared by the contact, account and deal checks below.

    The caller must be able to open the record: ``visible`` is the module's
    own visibility queryset, which also scopes to their org. Checking the org
    alone let a member put a record they cannot open on a document they then
    own as its creator, and read it back through the document.

    Keeping the value the document already has is allowed even when the
    caller cannot open it, the same rule `replace_visible_contacts` applies to
    contact lists: an edit form sends back what it loaded, and resending the
    stored value discloses nothing new.

    Refused with a 400 rather than dropped. Each is a single FK on the
    document, so dropping it would save a different document from the one the
    caller asked for. The message is the same for another org's record, a
    hidden one and one that does not exist, so the answer does not reveal
    that the record exists.
    """
    if stored_id is not None and stored_id == value:
        return value
    if not visible.filter(id=value).exists():
        raise serializers.ValidationError(
            f"{label} not found, or you do not have access to it."
        )
    return value


def validate_document_contact(contact_id, profile, instance):
    """The contact on an invoice, estimate or recurring invoice."""
    return _validate_document_link(
        contact_id,
        visible_contacts_qs(profile),
        instance.contact_id if instance is not None else None,
        "Contact",
    )


def validate_document_account(account_id, profile, instance):
    """The account an invoice, estimate or recurring invoice is billed to."""
    return _validate_document_link(
        account_id,
        visible_accounts_qs(profile, profile.user),
        instance.account_id if instance is not None else None,
        "Account",
    )


def validate_document_opportunity(opportunity_id, profile, instance):
    """The deal an invoice, estimate or recurring invoice is raised against."""
    return _validate_document_link(
        opportunity_id,
        visible_deals_qs(profile, profile.user),
        instance.opportunity_id if instance is not None else None,
        "Opportunity",
    )


# =============================================================================
# PAYMENT SERIALIZERS
# =============================================================================


class PaymentSerializer(serializers.ModelSerializer):
    """Serializer for Payments"""

    class Meta:
        model = Payment
        fields = (
            "id",
            "invoice",
            "amount",
            "payment_date",
            "payment_method",
            "reference_number",
            "notes",
            "created_at",
        )
        read_only_fields = ("id", "created_at")


class PaymentCreateSerializer(serializers.ModelSerializer):
    """Serializer for recording Payments.

    Pass the target invoice via ``context={"invoice": invoice}`` so the amount
    can be bounded by the outstanding balance and the invoice's status can be
    checked. Both endpoints that record a payment (``mark-paid`` and
    ``payments``) go through here, which is why the rules live on the
    serializer rather than in either view.
    """

    class Meta:
        model = Payment
        fields = (
            "amount",
            "payment_date",
            "payment_method",
            "reference_number",
            "notes",
        )

    def validate(self, attrs):
        """Refuse a payment against an invoice that is not owed.

        A cancelled invoice is the case that matters. It can still carry a
        non-zero ``amount_due``, so the amount check below passes, and
        ``Payment.save()`` calls ``update_invoice_payment()``, which writes
        ``status`` unconditionally. The effect was not merely an accepted
        payment: it moved the invoice out of Cancelled to Paid or
        Partially_Paid, silently undoing the cancellation. Neither ``send``
        nor ``cancel`` would allow that transition.

        A *paid* invoice needs no rule here: it has nothing outstanding, so
        ``validate_amount`` already rejects every amount as above the balance.
        """
        invoice = self.context.get("invoice")
        if invoice is not None and invoice.status == "Cancelled":
            raise serializers.ValidationError(
                "Cannot record a payment against a cancelled invoice."
            )
        return attrs

    def validate_amount(self, value):
        # Payment totals are a SUM over this invoice's payments, so a zero or
        # negative amount would silently walk amount_paid back down.
        if value <= Decimal("0"):
            raise serializers.ValidationError(
                "Payment amount must be greater than zero."
            )

        invoice = self.context.get("invoice")
        if invoice is not None and value > invoice.amount_due:
            raise serializers.ValidationError(
                f"Payment amount exceeds the amount due ({invoice.amount_due})."
            )

        return value


# =============================================================================
# INVOICE SERIALIZERS
# =============================================================================


class InvoiceListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for Invoice list views"""

    account_name = serializers.CharField(source="account.name", read_only=True)
    contact_name = serializers.SerializerMethodField()
    line_items_count = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = (
            "id",
            "invoice_number",
            "invoice_title",
            "status",
            "account",
            "account_name",
            "contact",
            "contact_name",
            "client_name",
            "client_email",
            "issue_date",
            "due_date",
            "total_amount",
            "amount_due",
            "amount_paid",
            "currency",
            "is_overdue",
            "line_items_count",
            "created_at",
        )

    def get_contact_name(self, obj):
        if obj.contact:
            return f"{obj.contact.first_name} {obj.contact.last_name}"
        return None

    def get_line_items_count(self, obj):
        return obj.line_items.count()


class InvoiceSerializer(serializers.ModelSerializer):
    """Full Invoice serializer with nested relationships"""

    # `{id, name}` only. Seeing this record is not access to its account, and
    # both clients read only these two; the rest is on `/api/accounts/<id>/`.
    account = AccountPickerSerializer(read_only=True)
    # The name only: mobile reads it, the web reads `contact_name` from lists.
    contact = ContactPickerSerializer(read_only=True)
    opportunity = OpportunityMinimalSerializer(read_only=True)
    template = InvoiceTemplateListSerializer(read_only=True)
    line_items = InvoiceLineItemSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    created_by = UserSerializer(read_only=True)
    assigned_to = ProfileSerializer(read_only=True, many=True)
    teams = TeamsSerializer(read_only=True, many=True)
    org = OrganizationSerializer(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    public_url = serializers.CharField(read_only=True)

    class Meta:
        model = Invoice
        fields = (
            "id",
            "invoice_number",
            "invoice_title",
            "status",
            # CRM Integration
            "account",
            "contact",
            "opportunity",
            # Client Details
            "client_name",
            "client_email",
            "client_phone",
            # Billing Address
            "billing_address_line",
            "billing_city",
            "billing_state",
            "billing_postcode",
            "billing_country",
            # Client Address
            "client_address_line",
            "client_city",
            "client_state",
            "client_postcode",
            "client_country",
            # Financial
            "subtotal",
            "discount_type",
            "discount_value",
            "discount_amount",
            "tax_rate",
            "tax_amount",
            "shipping_amount",
            "total_amount",
            "currency",
            "amount_paid",
            "amount_due",
            # Dates
            "issue_date",
            "due_date",
            "payment_terms",
            "sent_at",
            "viewed_at",
            "paid_at",
            # Reminders
            "reminder_enabled",
            "reminder_days_before",
            "reminder_days_after",
            "reminder_frequency",
            "last_reminder_sent",
            "reminder_count",
            # Client Portal
            "public_token",
            "public_link_enabled",
            "public_url",
            # Template
            "template",
            # Notes
            "notes",
            "terms",
            "details",
            # Additional Metadata
            "billing_period",
            "po_number",
            "custom_fields",
            # Related
            "line_items",
            "payments",
            # Assignment
            "assigned_to",
            "teams",
            # Meta
            "is_overdue",
            "created_at",
            "updated_at",
            "created_by",
            "org",
        )


class InvoiceCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating Invoices with CRM validation"""

    # CRM Integration fields - accept IDs for writing
    account_id = serializers.UUIDField(write_only=True, required=True)
    contact_id = serializers.UUIDField(write_only=True, required=True)
    opportunity_id = serializers.UUIDField(
        write_only=True, required=False, allow_null=True
    )
    template_id = serializers.UUIDField(
        write_only=True, required=False, allow_null=True
    )

    # Line items for bulk creation
    line_items = InvoiceLineItemCreateSerializer(many=True, required=False)

    class Meta:
        model = Invoice
        fields = (
            "invoice_title",
            "status",
            # CRM Integration (IDs)
            "account_id",
            "contact_id",
            "opportunity_id",
            # Client Details
            "client_name",
            "client_email",
            "client_phone",
            # Billing Address
            "billing_address_line",
            "billing_city",
            "billing_state",
            "billing_postcode",
            "billing_country",
            # Client Address
            "client_address_line",
            "client_city",
            "client_state",
            "client_postcode",
            "client_country",
            # Financial
            "discount_type",
            "discount_value",
            "tax_rate",
            "shipping_amount",
            "currency",
            # Dates
            "issue_date",
            "due_date",
            "payment_terms",
            # Reminders
            "reminder_enabled",
            "reminder_days_before",
            "reminder_days_after",
            "reminder_frequency",
            # Client Portal
            "public_link_enabled",
            # Template
            "template_id",
            # Notes
            "notes",
            "terms",
            # Additional Metadata
            "billing_period",
            "po_number",
            # Line Items
            "line_items",
        )
        # `status` is a lifecycle field, not a client-set one. A new invoice is
        # always created as Draft (the model default); status then advances only
        # through the validated lifecycle endpoints (InvoiceSendView /
        # InvoiceMarkPaidView / InvoiceCancelView). Left writable here, a member
        # could POST status="Paid" and mint an invoice that reports as paid with
        # no Payment behind it: corrupting AR, aging, and revenue. Read-only
        # closes that on both create and update.
        read_only_fields = ("status",)

    def __init__(self, *args, **kwargs):
        request_obj = kwargs.pop("request_obj", None)
        super().__init__(*args, **kwargs)
        self.org = None
        self.profile = None
        if request_obj and hasattr(request_obj, "profile"):
            self.org = request_obj.profile.org
            self.profile = request_obj.profile

    def validate_account_id(self, value):
        """Validate account exists, belongs to org, and the caller may open it"""
        if not self.org:
            raise serializers.ValidationError("Organization context required")
        return validate_document_account(value, self.profile, self.instance)

    def validate_contact_id(self, value):
        """Validate contact exists, belongs to org, and the caller may open it"""
        if not self.org:
            raise serializers.ValidationError("Organization context required")
        return validate_document_contact(value, self.profile, self.instance)

    def validate_opportunity_id(self, value):
        """Validate the deal (if provided) is one the caller may open"""
        if value is None:
            return value
        if not self.org:
            raise serializers.ValidationError("Organization context required")
        return validate_document_opportunity(value, self.profile, self.instance)

    def validate_template_id(self, value):
        """Validate template exists and belongs to org (if provided).

        Without this, a member could attach another org's InvoiceTemplate UUID
        and render its markup/branding on this invoice's PDF, a cross-tenant
        IDOR. Mirrors the account/contact/opportunity checks above.
        """
        if value is None:
            return value
        if not self.org:
            raise serializers.ValidationError("Organization context required")
        if not InvoiceTemplate.objects.filter(id=value, org=self.org).exists():
            raise serializers.ValidationError(
                "Template not found or does not belong to your organization"
            )
        return value

    def validate(self, attrs):
        """Cross-field validation"""
        account_id = attrs.get("account_id")
        contact_id = attrs.get("contact_id")

        # Validate contact belongs to account (if both provided)
        if account_id and contact_id and self.org:
            contact = Contact.objects.filter(id=contact_id, org=self.org).first()
            if contact and contact.account_id and contact.account_id != account_id:
                raise serializers.ValidationError(
                    {"contact_id": "Contact does not belong to the selected account"}
                )

        validate_line_item_products(attrs.get("line_items"), self.org)
        validate_document_discount(attrs, self.instance)
        return attrs

    def create(self, validated_data):
        line_items_data = validated_data.pop("line_items", [])

        # Set org
        validated_data["org"] = self.org

        # Create invoice
        invoice = Invoice.objects.create(**validated_data)

        # Create line items
        for idx, item_data in enumerate(line_items_data):
            InvoiceLineItem.objects.create(
                invoice=invoice,
                org=self.org,
                order=item_data.get("order", idx),
                **{k: v for k, v in item_data.items() if k != "order"},
            )

        # Recalculate totals
        invoice.recalculate_totals()
        invoice.save()

        return invoice

    def update(self, instance, validated_data):
        line_items_data = validated_data.pop("line_items", None)

        # Update invoice fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        # Handle line items if provided
        if line_items_data is not None:
            # Delete existing and recreate
            instance.line_items.all().delete()
            for idx, item_data in enumerate(line_items_data):
                InvoiceLineItem.objects.create(
                    invoice=instance,
                    org=self.org or instance.org,
                    order=item_data.get("order", idx),
                    **{k: v for k, v in item_data.items() if k != "order"},
                )

        # Only an edit to what the totals are made of recomputes them; see
        # `totals_follow_lines` (a Draft recomputes on save regardless).
        if line_items_data is not None or TOTALS_INPUTS & validated_data.keys():
            instance.recalculate_totals()
        instance.save()

        return instance


# =============================================================================
# INVOICE HISTORY SERIALIZER
# =============================================================================


class InvoiceHistorySerializer(serializers.ModelSerializer):
    """Serializer for Invoice History (audit trail)"""

    updated_by = ProfileSerializer(read_only=True)

    class Meta:
        model = InvoiceHistory
        fields = (
            "id",
            "invoice",
            "invoice_title",
            "invoice_number",
            "status",
            "client_name",
            "client_email",
            "total_amount",
            "amount_due",
            "currency",
            "due_date",
            "details",
            "updated_by",
            "created_at",
        )


# =============================================================================
# ESTIMATE SERIALIZERS
# =============================================================================


class EstimateLineItemSerializer(serializers.ModelSerializer):
    """Serializer for Estimate Line Items"""

    product_name = serializers.CharField(source="product.name", read_only=True)
    net_amount = _net_amount_field()

    class Meta:
        model = EstimateLineItem
        fields = (
            "id",
            "product",
            "product_name",
            "name",
            "description",
            "quantity",
            "unit_price",
            "discount_type",
            "discount_value",
            "discount_amount",
            "net_amount",
            "tax_rate",
            "tax_amount",
            "subtotal",
            "total",
            "order",
        )
        read_only_fields = ("id", "discount_amount", "tax_amount", "subtotal", "total")


class EstimateListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for Estimate list views.

    ``opportunity`` and ``converted_to_invoice`` are on the list row on purpose:
    the estimates worklist exists to separate "accepted but not yet billed"
    (money agreed, no invoice) from "already billed". Without the conversion
    reference the client cannot tell the two apart and would offer to raise a
    second invoice for an estimate that already has one. Both are trimmed to the
    two fields the list needs -- never the full nested object.
    """

    account_name = serializers.CharField(source="account.name", read_only=True)
    contact_name = serializers.SerializerMethodField()
    opportunity = serializers.SerializerMethodField()
    converted_to_invoice = serializers.SerializerMethodField()

    class Meta:
        model = Estimate
        fields = (
            "id",
            "estimate_number",
            "title",
            "status",
            "account",
            "account_name",
            "contact",
            "contact_name",
            "opportunity",
            "converted_to_invoice",
            "client_name",
            "issue_date",
            "expiry_date",
            "total_amount",
            "currency",
            "is_expired",
            "created_at",
        )

    def get_contact_name(self, obj):
        if obj.contact:
            return f"{obj.contact.first_name} {obj.contact.last_name}"
        return None

    def get_opportunity(self, obj):
        if obj.opportunity_id:
            return {"id": str(obj.opportunity_id), "name": obj.opportunity.name}
        return None

    def get_converted_to_invoice(self, obj):
        invoice = obj.converted_to_invoice
        if invoice:
            return {"id": str(invoice.id), "invoice_number": invoice.invoice_number}
        return None


class EstimateSerializer(serializers.ModelSerializer):
    """Full Estimate serializer"""

    # `{id, name}` only. Seeing this record is not access to its account, and
    # both clients read only these two; the rest is on `/api/accounts/<id>/`.
    account = AccountPickerSerializer(read_only=True)
    # The name only: mobile reads it, the web reads `contact_name` from lists.
    contact = ContactPickerSerializer(read_only=True)
    opportunity = OpportunityMinimalSerializer(read_only=True)
    converted_to_invoice = InvoiceListSerializer(read_only=True)
    line_items = EstimateLineItemSerializer(many=True, read_only=True)
    created_by = UserSerializer(read_only=True)
    assigned_to = ProfileSerializer(read_only=True, many=True)
    teams = TeamsSerializer(read_only=True, many=True)
    is_expired = serializers.BooleanField(read_only=True)
    public_url = serializers.CharField(read_only=True)

    class Meta:
        model = Estimate
        fields = "__all__"


class EstimateCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating Estimates"""

    account_id = serializers.UUIDField(write_only=True, required=True)
    contact_id = serializers.UUIDField(write_only=True, required=True)
    opportunity_id = serializers.UUIDField(
        write_only=True, required=False, allow_null=True
    )
    line_items = EstimateLineItemCreateSerializer(many=True, required=False)

    class Meta:
        model = Estimate
        fields = (
            "title",
            "status",
            "account_id",
            "contact_id",
            "opportunity_id",
            "client_name",
            "client_email",
            "client_phone",
            "client_address_line",
            "client_city",
            "client_state",
            "client_postcode",
            "client_country",
            "discount_type",
            "discount_value",
            "tax_rate",
            "currency",
            "issue_date",
            "expiry_date",
            "public_link_enabled",
            "notes",
            "terms",
            "line_items",
        )
        # Accepting a quote authorises its price, so it is a transition, not a
        # field edit. `PublicEstimateAcceptView` enforces the source state
        # (only Sent or Viewed), refuses an expired estimate, and records who
        # accepted it, their email, IP and user agent. This serializer backs
        # `PUT /api/estimates/<id>/` with `partial=True`, so leaving `status`
        # writable let any member with access to the estimate send
        # `{"status": "Accepted"}` and skip every one of those checks, landing
        # an Accepted estimate with no `accepted_at`, no acceptor and no
        # invoice. `InvoiceCreateSerializer` has always marked `status`
        # read-only; estimates were the asymmetry, not the rule.
        #
        # Nothing legitimate regresses: every backend write to
        # `estimate.status` is a guarded endpoint or a Celery task, each
        # setting the accompanying fields in the same step.
        read_only_fields = ("status",)

    def __init__(self, *args, **kwargs):
        request_obj = kwargs.pop("request_obj", None)
        super().__init__(*args, **kwargs)
        self.org = None
        self.profile = None
        if request_obj and hasattr(request_obj, "profile"):
            self.org = request_obj.profile.org
            self.profile = request_obj.profile

    def validate_account_id(self, value):
        """Validate account exists, belongs to org, and the caller may open it"""
        if not self.org:
            raise serializers.ValidationError("Organization context required")
        return validate_document_account(value, self.profile, self.instance)

    def validate_contact_id(self, value):
        """Validate contact exists, belongs to org, and the caller may open it"""
        if not self.org:
            raise serializers.ValidationError("Organization context required")
        return validate_document_contact(value, self.profile, self.instance)

    def validate_opportunity_id(self, value):
        """Validate the deal (if provided) is one the caller may open"""
        if value is None:
            return value
        if not self.org:
            raise serializers.ValidationError("Organization context required")
        return validate_document_opportunity(value, self.profile, self.instance)

    def validate(self, attrs):
        """Cross-field validation"""
        account_id = attrs.get("account_id")
        contact_id = attrs.get("contact_id")

        if account_id and contact_id and self.org:
            contact = Contact.objects.filter(id=contact_id, org=self.org).first()
            if contact and contact.account_id and contact.account_id != account_id:
                raise serializers.ValidationError(
                    {"contact_id": "Contact does not belong to the selected account"}
                )

        # On a partial edit the side not sent is the stored one, so moving
        # either date alone cannot leave the pair backwards.
        issue = attrs.get("issue_date", getattr(self.instance, "issue_date", None))
        expiry = attrs.get("expiry_date", getattr(self.instance, "expiry_date", None))
        if issue and expiry and expiry < issue:
            raise serializers.ValidationError(
                {"expiry_date": "An estimate cannot expire before it is issued."}
            )

        validate_line_item_products(attrs.get("line_items"), self.org)
        validate_document_discount(attrs, self.instance)
        return attrs

    def create(self, validated_data):
        line_items_data = validated_data.pop("line_items", [])
        validated_data["org"] = self.org

        estimate = Estimate.objects.create(**validated_data)

        for idx, item_data in enumerate(line_items_data):
            EstimateLineItem.objects.create(
                estimate=estimate,
                org=self.org,
                order=item_data.get("order", idx),
                **{k: v for k, v in item_data.items() if k != "order"},
            )

        estimate.recalculate_totals()
        estimate.save()
        return estimate

    def update(self, instance, validated_data):
        line_items_data = validated_data.pop("line_items", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if line_items_data is not None:
            instance.line_items.all().delete()
            for idx, item_data in enumerate(line_items_data):
                EstimateLineItem.objects.create(
                    estimate=instance,
                    org=self.org or instance.org,
                    order=item_data.get("order", idx),
                    **{k: v for k, v in item_data.items() if k != "order"},
                )

        # As `InvoiceCreateSerializer.update`.
        if line_items_data is not None or TOTALS_INPUTS & validated_data.keys():
            instance.recalculate_totals()
        instance.save()
        return instance


# =============================================================================
# RECURRING INVOICE SERIALIZERS
# =============================================================================


class RecurringInvoiceLineItemSerializer(serializers.ModelSerializer):
    """Serializer for Recurring Invoice Line Items"""

    product_name = serializers.CharField(source="product.name", read_only=True)
    net_amount = _net_amount_field()

    class Meta:
        model = RecurringInvoiceLineItem
        fields = (
            "id",
            "product",
            "product_name",
            "name",
            "description",
            "quantity",
            "unit_price",
            "discount_type",
            "discount_value",
            "net_amount",
            "tax_rate",
            "order",
        )
        read_only_fields = ("id",)


class RecurringInvoiceListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for Recurring Invoice list views.

    ``payment_terms``, ``custom_days`` and ``contact_name`` are on the row
    because the schedules worklist shows all three: the terms line, the "every N
    days" cadence for a CUSTOM frequency, and the contact under the account.
    ``custom_days`` is null unless ``frequency == 'CUSTOM'``.
    """

    account_name = serializers.CharField(source="account.name", read_only=True)
    contact_name = serializers.SerializerMethodField()

    class Meta:
        model = RecurringInvoice
        fields = (
            "id",
            "title",
            "account",
            "account_name",
            "contact",
            "contact_name",
            "client_name",
            "frequency",
            "custom_days",
            "payment_terms",
            "start_date",
            "end_date",
            "next_generation_date",
            "is_active",
            "auto_send",
            "total_amount",
            "currency",
            "invoices_generated",
            "created_at",
        )

    def get_contact_name(self, obj):
        if obj.contact:
            return f"{obj.contact.first_name} {obj.contact.last_name}"
        return None


class RecurringInvoiceSerializer(serializers.ModelSerializer):
    """Full Recurring Invoice serializer"""

    # `{id, name}` only. Seeing this record is not access to its account, and
    # both clients read only these two; the rest is on `/api/accounts/<id>/`.
    account = AccountPickerSerializer(read_only=True)
    # The name only: mobile reads it, the web reads `contact_name` from lists.
    contact = ContactPickerSerializer(read_only=True)
    opportunity = OpportunityMinimalSerializer(read_only=True)
    line_items = RecurringInvoiceLineItemSerializer(many=True, read_only=True)
    created_by = UserSerializer(read_only=True)
    assigned_to = ProfileSerializer(read_only=True, many=True)
    teams = TeamsSerializer(read_only=True, many=True)

    class Meta:
        model = RecurringInvoice
        fields = "__all__"


class RecurringInvoiceCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating Recurring Invoices"""

    account_id = serializers.UUIDField(write_only=True, required=True)
    contact_id = serializers.UUIDField(write_only=True, required=True)
    opportunity_id = serializers.UUIDField(
        write_only=True, required=False, allow_null=True
    )
    line_items = RecurringInvoiceLineItemCreateSerializer(many=True, required=False)

    class Meta:
        model = RecurringInvoice
        fields = (
            "title",
            "is_active",
            "account_id",
            "contact_id",
            "opportunity_id",
            "client_name",
            "client_email",
            "frequency",
            "custom_days",
            "start_date",
            "end_date",
            "next_generation_date",
            "payment_terms",
            "auto_send",
            "currency",
            "discount_type",
            "discount_value",
            "tax_rate",
            "notes",
            "terms",
            "line_items",
        )

    def __init__(self, *args, **kwargs):
        request_obj = kwargs.pop("request_obj", None)
        super().__init__(*args, **kwargs)
        self.org = None
        self.profile = None
        if request_obj and hasattr(request_obj, "profile"):
            self.org = request_obj.profile.org
            self.profile = request_obj.profile

    def validate_account_id(self, value):
        """Validate account exists, belongs to org, and the caller may open it"""
        if not self.org:
            raise serializers.ValidationError("Organization context required")
        return validate_document_account(value, self.profile, self.instance)

    def validate_contact_id(self, value):
        """Validate contact exists, belongs to org, and the caller may open it"""
        if not self.org:
            raise serializers.ValidationError("Organization context required")
        return validate_document_contact(value, self.profile, self.instance)

    def validate_opportunity_id(self, value):
        """Validate the deal (if provided) is one the caller may open"""
        if value is None:
            return value
        if not self.org:
            raise serializers.ValidationError("Organization context required")
        return validate_document_opportunity(value, self.profile, self.instance)

    def validate(self, attrs):
        """Cross-field validation"""
        account_id = attrs.get("account_id")
        contact_id = attrs.get("contact_id")

        if account_id and contact_id and self.org:
            contact = Contact.objects.filter(id=contact_id, org=self.org).first()
            if contact and contact.account_id and contact.account_id != account_id:
                raise serializers.ValidationError(
                    {"contact_id": "Contact does not belong to the selected account"}
                )

        # A CUSTOM cadence is meaningless without its interval.
        #
        # `calculate_next_date` reads `if self.frequency == "CUSTOM" and
        # self.custom_days:` and otherwise falls through to its final
        # `return current + relativedelta(months=1)`. So a CUSTOM schedule with
        # no interval was accepted and then billed monthly forever, with the
        # list still describing it as "Custom": the client asked for one thing
        # and the server quietly did another. Zero is caught by the same rule,
        # because it is falsy there and behaves identically to missing.
        #
        # `partial` guards the PUT path: an update that does not mention
        # frequency must be read against the stored value, not against an
        # absent one.
        frequency = attrs.get("frequency")
        if frequency is None and self.partial and self.instance is not None:
            frequency = self.instance.frequency
        if frequency == "CUSTOM":
            custom_days = attrs.get("custom_days")
            if custom_days is None and self.partial and self.instance is not None:
                custom_days = self.instance.custom_days
            if not custom_days:
                raise serializers.ValidationError(
                    {
                        "custom_days": (
                            "A custom frequency needs an interval in days. "
                            "Without one the schedule would bill monthly."
                        )
                    }
                )

        validate_line_item_products(attrs.get("line_items"), self.org)
        validate_document_discount(attrs, self.instance)
        return attrs

    def _recalculate_totals(self, instance):
        # The same subtotal an invoice generated from this schedule will have:
        # each line's net_amount, after its own discount.
        subtotal = sum(
            (item.net_amount for item in instance.line_items.all()),
            Decimal("0"),
        )
        instance.subtotal = subtotal

        # Capped at the subtotal, as on an invoice.
        if instance.discount_type == "PERCENTAGE":
            discount_amount = subtotal * (instance.discount_value / Decimal("100"))
        else:
            discount_amount = instance.discount_value
        discount_amount = min(discount_amount, subtotal)

        taxable = subtotal - discount_amount
        instance.total_amount = taxable + (
            taxable * (instance.tax_rate / Decimal("100"))
        )

    def create(self, validated_data):
        line_items_data = validated_data.pop("line_items", [])
        validated_data["org"] = self.org

        recurring = RecurringInvoice.objects.create(**validated_data)

        for idx, item_data in enumerate(line_items_data):
            RecurringInvoiceLineItem.objects.create(
                recurring_invoice=recurring,
                org=self.org,
                order=item_data.get("order", idx),
                **{k: v for k, v in item_data.items() if k != "order"},
            )

        self._recalculate_totals(recurring)
        recurring.save()
        return recurring

    def update(self, instance, validated_data):
        line_items_data = validated_data.pop("line_items", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if line_items_data is not None:
            instance.line_items.all().delete()
            for idx, item_data in enumerate(line_items_data):
                RecurringInvoiceLineItem.objects.create(
                    recurring_invoice=instance,
                    org=self.org or instance.org,
                    order=item_data.get("order", idx),
                    **{k: v for k, v in item_data.items() if k != "order"},
                )

        self._recalculate_totals(instance)
        instance.save()
        return instance


# =============================================================================
# PUBLIC PORTAL SERIALIZERS (No auth required)
# =============================================================================


class PublicInvoiceSerializer(serializers.ModelSerializer):
    """Serializer for public invoice viewing (no sensitive data)"""

    line_items = InvoiceLineItemSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = (
            "invoice_number",
            "invoice_title",
            "status",
            "client_name",
            "client_email",
            "billing_address_line",
            "billing_city",
            "billing_state",
            "billing_postcode",
            "billing_country",
            "client_address_line",
            "client_city",
            "client_state",
            "client_postcode",
            "client_country",
            "subtotal",
            "discount_type",
            "discount_value",
            "discount_amount",
            "tax_rate",
            "tax_amount",
            "shipping_amount",
            "total_amount",
            "currency",
            "amount_paid",
            "amount_due",
            "issue_date",
            "due_date",
            "payment_terms",
            "notes",
            "terms",
            "line_items",
        )


class PublicEstimateSerializer(serializers.ModelSerializer):
    """Serializer for public estimate viewing"""

    line_items = EstimateLineItemSerializer(many=True, read_only=True)

    class Meta:
        model = Estimate
        fields = (
            "estimate_number",
            "title",
            "status",
            "client_name",
            "client_email",
            "client_address_line",
            "client_city",
            "client_state",
            "client_postcode",
            "client_country",
            "subtotal",
            "discount_type",
            "discount_value",
            "discount_amount",
            "tax_rate",
            "tax_amount",
            "total_amount",
            "currency",
            "issue_date",
            "expiry_date",
            "notes",
            "terms",
            "line_items",
        )
