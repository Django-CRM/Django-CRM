from django import forms
from django.contrib import admin

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
)
from invoices.serializer import issued_lock_message

# The money on an issued invoice or estimate, read-only in the admin as it is
# in the API (`issued_lock_message`, owner decision 1.11.0). Status, dates,
# notes and payments stay editable.
INVOICE_AMOUNT_FIELDS = (
    "currency",
    "subtotal",
    "discount_type",
    "discount_value",
    "discount_amount",
    "tax_rate",
    "tax_amount",
    "shipping_amount",
    "total_amount",
    "amount_paid",
    "amount_due",
)
ESTIMATE_AMOUNT_FIELDS = (
    "currency",
    "subtotal",
    "discount_type",
    "discount_value",
    "discount_amount",
    "tax_rate",
    "tax_amount",
    "total_amount",
)


class IssuedAmountsReadOnlyMixin:
    """A document admin whose amount fields lock once it leaves Draft."""

    amount_fields = ()

    def get_readonly_fields(self, request, obj=None):
        readonly = tuple(super().get_readonly_fields(request, obj))
        if issued_lock_message(obj):
            return readonly + self.amount_fields
        return readonly


class IssuedLinesInlineMixin:
    """A line inline that can be read, not added, edited or deleted, under an
    issued document. ``obj`` is the parent document here."""

    def has_add_permission(self, request, obj=None):
        return not issued_lock_message(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return not issued_lock_message(obj) and super().has_change_permission(
            request, obj
        )

    def has_delete_permission(self, request, obj=None):
        return not issued_lock_message(obj) and super().has_delete_permission(
            request, obj
        )


class IssuedLineAdminMixin:
    """The standalone line admin: a line of an issued document cannot be
    changed or deleted, a new line cannot be put on one, and the bulk delete
    action (which would skip the per-line check) is not offered."""

    document_field = ""

    def _issued(self, line):
        return line is not None and issued_lock_message(
            getattr(line, self.document_field)
        )

    def has_change_permission(self, request, obj=None):
        return not self._issued(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not self._issued(obj) and super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def get_form(self, request, obj=None, **kwargs):
        base = super().get_form(request, obj, **kwargs)
        document_field = self.document_field

        class Form(base):
            def clean(self):
                cleaned = super().clean()
                message = issued_lock_message(cleaned.get(document_field))
                if message:
                    raise forms.ValidationError(message)
                return cleaned

        return Form


class InvoiceLineItemInline(IssuedLinesInlineMixin, admin.TabularInline):
    model = InvoiceLineItem
    extra = 1
    fields = (
        "product",
        "description",
        "quantity",
        "unit_price",
        "discount_type",
        "discount_value",
        "tax_rate",
        "total",
        "order",
    )
    readonly_fields = ("total",)


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    fields = ("amount", "payment_date", "payment_method", "reference_number", "notes")


@admin.register(Invoice)
class InvoiceAdmin(IssuedAmountsReadOnlyMixin, admin.ModelAdmin):
    amount_fields = INVOICE_AMOUNT_FIELDS
    list_display = (
        "invoice_number",
        "invoice_title",
        "client_name",
        "account",
        "status",
        "total_amount",
        "amount_due",
        "due_date",
        "created_at",
    )
    list_filter = ("status", "created_at", "due_date", "org")
    search_fields = (
        "invoice_number",
        "invoice_title",
        "client_name",
        "client_email",
        "account__name",
    )
    raw_id_fields = (
        "created_by",
        "org",
        "account",
        "contact",
        "opportunity",
        "template",
    )
    filter_horizontal = ("assigned_to", "teams")
    inlines = [InvoiceLineItemInline, PaymentInline]
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    fieldsets = (
        (
            "Invoice Info",
            {
                "fields": (
                    "invoice_title",
                    "invoice_number",
                    "status",
                    "template",
                )
            },
        ),
        (
            "CRM Integration",
            {
                "fields": (
                    "account",
                    "contact",
                    "opportunity",
                )
            },
        ),
        (
            "Client Details",
            {
                "fields": (
                    "client_name",
                    "client_email",
                    "client_phone",
                    "client_address_line",
                    "client_city",
                    "client_state",
                    "client_postcode",
                    "client_country",
                )
            },
        ),
        (
            "Billing Address",
            {
                "fields": (
                    "billing_address_line",
                    "billing_city",
                    "billing_state",
                    "billing_postcode",
                    "billing_country",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Financial",
            {
                "fields": (
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
                )
            },
        ),
        (
            "Dates & Terms",
            {
                "fields": (
                    "issue_date",
                    "due_date",
                    "payment_terms",
                    "sent_at",
                    "viewed_at",
                    "paid_at",
                )
            },
        ),
        (
            "Reminders",
            {
                "fields": (
                    "reminder_enabled",
                    "reminder_days_before",
                    "reminder_days_after",
                    "reminder_frequency",
                    "last_reminder_sent",
                    "reminder_count",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Client Portal",
            {
                "fields": (
                    "public_token",
                    "public_link_enabled",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Notes & Terms",
            {
                "fields": (
                    "notes",
                    "terms",
                    "details",
                )
            },
        ),
        (
            "Assignment",
            {
                "fields": (
                    "assigned_to",
                    "teams",
                    "org",
                )
            },
        ),
    )


@admin.register(InvoiceHistory)
class InvoiceHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "invoice",
        "invoice_number",
        "status",
        "total_amount",
        "updated_by",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = ("invoice_number", "invoice_title", "client_name")
    raw_id_fields = ("invoice", "updated_by", "org")
    ordering = ("-created_at",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "sku",
        "price",
        "currency",
        "category",
        "is_active",
        "org",
        "created_at",
    )
    list_filter = ("is_active", "category", "org", "created_at")
    search_fields = ("name", "sku", "description")
    raw_id_fields = ("org",)
    ordering = ("name",)


@admin.register(InvoiceLineItem)
class InvoiceLineItemAdmin(IssuedLineAdminMixin, admin.ModelAdmin):
    document_field = "invoice"
    list_display = (
        "invoice",
        "description",
        "product",
        "quantity",
        "unit_price",
        "discount_amount",
        "tax_amount",
        "total",
        "order",
    )
    list_filter = ("invoice__status", "created_at")
    search_fields = ("description", "invoice__invoice_number", "product__name")
    raw_id_fields = ("invoice", "product", "org")
    ordering = ("invoice", "order")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "invoice",
        "amount",
        "payment_date",
        "payment_method",
        "reference_number",
        "created_at",
    )
    list_filter = ("payment_method", "payment_date", "created_at")
    search_fields = ("invoice__invoice_number", "reference_number")
    raw_id_fields = ("invoice", "org")
    date_hierarchy = "payment_date"
    ordering = ("-payment_date",)


@admin.register(InvoiceTemplate)
class InvoiceTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "is_default",
        "primary_color",
        "org",
        "created_at",
    )
    list_filter = ("is_default", "org", "created_at")
    search_fields = ("name",)
    raw_id_fields = ("org",)
    ordering = ("name",)


# Estimate Admin
class EstimateLineItemInline(IssuedLinesInlineMixin, admin.TabularInline):
    model = EstimateLineItem
    extra = 1
    fields = (
        "product",
        "description",
        "quantity",
        "unit_price",
        "discount_type",
        "discount_value",
        "tax_rate",
        "total",
        "order",
    )
    readonly_fields = ("total",)


@admin.register(Estimate)
class EstimateAdmin(IssuedAmountsReadOnlyMixin, admin.ModelAdmin):
    amount_fields = ESTIMATE_AMOUNT_FIELDS
    list_display = (
        "estimate_number",
        "title",
        "client_name",
        "account",
        "status",
        "total_amount",
        "expiry_date",
        "created_at",
    )
    list_filter = ("status", "created_at", "expiry_date", "org")
    search_fields = (
        "estimate_number",
        "title",
        "client_name",
        "client_email",
        "account__name",
    )
    raw_id_fields = (
        "created_by",
        "org",
        "account",
        "contact",
        "opportunity",
        "converted_to_invoice",
    )
    filter_horizontal = ("assigned_to", "teams")
    inlines = [EstimateLineItemInline]
    date_hierarchy = "created_at"
    ordering = ("-created_at",)


@admin.register(EstimateLineItem)
class EstimateLineItemAdmin(IssuedLineAdminMixin, admin.ModelAdmin):
    document_field = "estimate"
    list_display = (
        "estimate",
        "description",
        "product",
        "quantity",
        "unit_price",
        "total",
        "order",
    )
    list_filter = ("estimate__status", "created_at")
    search_fields = ("description", "estimate__estimate_number", "product__name")
    raw_id_fields = ("estimate", "product", "org")
    ordering = ("estimate", "order")


# Recurring Invoice Admin
class RecurringInvoiceLineItemInline(admin.TabularInline):
    model = RecurringInvoiceLineItem
    extra = 1
    fields = (
        "product",
        "description",
        "quantity",
        "unit_price",
        "discount_type",
        "discount_value",
        "tax_rate",
        "order",
    )


@admin.register(RecurringInvoice)
class RecurringInvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "client_name",
        "account",
        "frequency",
        "next_generation_date",
        "is_active",
        "auto_send",
        "invoices_generated",
        "created_at",
    )
    list_filter = ("is_active", "frequency", "auto_send", "org", "created_at")
    search_fields = ("title", "client_name", "client_email", "account__name")
    raw_id_fields = ("created_by", "org", "account", "contact", "opportunity")
    filter_horizontal = ("assigned_to", "teams")
    inlines = [RecurringInvoiceLineItemInline]
    date_hierarchy = "created_at"
    ordering = ("-created_at",)


@admin.register(RecurringInvoiceLineItem)
class RecurringInvoiceLineItemAdmin(admin.ModelAdmin):
    list_display = (
        "recurring_invoice",
        "description",
        "product",
        "quantity",
        "unit_price",
        "order",
    )
    list_filter = ("recurring_invoice__is_active", "created_at")
    search_fields = ("description", "recurring_invoice__title", "product__name")
    raw_id_fields = ("recurring_invoice", "product", "org")
    ordering = ("recurring_invoice", "order")
