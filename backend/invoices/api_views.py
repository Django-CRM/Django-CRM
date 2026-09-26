import datetime
import json
import logging
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.custom_fields import validate_payload as validate_custom_fields_payload
from common.models import Attachments, Comment, CustomFieldDefinition
from common.money import currency_block, group_by_currency
from common.permissions import HasOrgContext, is_org_admin
from common.serializer import (
    AttachmentsSerializer,
    CommentSerializer,
    CustomFieldDefinitionSerializer,
)
from common.utils import create_attachment
from common.validators import date_param, payload_id_list, uuid_param, validate_uuid
from contacts.access import visible_contacts_qs
from invoices.models import (
    UNPAID_STATUSES,
    Estimate,
    Invoice,
    InvoiceLineItem,
    InvoiceTemplate,
    Payment,
    Product,
    RecurringInvoice,
)
from invoices.pdf import (
    generate_estimate_filename,
    generate_estimate_pdf,
    generate_invoice_filename,
    generate_invoice_pdf,
)
from invoices.permissions import (
    get_estimate_or_error,
    get_invoice_or_error,
    get_recurring_or_error,
    visible_invoices_qs,
)
from invoices.serializer import (
    EstimateCreateSerializer,
    EstimateListSerializer,
    EstimateSerializer,
    InvoiceCreateSerializer,
    InvoiceHistorySerializer,
    InvoiceLineItemCreateSerializer,
    InvoiceLineItemSerializer,
    InvoiceListSerializer,
    InvoiceSerializer,
    InvoiceTemplateCreateSerializer,
    InvoiceTemplateListSerializer,
    InvoiceTemplateSerializer,
    PaymentCreateSerializer,
    PaymentSerializer,
    ProductCreateSerializer,
    ProductSerializer,
    RecurringInvoiceCreateSerializer,
    RecurringInvoiceListSerializer,
    RecurringInvoiceSerializer,
    validate_document_account,
)
from invoices.tasks import create_invoice_history, send_email, send_invoice_to_client

logger = logging.getLogger(__name__)


# =============================================================================
# INVOICE VIEWS
# =============================================================================


def filter_invoices(queryset, params):
    """Narrow ``queryset`` by the invoice list's query string, and order it.

    The list and the CSV export both call this over `visible_invoices_qs`, so a
    downloaded file holds exactly the invoices the page would show with the
    same filters. It used to be the list view's own `filter_queryset`, beside
    an inline copy of the read rule.
    """
    # Search by invoice number or title (limit length to prevent expensive queries)
    search = params.get("search")
    if search:
        search = search[:100]  # Limit search term length
        queryset = queryset.filter(
            Q(invoice_title__icontains=search)
            | Q(invoice_number__icontains=search)
            | Q(client_name__icontains=search)
            | Q(client_email__icontains=search)
        ).distinct()

    # Filter by status
    if params.get("status"):
        queryset = queryset.filter(status=params.get("status"))

    # Filter by the related record, when one was named
    for related in ("account", "contact", "opportunity"):
        related_id = uuid_param(params, related)
        if related_id:
            queryset = queryset.filter(**{f"{related}_id": related_id})

    # Filter by assigned user
    assigned_to = uuid_param(params, "assigned_to")
    if assigned_to:
        queryset = queryset.filter(assigned_to__id=assigned_to)

    # Filter by created_by
    created_by = uuid_param(params, "created_by")
    if created_by:
        queryset = queryset.filter(created_by__id=created_by)

    # Filter by date range. Parsed first: raw text reached the date lookup
    # and a malformed day answered 500.
    for field, lookup in (
        ("issue_date_gte", "issue_date__gte"),
        ("issue_date_lte", "issue_date__lte"),
        ("due_date_gte", "due_date__gte"),
        ("due_date_lte", "due_date__lte"),
    ):
        day = date_param(params, field)
        if day:
            queryset = queryset.filter(**{lookup: day})

    # Filter by custom_fields (cf_<key>=value)
    for raw_key, raw_value in params.items():
        if raw_key.startswith("cf_") and raw_value:
            cf_key = raw_key[3:]
            if cf_key:
                queryset = queryset.filter(custom_fields__contains={cf_key: raw_value})

    # Sorting
    sort = params.get("sort", "-created_at")
    if sort.lstrip("-") in [
        "created_at",
        "due_date",
        "issue_date",
        "total_amount",
        "status",
    ]:
        queryset = queryset.order_by(sort)

    return queryset


class InvoiceListView(APIView, LimitOffsetPagination):
    """List and create invoices"""

    permission_classes = (IsAuthenticated, HasOrgContext)
    model = Invoice

    def get_queryset(self):
        """Every invoice the caller may open, before the query string."""
        return (
            visible_invoices_qs(self.request.profile, self.request.user)
            .select_related("account", "contact", "opportunity", "created_by")
            .prefetch_related("line_items", "payments", "assigned_to")
        )

    @extend_schema(tags=["Invoices"], operation_id="invoices_list")
    def get(self, request, *args, **kwargs):
        base = self.get_queryset()
        queryset = filter_invoices(base, request.query_params)
        results = self.paginate_queryset(queryset, request, view=self)
        serializer = InvoiceListSerializer(results, many=True)

        return Response(
            {
                "count": self.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": serializer.data,
                # Header figures over everything the caller can see, not just
                # this page or the current filter -- v1 summed the loaded page,
                # so the pills disagreed with the rows.
                "totals": self._totals(base),
            }
        )

    def _totals(self, queryset):
        """Aggregate the list-header figures in a single query, per currency.

        Computed over a subquery of the visible pks rather than ``queryset``
        directly: the non-admin queryset joins ``assigned_to`` (an M2M), and a
        ``Sum`` across that join multiplies an invoice's amount by its number
        of assignees. ``pk__in`` drops the join so the sums are honest.

        Money is shaped by ``currency_block``, as in the reports: amounts in
        different currencies are never added together.
        """
        scoped = Invoice.objects.filter(pk__in=queryset.values("pk"))
        today = timezone.localdate()
        unpaid = Q(status__in=UNPAID_STATUSES)
        quarter_start = datetime.date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)

        needs_action = Q(status="Draft") | (unpaid & Q(due_date__lt=today))
        groups = group_by_currency(
            scoped,
            count=Count("id"),
            outstanding=Sum("amount_due", filter=unpaid),
            overdue=Sum("amount_due", filter=unpaid & Q(due_date__lt=today)),
            due_this_month=Sum(
                "total_amount",
                filter=unpaid
                & Q(due_date__year=today.year, due_date__month=today.month),
            ),
            paid_this_quarter=Sum(
                "amount_paid", filter=Q(paid_at__date__gte=quarter_start)
            ),
            draft=Sum("total_amount", filter=Q(status="Draft")),
            # A *count* (not an amount) for the sidebar badge: invoices waiting
            # on this person -- drafts to send, plus anything overdue to chase.
            action_needed=Count("id", filter=needs_action),
        )
        return currency_block(
            groups,
            money=(
                "outstanding",
                "overdue",
                "due_this_month",
                "paid_this_quarter",
                "draft",
            ),
            counts=("count", "action_needed"),
        )

    @extend_schema(tags=["Invoices"], operation_id="invoices_create")
    def post(self, request, *args, **kwargs):
        cf_payload = request.data.get("custom_fields")
        if isinstance(cf_payload, str):
            try:
                cf_payload = json.loads(cf_payload)
            except (TypeError, ValueError):
                cf_payload = None
        cleaned_cf, cf_errors = validate_custom_fields_payload(
            "Invoice", cf_payload or {}, request.profile.org
        )
        if cf_errors:
            return Response(
                {"error": True, "errors": {"custom_fields": cf_errors}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = InvoiceCreateSerializer(
            data=request.data, request_obj=request, context={"request": request}
        )

        if serializer.is_valid():
            invoice = serializer.save(custom_fields=cleaned_cf)

            # Create history entry
            create_invoice_history.delay(
                str(invoice.id),
                str(request.profile.id),
                [],
                str(request.profile.org.id),
            )

            # Send notification email to assigned users
            assigned_to_ids = list(invoice.assigned_to.values_list("id", flat=True))
            if assigned_to_ids:
                send_email.delay(
                    str(invoice.id),
                    [str(uid) for uid in assigned_to_ids],
                    str(request.profile.org.id),
                    domain=getattr(settings, "DOMAIN_NAME", "localhost"),
                    protocol=request.scheme,
                )

            return Response(
                {
                    "error": False,
                    "message": "Invoice created successfully",
                    "invoice": InvoiceSerializer(invoice).data,
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class InvoiceDetailView(APIView):
    """Retrieve and update a single invoice"""

    permission_classes = (IsAuthenticated, HasOrgContext)
    model = Invoice

    def get_object(self, pk):
        return self.model.objects.filter(id=pk, org=self.request.profile.org).first()

    @extend_schema(tags=["Invoices"], operation_id="invoices_retrieve")
    def get(self, request, pk, format=None):
        invoice, error = get_invoice_or_error(request, pk)
        if error:
            return error

        # Get attachments and comments
        invoice_content_type = ContentType.objects.get_for_model(Invoice)
        attachments = Attachments.objects.filter(
            content_type=invoice_content_type,
            object_id=invoice.id,
            org=request.profile.org,
        ).order_by("-id")
        comments = Comment.objects.filter(
            content_type=invoice_content_type,
            object_id=invoice.id,
            org=request.profile.org,
        ).order_by("-id")

        cf_definitions = CustomFieldDefinition.objects.filter(
            org=request.profile.org,
            target_model="Invoice",
            is_active=True,
        ).order_by("display_order", "label")

        return Response(
            {
                "invoice": InvoiceSerializer(invoice).data,
                "attachments": AttachmentsSerializer(attachments, many=True).data,
                "comments": CommentSerializer(comments, many=True).data,
                "history": InvoiceHistorySerializer(
                    invoice.invoice_history.all(), many=True
                ).data,
                "custom_field_definitions": CustomFieldDefinitionSerializer(
                    cf_definitions, many=True
                ).data,
            }
        )

    @extend_schema(tags=["Invoices"], operation_id="invoices_update")
    def put(self, request, pk, format=None):
        invoice, error = get_invoice_or_error(request, pk)
        if error:
            return error

        save_kwargs = {}
        if "custom_fields" in request.data:
            cf_payload = request.data.get("custom_fields")
            if isinstance(cf_payload, str):
                try:
                    cf_payload = json.loads(cf_payload)
                except (TypeError, ValueError):
                    cf_payload = None
            cleaned_cf, cf_errors = validate_custom_fields_payload(
                "Invoice",
                cf_payload or {},
                request.profile.org,
                existing=invoice.custom_fields or {},
            )
            if cf_errors:
                return Response(
                    {"error": True, "errors": {"custom_fields": cf_errors}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            save_kwargs["custom_fields"] = cleaned_cf

        serializer = InvoiceCreateSerializer(
            invoice,
            data=request.data,
            request_obj=request,
            context={"request": request},
            partial=True,
        )

        if serializer.is_valid():
            invoice = serializer.save(**save_kwargs)

            # Create history entry
            create_invoice_history.delay(
                str(invoice.id),
                str(request.profile.id),
                [],
                str(request.profile.org.id),
            )

            return Response(
                {
                    "error": False,
                    "message": "Invoice updated successfully",
                    "invoice": InvoiceSerializer(invoice).data,
                }
            )

        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class InvoiceSendView(APIView):
    """Send invoice to client via email"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Invoices"], operation_id="invoices_send")
    def post(self, request, pk):
        invoice, error = get_invoice_or_error(request, pk)
        if error:
            return error

        # A settled invoice must not be re-sent: doing so would overwrite
        # sent_at/is_email_sent and mail the client about a closed invoice.
        if invoice.status in ("Paid", "Cancelled"):
            return Response(
                {
                    "error": True,
                    "message": f"Cannot send a {invoice.status.lower()} invoice",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Update status and sent_at
        if invoice.status == "Draft":
            invoice.status = "Sent"
        invoice.sent_at = timezone.now()
        invoice.is_email_sent = True
        invoice.save()

        # Send invoice email to client
        send_invoice_to_client.delay(
            str(invoice.id),
            str(request.profile.org.id),
            domain=getattr(settings, "DOMAIN_NAME", "localhost"),
            protocol=request.scheme,
        )

        return Response({"error": False, "message": "Invoice sent successfully"})


class InvoiceMarkPaidView(APIView):
    """Mark invoice as paid"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Invoices"], operation_id="invoices_mark_paid")
    def post(self, request, pk):
        invoice, error = get_invoice_or_error(request, pk)
        if error:
            return error

        # Fall back to settling the outstanding balance, but route everything
        # through the serializer so the amount is validated either way.
        payload = {
            "amount": request.data.get("amount", invoice.amount_due),
            "payment_method": request.data.get("payment_method", "OTHER"),
            "payment_date": request.data.get("payment_date", timezone.localdate()),
            "reference_number": request.data.get("reference_number", ""),
            "notes": request.data.get("notes", ""),
        }
        serializer = PaymentCreateSerializer(data=payload, context={"invoice": invoice})
        if not serializer.is_valid():
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer.save(invoice=invoice, org=request.profile.org)

        # Refresh invoice from DB to get updated payment totals
        invoice.refresh_from_db()

        return Response(
            {
                "error": False,
                "message": "Payment recorded successfully",
                "invoice": InvoiceSerializer(invoice).data,
            }
        )


class InvoiceDuplicateView(APIView):
    """Duplicate an existing invoice"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Invoices"], operation_id="invoices_duplicate")
    def post(self, request, pk):
        original, error = get_invoice_or_error(request, pk)
        if error:
            return error

        with transaction.atomic():
            # Create duplicate invoice
            new_invoice = Invoice.objects.create(
                invoice_title=f"Copy of {original.invoice_title}",
                status="Draft",
                account=original.account,
                contact=original.contact,
                opportunity=original.opportunity,
                client_name=original.client_name,
                client_email=original.client_email,
                client_phone=original.client_phone,
                billing_address_line=original.billing_address_line,
                billing_city=original.billing_city,
                billing_state=original.billing_state,
                billing_postcode=original.billing_postcode,
                billing_country=original.billing_country,
                client_address_line=original.client_address_line,
                client_city=original.client_city,
                client_state=original.client_state,
                client_postcode=original.client_postcode,
                client_country=original.client_country,
                discount_type=original.discount_type,
                discount_value=original.discount_value,
                tax_rate=original.tax_rate,
                shipping_amount=original.shipping_amount,
                currency=original.currency,
                issue_date=timezone.localdate(),
                payment_terms=original.payment_terms,
                reminder_enabled=original.reminder_enabled,
                reminder_days_before=original.reminder_days_before,
                reminder_days_after=original.reminder_days_after,
                reminder_frequency=original.reminder_frequency,
                template=original.template,
                notes=original.notes,
                terms=original.terms,
                org=request.profile.org,
            )

            # Copy line items
            for item in original.line_items.all():
                InvoiceLineItem.objects.create(
                    invoice=new_invoice,
                    product=item.product,
                    name=item.name,
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    discount_type=item.discount_type,
                    discount_value=item.discount_value,
                    tax_rate=item.tax_rate,
                    order=item.order,
                    org=request.profile.org,
                )

            # Recalculate totals
            new_invoice.recalculate_totals()
            new_invoice.save()

            return Response(
                {
                    "error": False,
                    "message": "Invoice duplicated successfully",
                    "invoice": InvoiceSerializer(new_invoice).data,
                },
                status=status.HTTP_201_CREATED,
            )


class InvoiceCancelView(APIView):
    """Cancel an invoice"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Invoices"], operation_id="invoices_cancel")
    def post(self, request, pk):
        invoice, error = get_invoice_or_error(request, pk)
        if error:
            return error

        # Cannot cancel already cancelled invoices
        if invoice.status == "Cancelled":
            return Response(
                {"error": True, "message": "Invoice is already cancelled"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Cannot cancel paid invoices
        if invoice.status == "Paid":
            return Response(
                {"error": True, "message": "Cannot cancel a paid invoice"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Cancel the invoice
        invoice.status = "Cancelled"
        invoice.cancelled_at = timezone.now()
        invoice.save(update_fields=["status", "cancelled_at"])

        return Response(
            {
                "error": False,
                "message": "Invoice cancelled successfully",
                "invoice": InvoiceSerializer(invoice).data,
            }
        )


class InvoicePDFView(APIView):
    """Generate and download invoice PDF"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Invoices"], operation_id="invoices_pdf")
    def get(self, request, pk):
        invoice, error = get_invoice_or_error(request, pk)
        if error:
            return error

        try:
            pdf_content = generate_invoice_pdf(invoice)
            filename = generate_invoice_filename(invoice)

            response = HttpResponse(pdf_content, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
        except ImportError:
            logger.exception("PDF generation library not available")
            return Response(
                {"error": True, "message": "PDF generation unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception:
            logger.exception("Failed to generate invoice PDF")
            return Response(
                {"error": True, "message": "Failed to generate PDF"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# LINE ITEM VIEWS
# =============================================================================


class InvoiceLineItemListView(APIView):
    """Manage line items for an invoice"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Invoice Line Items"], operation_id="line_items_list")
    def get(self, request, invoice_id):
        invoice, error = get_invoice_or_error(request, invoice_id)
        if error:
            return error

        line_items = invoice.line_items.all().order_by("order")
        return Response(InvoiceLineItemSerializer(line_items, many=True).data)

    @extend_schema(tags=["Invoice Line Items"], operation_id="line_items_create")
    def post(self, request, invoice_id):
        invoice, error = get_invoice_or_error(request, invoice_id)
        if error:
            return error

        serializer = InvoiceLineItemCreateSerializer(
            data=request.data, request_obj=request
        )
        if serializer.is_valid():
            line_item = serializer.save(invoice=invoice, org=request.profile.org)

            # Recalculate invoice totals
            invoice.recalculate_totals()
            invoice.save()

            return Response(
                {
                    "error": False,
                    "message": "Line item added",
                    "line_item": InvoiceLineItemSerializer(line_item).data,
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class InvoiceLineItemDetailView(APIView):
    """Update or delete a single line item"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, invoice_id, pk):
        return InvoiceLineItem.objects.filter(
            id=pk, invoice_id=invoice_id, org=self.request.profile.org
        ).first()

    @extend_schema(tags=["Invoice Line Items"], operation_id="line_items_update")
    def put(self, request, invoice_id, pk):
        _, error = get_invoice_or_error(request, invoice_id)
        if error:
            return error

        line_item = self.get_object(invoice_id, pk)
        if not line_item:
            return Response(
                {"error": True, "message": "Line item not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = InvoiceLineItemCreateSerializer(
            line_item, data=request.data, partial=True, request_obj=request
        )
        if serializer.is_valid():
            line_item = serializer.save()

            # Recalculate invoice totals
            line_item.invoice.recalculate_totals()
            line_item.invoice.save()

            return Response(
                {
                    "error": False,
                    "message": "Line item updated",
                    "line_item": InvoiceLineItemSerializer(line_item).data,
                }
            )

        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(tags=["Invoice Line Items"], operation_id="line_items_destroy")
    def delete(self, request, invoice_id, pk):
        _, error = get_invoice_or_error(request, invoice_id)
        if error:
            return error

        line_item = self.get_object(invoice_id, pk)
        if not line_item:
            return Response(
                {"error": True, "message": "Line item not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        invoice = line_item.invoice
        line_item.delete()

        # Recalculate invoice totals
        invoice.recalculate_totals()
        invoice.save()

        return Response(
            {"error": False, "message": "Line item deleted"},
            status=status.HTTP_200_OK,
        )


# =============================================================================
# PAYMENT VIEWS
# =============================================================================


class PaymentListView(APIView, LimitOffsetPagination):
    """List and create payments for an invoice"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Payments"], operation_id="payments_list")
    def get(self, request, invoice_id):
        invoice, error = get_invoice_or_error(request, invoice_id)
        if error:
            return error

        payments = invoice.payments.all().order_by("-payment_date")
        return Response(PaymentSerializer(payments, many=True).data)

    @extend_schema(tags=["Payments"], operation_id="payments_create")
    def post(self, request, invoice_id):
        invoice, error = get_invoice_or_error(request, invoice_id)
        if error:
            return error

        serializer = PaymentCreateSerializer(
            data=request.data, context={"invoice": invoice}
        )
        if serializer.is_valid():
            payment = serializer.save(invoice=invoice, org=request.profile.org)
            return Response(
                {
                    "error": False,
                    "message": "Payment recorded",
                    "payment": PaymentSerializer(payment).data,
                    "invoice": InvoiceListSerializer(invoice).data,
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class PaymentDetailView(APIView):
    """Delete a payment"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Payments"], operation_id="payments_destroy")
    def delete(self, request, invoice_id, pk):
        _, error = get_invoice_or_error(request, invoice_id)
        if error:
            return error

        payment = (
            Payment.objects.filter(
                id=pk, invoice_id=invoice_id, org=request.profile.org
            )
            .select_related("invoice")
            .first()
        )
        if not payment:
            return Response(
                {"error": True, "message": "Payment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        payment.delete()
        return Response(
            {"error": False, "message": "Payment deleted"},
            status=status.HTTP_200_OK,
        )


# =============================================================================
# PRODUCT VIEWS
# =============================================================================


class ProductListView(APIView, LimitOffsetPagination):
    """List and create products"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Products"], operation_id="products_list")
    def get(self, request):
        queryset = Product.objects.filter(org=request.profile.org)

        # Filter by active status
        if request.query_params.get("is_active"):
            is_active = request.query_params.get("is_active").lower() == "true"
            queryset = queryset.filter(is_active=is_active)

        # Search
        search = request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(sku__icontains=search)
                | Q(description__icontains=search)
            )

        # Category filter
        if request.query_params.get("category"):
            queryset = queryset.filter(category=request.query_params.get("category"))

        # `used_on`: how many distinct invoices each product is a line item on.
        # Annotated once here (the hot path) instead of per row so the list does
        # not fan out into N count queries; `ProductSerializer.get_used_on` reads
        # this attribute and falls back to its own query on the detail views.
        queryset = queryset.annotate(
            used_on_count=Count("invoice_line_items__invoice", distinct=True)
        ).order_by("name")

        results = self.paginate_queryset(queryset, request, view=self)
        return Response(
            {
                "count": self.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": ProductSerializer(results, many=True).data,
            }
        )

    @extend_schema(tags=["Products"], operation_id="products_create")
    def post(self, request):
        # The catalogue is org-wide config: its list prices flow onto every
        # rep's invoices and estimates. Reading it is open to any member (they
        # need it to build line items), but changing it is an admin act, the
        # same posture Organization and Team settings take. A non-admin who
        # curls this endpoint is refused here, not just hidden from in the UI.
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {
                    "error": True,
                    "message": "Only an administrator can change the product catalog.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ProductCreateSerializer(
            data=request.data, context={"request": request}
        )
        if serializer.is_valid():
            product = serializer.save()
            return Response(
                {
                    "error": False,
                    "message": "Product created",
                    "product": ProductSerializer(product).data,
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class ProductDetailView(APIView):
    """Retrieve, update, and delete a product"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk):
        return Product.objects.filter(id=pk, org=self.request.profile.org).first()

    @extend_schema(tags=["Products"], operation_id="products_retrieve")
    def get(self, request, pk):
        product = self.get_object(pk)
        if not product:
            return Response(
                {"error": True, "message": "Product not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(ProductSerializer(product).data)

    def _require_admin(self, request):
        """Editing the shared catalog is admin-only; see ProductListView.post."""
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {
                    "error": True,
                    "message": "Only an administrator can change the product catalog.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    @extend_schema(tags=["Products"], operation_id="products_update")
    def put(self, request, pk):
        product = self.get_object(pk)
        if not product:
            return Response(
                {"error": True, "message": "Product not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        denied = self._require_admin(request)
        if denied:
            return denied

        serializer = ProductCreateSerializer(
            product, data=request.data, partial=True, context={"request": request}
        )
        if serializer.is_valid():
            product = serializer.save()
            return Response(
                {
                    "error": False,
                    "message": "Product updated",
                    "product": ProductSerializer(product).data,
                }
            )

        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(tags=["Products"], operation_id="products_destroy")
    def delete(self, request, pk):
        product = self.get_object(pk)
        if not product:
            return Response(
                {"error": True, "message": "Product not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        denied = self._require_admin(request)
        if denied:
            return denied

        product.delete()
        return Response(
            {"error": False, "message": "Product deleted"},
            status=status.HTTP_200_OK,
        )


# =============================================================================
# ESTIMATE VIEWS
# =============================================================================


class EstimateListView(APIView, LimitOffsetPagination):
    """List and create estimates"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_queryset(self):
        org = self.request.profile.org

        queryset = Estimate.objects.filter(org=org).select_related(
            "account", "contact", "opportunity", "created_by", "converted_to_invoice"
        )

        if (
            not is_org_admin(self.request.profile)
            and not self.request.user.is_superuser
        ):
            # created_by is a User FK; comparing it to a Profile does not just
            # silently fail here, it reaches the DB as Q(created_by=<Profile>)
            # and raised ValueError -- a 500 on every non-admin estimate list.
            queryset = queryset.filter(
                Q(created_by=self.request.profile.user)
                | Q(assigned_to=self.request.profile)
            ).distinct()

        return queryset

    @extend_schema(tags=["Estimates"], operation_id="estimates_list")
    def get(self, request):
        queryset = self.get_queryset()

        # Apply filters
        params = request.query_params
        if params.get("status"):
            queryset = queryset.filter(status=params.get("status"))
        if params.get("account"):
            queryset = queryset.filter(account_id=params.get("account"))
        if params.get("search"):
            search = params.get("search")
            queryset = queryset.filter(
                Q(estimate_number__icontains=search)
                | Q(title__icontains=search)
                | Q(client_name__icontains=search)
            )

        # Filter by custom_fields (cf_<key>=value)
        for raw_key, raw_value in params.items():
            if raw_key.startswith("cf_") and raw_value:
                cf_key = raw_key[3:]
                if cf_key:
                    queryset = queryset.filter(
                        custom_fields__contains={cf_key: raw_value}
                    )

        results = self.paginate_queryset(
            queryset.order_by("-created_at"), request, view=self
        )
        return Response(
            {
                "count": self.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": EstimateListSerializer(results, many=True).data,
            }
        )

    @extend_schema(tags=["Estimates"], operation_id="estimates_create")
    def post(self, request):
        cf_payload = request.data.get("custom_fields")
        if isinstance(cf_payload, str):
            try:
                cf_payload = json.loads(cf_payload)
            except (TypeError, ValueError):
                cf_payload = None
        cleaned_cf, cf_errors = validate_custom_fields_payload(
            "Estimate", cf_payload or {}, request.profile.org
        )
        if cf_errors:
            return Response(
                {"error": True, "errors": {"custom_fields": cf_errors}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = EstimateCreateSerializer(
            data=request.data, request_obj=request, context={"request": request}
        )
        if serializer.is_valid():
            estimate = serializer.save(custom_fields=cleaned_cf)
            return Response(
                {
                    "error": False,
                    "message": "Estimate created",
                    "estimate": EstimateSerializer(estimate).data,
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class EstimateDetailView(APIView):
    """Retrieve, update, and delete an estimate"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Estimates"], operation_id="estimates_retrieve")
    def get(self, request, pk):
        estimate, error = get_estimate_or_error(request, pk)
        if error:
            return error
        cf_definitions = CustomFieldDefinition.objects.filter(
            org=request.profile.org,
            target_model="Estimate",
            is_active=True,
        ).order_by("display_order", "label")
        return Response(
            {
                "estimate": EstimateSerializer(estimate).data,
                "custom_field_definitions": CustomFieldDefinitionSerializer(
                    cf_definitions, many=True
                ).data,
            }
        )

    @extend_schema(tags=["Estimates"], operation_id="estimates_update")
    def put(self, request, pk):
        estimate, error = get_estimate_or_error(request, pk)
        if error:
            return error

        save_kwargs = {}
        if "custom_fields" in request.data:
            cf_payload = request.data.get("custom_fields")
            if isinstance(cf_payload, str):
                try:
                    cf_payload = json.loads(cf_payload)
                except (TypeError, ValueError):
                    cf_payload = None
            cleaned_cf, cf_errors = validate_custom_fields_payload(
                "Estimate",
                cf_payload or {},
                request.profile.org,
                existing=estimate.custom_fields or {},
            )
            if cf_errors:
                return Response(
                    {"error": True, "errors": {"custom_fields": cf_errors}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            save_kwargs["custom_fields"] = cleaned_cf

        serializer = EstimateCreateSerializer(
            estimate,
            data=request.data,
            request_obj=request,
            context={"request": request},
            partial=True,
        )
        if serializer.is_valid():
            estimate = serializer.save(**save_kwargs)
            return Response(
                {
                    "error": False,
                    "message": "Estimate updated",
                    "estimate": EstimateSerializer(estimate).data,
                }
            )

        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(tags=["Estimates"], operation_id="estimates_destroy")
    def delete(self, request, pk):
        estimate, error = get_estimate_or_error(request, pk)
        if error:
            return error

        estimate.delete()
        return Response(
            {"error": False, "message": "Estimate deleted"},
            status=status.HTTP_200_OK,
        )


class EstimateConvertView(APIView):
    """Convert an estimate to an invoice"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Estimates"], operation_id="estimates_convert")
    def post(self, request, pk):
        estimate, error = get_estimate_or_error(request, pk)
        if error:
            return error

        if estimate.converted_to_invoice:
            return Response(
                {"error": True, "message": "Estimate already converted to invoice"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # Create invoice from estimate
            invoice = Invoice.objects.create(
                invoice_title=estimate.title,
                status="Draft",
                account=estimate.account,
                contact=estimate.contact,
                opportunity=estimate.opportunity,
                client_name=estimate.client_name,
                client_email=estimate.client_email,
                client_phone=estimate.client_phone,
                client_address_line=estimate.client_address_line,
                client_city=estimate.client_city,
                client_state=estimate.client_state,
                client_postcode=estimate.client_postcode,
                client_country=estimate.client_country,
                subtotal=estimate.subtotal,
                discount_type=estimate.discount_type,
                discount_value=estimate.discount_value,
                tax_rate=estimate.tax_rate,
                currency=estimate.currency,
                issue_date=timezone.localdate(),
                notes=estimate.notes,
                terms=estimate.terms,
                org=request.profile.org,
            )

            # Copy line items
            for item in estimate.line_items.all():
                InvoiceLineItem.objects.create(
                    invoice=invoice,
                    product=item.product,
                    name=item.name,
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    discount_type=item.discount_type,
                    discount_value=item.discount_value,
                    tax_rate=item.tax_rate,
                    order=item.order,
                    org=request.profile.org,
                )

            # Recalculate totals
            invoice.recalculate_totals()
            invoice.save()

            # Update estimate
            estimate.status = "Accepted"
            estimate.accepted_at = timezone.now()
            estimate.converted_to_invoice = invoice
            estimate.save()

        return Response(
            {
                "error": False,
                "message": "Estimate converted to invoice",
                "invoice": InvoiceSerializer(invoice).data,
            },
            status=status.HTTP_201_CREATED,
        )


class EstimateSendView(APIView):
    """Send estimate to client via email"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Estimates"], operation_id="estimates_send")
    def post(self, request, pk):
        estimate, error = get_estimate_or_error(request, pk)
        if error:
            return error

        # A settled quote must not be re-sent, the same rule InvoiceSendView
        # applies for the same reason: sending overwrites sent_at and mails the
        # client about a quote that is closed. Accepted belongs here too, since
        # an agreed quote should be converted rather than re-quoted.
        if estimate.status in ("Accepted", "Declined", "Expired"):
            return Response(
                {
                    "error": True,
                    "message": f"Cannot send a {estimate.status.lower()} estimate",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Checked separately from the status above, not folded into it.
        # `check_expired_estimates` flips Sent/Viewed to Expired on a daily
        # schedule, so between the expiry date passing and that task running an
        # estimate reads as "Sent" while `is_expired` is already true. Testing
        # only the status would send a quote at a price that has lapsed on any
        # day the task has not run yet. `PublicEstimateAcceptView` asks the
        # same question in the same order for the same reason.
        if estimate.is_expired:
            return Response(
                {
                    "error": True,
                    "message": (
                        f"This estimate expired on {estimate.expiry_date}. "
                        "Update the expiry date or raise a new quote before "
                        "sending it."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Update status and sent_at
        if estimate.status == "Draft":
            estimate.status = "Sent"
        estimate.sent_at = timezone.now()
        estimate.save()

        # Trigger async email task
        from invoices.tasks import send_estimate_to_client

        send_estimate_to_client.delay(str(estimate.id), str(request.profile.org.id))

        return Response({"error": False, "message": "Estimate sent successfully"})


class EstimatePDFView(APIView):
    """Generate and download estimate PDF"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Estimates"], operation_id="estimates_pdf")
    def get(self, request, pk):
        # The old inline check compared request.profile (a Profile) to
        # estimate.created_by (a User), which is always unequal -- so the
        # estimate's creator was denied their own PDF while assignees got it.
        # get_estimate_or_error compares created_by against request.user.
        estimate, error = get_estimate_or_error(request, pk)
        if error:
            return error

        try:
            pdf_content = generate_estimate_pdf(estimate)
            filename = generate_estimate_filename(estimate)

            response = HttpResponse(pdf_content, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
        except ImportError:
            logger.exception("PDF generation library not available")
            return Response(
                {"error": True, "message": "PDF generation unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception:
            logger.exception("Failed to generate estimate PDF")
            return Response(
                {"error": True, "message": "Failed to generate PDF"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# RECURRING INVOICE VIEWS
# =============================================================================


class RecurringInvoiceListView(APIView, LimitOffsetPagination):
    """List and create recurring invoices"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Recurring Invoices"], operation_id="recurring_list")
    def get(self, request):
        queryset = (
            RecurringInvoice.objects.filter(org=request.profile.org)
            .select_related("account", "contact")
            .order_by("-created_at")
        )

        # Non-admins see only schedules they created or are assigned to -- the
        # same scoping as the invoice and estimate lists. created_by is a User
        # FK, so it is matched against request.profile.user; assigned_to holds
        # Profiles, matched against request.profile.
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            queryset = queryset.filter(
                Q(created_by=request.profile.user) | Q(assigned_to=request.profile)
            ).distinct()

        # Apply filters
        params = request.query_params
        if params.get("is_active"):
            is_active = params.get("is_active").lower() == "true"
            queryset = queryset.filter(is_active=is_active)

        # Filter by custom_fields (cf_<key>=value)
        for raw_key, raw_value in params.items():
            if raw_key.startswith("cf_") and raw_value:
                cf_key = raw_key[3:]
                if cf_key:
                    queryset = queryset.filter(
                        custom_fields__contains={cf_key: raw_value}
                    )

        results = self.paginate_queryset(queryset, request, view=self)
        return Response(
            {
                "count": self.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": RecurringInvoiceListSerializer(results, many=True).data,
            }
        )

    @extend_schema(tags=["Recurring Invoices"], operation_id="recurring_create")
    def post(self, request):
        cf_payload = request.data.get("custom_fields")
        if isinstance(cf_payload, str):
            try:
                cf_payload = json.loads(cf_payload)
            except (TypeError, ValueError):
                cf_payload = None
        cleaned_cf, cf_errors = validate_custom_fields_payload(
            "RecurringInvoice", cf_payload or {}, request.profile.org
        )
        if cf_errors:
            return Response(
                {"error": True, "errors": {"custom_fields": cf_errors}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = RecurringInvoiceCreateSerializer(
            data=request.data, request_obj=request, context={"request": request}
        )
        if serializer.is_valid():
            recurring = serializer.save(custom_fields=cleaned_cf)
            return Response(
                {
                    "error": False,
                    "message": "Recurring invoice created",
                    "recurring_invoice": RecurringInvoiceSerializer(recurring).data,
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class RecurringInvoiceDetailView(APIView):
    """Retrieve, update, and delete a recurring invoice"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Recurring Invoices"], operation_id="recurring_retrieve")
    def get(self, request, pk):
        recurring, error = get_recurring_or_error(request, pk)
        if error:
            return error
        cf_definitions = CustomFieldDefinition.objects.filter(
            org=request.profile.org,
            target_model="RecurringInvoice",
            is_active=True,
        ).order_by("display_order", "label")
        return Response(
            {
                "recurring_invoice": RecurringInvoiceSerializer(recurring).data,
                "custom_field_definitions": CustomFieldDefinitionSerializer(
                    cf_definitions, many=True
                ).data,
            }
        )

    @extend_schema(tags=["Recurring Invoices"], operation_id="recurring_update")
    def put(self, request, pk):
        recurring, error = get_recurring_or_error(request, pk)
        if error:
            return error

        save_kwargs = {}
        if "custom_fields" in request.data:
            cf_payload = request.data.get("custom_fields")
            if isinstance(cf_payload, str):
                try:
                    cf_payload = json.loads(cf_payload)
                except (TypeError, ValueError):
                    cf_payload = None
            cleaned_cf, cf_errors = validate_custom_fields_payload(
                "RecurringInvoice",
                cf_payload or {},
                request.profile.org,
                existing=recurring.custom_fields or {},
            )
            if cf_errors:
                return Response(
                    {"error": True, "errors": {"custom_fields": cf_errors}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            save_kwargs["custom_fields"] = cleaned_cf

        serializer = RecurringInvoiceCreateSerializer(
            recurring,
            data=request.data,
            request_obj=request,
            context={"request": request},
            partial=True,
        )
        if serializer.is_valid():
            recurring = serializer.save(**save_kwargs)
            return Response(
                {
                    "error": False,
                    "message": "Recurring invoice updated",
                    "recurring_invoice": RecurringInvoiceSerializer(recurring).data,
                }
            )

        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(tags=["Recurring Invoices"], operation_id="recurring_destroy")
    def delete(self, request, pk):
        recurring, error = get_recurring_or_error(request, pk)
        if error:
            return error

        recurring.delete()
        return Response(
            {"error": False, "message": "Recurring invoice deleted"},
            status=status.HTTP_200_OK,
        )


class RecurringInvoicePauseView(APIView):
    """Pause or resume a recurring invoice"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Recurring Invoices"], operation_id="recurring_toggle")
    def post(self, request, pk):
        recurring, error = get_recurring_or_error(request, pk)
        if error:
            return error

        recurring.is_active = not recurring.is_active
        recurring.save()

        action = "resumed" if recurring.is_active else "paused"
        return Response(
            {
                "error": False,
                "message": f"Recurring invoice {action}",
                "recurring_invoice": RecurringInvoiceListSerializer(recurring).data,
            }
        )


# =============================================================================
# INVOICE TEMPLATE VIEWS
# =============================================================================


def _forbid_non_admin_template(
    request, message="Only an administrator can change invoice templates."
):
    """Return a 403 Response for non-admins, or None to allow.

    Invoice templates are org-wide shared config: every member reads the
    catalogue (to see how their invoices look), but changing a template: its
    colours, its default flag, its raw HTML/CSS, affects everyone in the org
    and drives the server-side PDF render. So writes (create/update/delete) are
    admin-only, while the catalogue read stays open. Role is derived from
    ``request.profile``, never the request body.

    ``message`` is a parameter because one read is admin-only too: fetching a
    template's raw markup for the editor. That is the same rule for the same
    reason, so it is the same gate rather than a second copy of the role check
    that could drift away from this one.
    """
    if not is_org_admin(request.profile) and not request.user.is_superuser:
        return Response(
            {"error": True, "message": message},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


class InvoiceTemplateListView(APIView, LimitOffsetPagination):
    """List and create invoice templates"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Invoice Templates"], operation_id="templates_list")
    def get(self, request):
        queryset = (
            InvoiceTemplate.objects.filter(org=request.profile.org)
            .annotate(used_on_invoices_count=Count("invoices"))
            .order_by("-is_default", "name")
        )

        results = self.paginate_queryset(queryset, request, view=self)
        return Response(
            {
                "count": self.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": InvoiceTemplateListSerializer(results, many=True).data,
            }
        )

    @extend_schema(tags=["Invoice Templates"], operation_id="templates_create")
    def post(self, request):
        denied = _forbid_non_admin_template(request)
        if denied:
            return denied

        serializer = InvoiceTemplateCreateSerializer(
            data=request.data, context={"request": request}
        )
        if serializer.is_valid():
            template = serializer.save()
            return Response(
                {
                    "error": False,
                    "message": "Template created",
                    "template": InvoiceTemplateListSerializer(template).data,
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class InvoiceTemplateDetailView(APIView):
    """Retrieve, update, and delete an invoice template"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk):
        return InvoiceTemplate.objects.filter(
            id=pk, org=self.request.profile.org
        ).first()

    @extend_schema(tags=["Invoice Templates"], operation_id="templates_retrieve")
    def get(self, request, pk):
        template = self.get_object(pk)
        if not template:
            return Response(
                {"error": True, "message": "Template not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        # Safe serializer only, never returns the raw template_html/css.
        return Response(InvoiceTemplateListSerializer(template).data)

    @extend_schema(tags=["Invoice Templates"], operation_id="templates_update")
    def put(self, request, pk):
        denied = _forbid_non_admin_template(request)
        if denied:
            return denied

        template = self.get_object(pk)
        if not template:
            return Response(
                {"error": True, "message": "Template not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = InvoiceTemplateCreateSerializer(
            template, data=request.data, partial=True, context={"request": request}
        )
        if serializer.is_valid():
            template = serializer.save()
            return Response(
                {
                    "error": False,
                    "message": "Template updated",
                    "template": InvoiceTemplateListSerializer(template).data,
                }
            )

        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @extend_schema(tags=["Invoice Templates"], operation_id="templates_destroy")
    def delete(self, request, pk):
        denied = _forbid_non_admin_template(request)
        if denied:
            return denied

        template = self.get_object(pk)
        if not template:
            return Response(
                {"error": True, "message": "Template not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        template.delete()
        return Response(
            {"error": False, "message": "Template deleted"},
            status=status.HTTP_200_OK,
        )


class InvoiceTemplateEditorView(APIView):
    """The only endpoint that returns a template's raw ``template_html``/``css``.

    Every other read path uses ``InvoiceTemplateListSerializer``, which reports
    booleans and a byte count in place of the markup, because that markup is
    org-authored HTML/CSS that WeasyPrint renders into a PDF server-side: the
    moment either field lands in a browser DOM, a PDF setting becomes stored
    XSS.

    An editor still has to load what it is editing, and it could not: the edit
    form had no way to pre-fill the two fields it exists to change, so opening
    it and saving silently blanked them. Serving the markup from its own
    admin-only route keeps that fix out of the detail response every member
    reads. A client must bind these two fields to a ``<textarea>`` value and
    never to ``{@html}``.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Invoice Templates"], operation_id="templates_editor_retrieve")
    def get(self, request, pk):
        # Role first, so the 403 does not depend on whether the id exists.
        denied = _forbid_non_admin_template(
            request,
            "Only an administrator can view invoice template markup.",
        )
        if denied:
            return denied

        template = InvoiceTemplate.objects.filter(
            id=pk, org=request.profile.org
        ).first()
        if not template:
            return Response(
                {"error": True, "message": "Template not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(InvoiceTemplateSerializer(template).data)


# =============================================================================
# COMMENTS AND ATTACHMENTS
# =============================================================================


class InvoiceCommentView(APIView):
    """Manage comments on an invoice"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Invoice Comments"], operation_id="comments_create")
    def post(self, request, invoice_id):
        invoice, error = get_invoice_or_error(request, invoice_id)
        if error:
            return error

        comment_text = request.data.get("comment")
        if not comment_text:
            return Response(
                {"error": True, "message": "Comment text required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        invoice_content_type = ContentType.objects.get_for_model(Invoice)
        comment = Comment.objects.create(
            content_type=invoice_content_type,
            object_id=invoice.id,
            comment=comment_text,
            commented_by=request.profile,
            org=request.profile.org,
        )

        return Response(
            {
                "error": False,
                "message": "Comment added",
                "comment": CommentSerializer(comment).data,
            },
            status=status.HTTP_201_CREATED,
        )


class InvoiceCommentDetailView(APIView):
    """Update or delete a comment"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk):
        return Comment.objects.filter(id=pk, org=self.request.profile.org).first()

    @extend_schema(tags=["Invoice Comments"], operation_id="comments_update")
    def put(self, request, pk):
        comment = self.get_object(pk)
        if not comment:
            return Response(
                {"error": True, "message": "Comment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Only comment author or admin can edit
        if comment.commented_by != request.profile and not is_org_admin(
            request.profile
        ):
            return Response(
                {"error": True, "message": "Permission denied"},
                status=status.HTTP_403_FORBIDDEN,
            )

        comment.comment = request.data.get("comment", comment.comment)
        comment.save()

        return Response(
            {
                "error": False,
                "message": "Comment updated",
                "comment": CommentSerializer(comment).data,
            }
        )

    @extend_schema(tags=["Invoice Comments"], operation_id="comments_destroy")
    def delete(self, request, pk):
        comment = self.get_object(pk)
        if not comment:
            return Response(
                {"error": True, "message": "Comment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if comment.commented_by != request.profile and not is_org_admin(
            request.profile
        ):
            return Response(
                {"error": True, "message": "Permission denied"},
                status=status.HTTP_403_FORBIDDEN,
            )

        comment.delete()
        return Response(
            {"error": False, "message": "Comment deleted"},
            status=status.HTTP_200_OK,
        )


class InvoiceAttachmentView(APIView):
    """Manage attachments on an invoice"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Invoice Attachments"], operation_id="attachments_create")
    def post(self, request, invoice_id):
        invoice, error = get_invoice_or_error(request, invoice_id)
        if error:
            return error

        if not request.FILES.get("file"):
            return Response(
                {"error": True, "message": "File required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        attachment = create_attachment(
            request.FILES.get("file"), invoice, request.profile
        )

        return Response(
            {
                "error": False,
                "message": "Attachment uploaded",
                "attachment": AttachmentsSerializer(attachment).data,
            },
            status=status.HTTP_201_CREATED,
        )


class InvoiceAttachmentDetailView(APIView):
    """Delete an attachment"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Invoice Attachments"], operation_id="attachments_destroy")
    def delete(self, request, pk):
        attachment = Attachments.objects.filter(id=pk, org=request.profile.org).first()
        if not attachment:
            return Response(
                {"error": True, "message": "Attachment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # created_by is a User FK, so it must be compared against request.user --
        # comparing it to request.profile is always unequal and locks the
        # uploader out of their own attachment.
        if attachment.created_by_id != request.user.id and not is_org_admin(
            request.profile
        ):
            return Response(
                {"error": True, "message": "Permission denied"},
                status=status.HTTP_403_FORBIDDEN,
            )

        attachment.delete()
        return Response(
            {"error": False, "message": "Attachment deleted"},
            status=status.HTTP_200_OK,
        )


# =============================================================================
# REPORTS AND DASHBOARD VIEWS
# =============================================================================


def _forbid_non_admin_reports(request):
    """Return a 403 Response for non-admins, or None to allow.

    The invoice reports are org-wide financial roll-ups -- total invoiced and
    collected, revenue by month, AR aging, who owes what across every account.
    That is management data. The invoice and estimate *lists* scope a non-admin
    to their own and assigned records; a report that ignored that scoping and
    showed the whole org's revenue and receivables to any member would be the
    leak those scopes exist to prevent. So the three report endpoints are
    admin-only (Django superusers included), the same bar as the org and team
    settings.
    """
    if not is_org_admin(request.profile) and not request.user.is_superuser:
        return Response(
            {
                "error": True,
                "message": "Only an administrator can view invoice reports.",
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _tally_by_currency(invoices):
    """``{currency: {"count", "amount"}}`` of amount due over a list."""
    groups = {}
    for inv in invoices:
        entry = groups.setdefault(inv.currency, {"count": 0, "amount": Decimal("0")})
        entry["count"] += 1
        entry["amount"] += inv.amount_due or Decimal("0")
    return groups


class InvoiceDashboardView(APIView):
    """Dashboard summary for invoices"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Reports"], operation_id="dashboard")
    def get(self, request):
        denied = _forbid_non_admin_reports(request)
        if denied:
            return denied

        org = request.profile.org
        today = timezone.localdate()
        thirty_days_ago = today - timedelta(days=30)

        # Total invoices
        all_invoices = Invoice.objects.filter(org=org)

        # Status counts
        status_counts = (
            all_invoices.values("status").annotate(count=Count("id")).order_by("status")
        )

        # Financial summary, per currency (see currency_block).
        summary = currency_block(
            group_by_currency(
                all_invoices,
                total_invoiced=Sum("total_amount"),
                total_paid=Sum("amount_paid"),
                total_due=Sum("amount_due"),
            ),
            money=("total_invoiced", "total_paid", "total_due"),
        )

        # Overdue invoices
        overdue_invoices = all_invoices.filter(
            status__in=UNPAID_STATUSES,
            due_date__lt=today,
        )
        overdue = currency_block(
            group_by_currency(
                overdue_invoices, count=Count("id"), amount=Sum("amount_due")
            ),
            money=("amount",),
            counts=("count",),
        )

        # Recent activity (last 30 days)
        recent_invoices = all_invoices.filter(created_at__gte=thirty_days_ago)
        recent_paid = all_invoices.filter(paid_at__gte=thirty_days_ago)

        recent = group_by_currency(recent_paid, revenue_30d=Sum("amount_paid"))
        group_by_currency(recent_invoices, recent, invoiced_30d=Sum("total_amount"))
        recent = currency_block(recent, money=("revenue_30d", "invoiced_30d"))

        # Average days from issue to payment, over paid invoices that carry both
        # dates. Computed in Python so it does not depend on a database-specific
        # date-difference expression; the paid set is bounded.
        paid_with_dates = all_invoices.filter(
            paid_at__isnull=False, issue_date__isnull=False
        ).only("paid_at", "issue_date")
        pay_gaps = [
            (timezone.localdate(inv.paid_at) - inv.issue_date).days
            for inv in paid_with_dates
        ]
        average_days_to_pay = round(sum(pay_gaps) / len(pay_gaps)) if pay_gaps else 0

        # Estimates summary
        all_estimates = Estimate.objects.filter(org=org)
        estimates_pending = all_estimates.filter(status__in=["Sent", "Viewed"]).count()
        estimates_accepted = all_estimates.filter(status="Accepted").count()
        estimates_declined = all_estimates.filter(status="Declined").count()

        return Response(
            {
                "summary": summary,
                "invoice_count": all_invoices.count(),
                "average_days_to_pay": average_days_to_pay,
                "status_counts": {
                    item["status"]: item["count"] for item in status_counts
                },
                "overdue": overdue,
                "recent_activity": {
                    **recent,
                    "invoices_created_30d": recent_invoices.count(),
                    "invoices_paid_30d": recent_paid.count(),
                },
                "estimates": {
                    "pending": estimates_pending,
                    "accepted": estimates_accepted,
                    "declined": estimates_declined,
                },
            }
        )


class RevenueReportView(APIView):
    """Revenue report with date range and grouping"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Reports"], operation_id="revenue_report")
    def get(self, request):
        denied = _forbid_non_admin_reports(request)
        if denied:
            return denied

        org = request.profile.org

        # Date range filters
        start_date = request.GET.get("start_date")
        end_date = request.GET.get("end_date")
        group_by = request.GET.get("group_by", "month")  # day, week, month, year

        if not start_date:
            start_date = timezone.localdate() - timedelta(days=365)
        else:
            start_date = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()

        if not end_date:
            end_date = timezone.localdate()
        else:
            end_date = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()

        from django.db.models.functions import (
            TruncDay,
            TruncMonth,
            TruncWeek,
            TruncYear,
        )

        trunc = {
            "day": TruncDay,
            "week": TruncWeek,
            "year": TruncYear,
        }.get(group_by, TruncMonth)

        # Two series over the same window, on two different dates: money
        # *collected* is grouped by paid_at, money *invoiced* by issue_date. They
        # do not line up period to period -- an invoice raised in one month is
        # paid in another -- which is the whole point the chart is trying to
        # show, so both are returned and merged by period rather than divided.
        paid_invoices = Invoice.objects.filter(
            org=org,
            paid_at__date__gte=start_date,
            paid_at__date__lte=end_date,
        )
        paid_grouped = (
            paid_invoices.annotate(period=trunc("paid_at"))
            .values("period", "currency")
            .annotate(revenue=Sum("amount_paid"), count=Count("id"))
            .order_by()
        )

        invoiced_invoices = Invoice.objects.filter(
            org=org,
            issue_date__gte=start_date,
            issue_date__lte=end_date,
        )
        invoiced_grouped = (
            invoiced_invoices.annotate(period=trunc("issue_date"))
            .values("period", "currency")
            .annotate(invoiced=Sum("total_amount"))
            .order_by()
        )

        # Merge the two series into period -> currency -> figures.
        periods = {}
        for item in [*paid_grouped, *invoiced_grouped]:
            period = item.pop("period")
            key = period.strftime("%Y-%m-%d") if period else None
            periods.setdefault(key, {}).setdefault(item.pop("currency"), {}).update(
                item
            )

        series = {"money": ("invoiced", "revenue"), "counts": ("count",)}
        data = [
            {"period": key, **currency_block(periods[key], **series)}
            for key in sorted(periods, key=lambda k: (k is None, k))
        ]

        total = group_by_currency(
            paid_invoices, revenue=Sum("amount_paid"), count=Count("id")
        )
        group_by_currency(invoiced_invoices, total, invoiced=Sum("total_amount"))

        return Response(
            {
                "start_date": str(start_date),
                "end_date": str(end_date),
                "group_by": group_by,
                "data": data,
                "total": currency_block(total, **series),
            }
        )


class AgingReportView(APIView):
    """Accounts receivable aging report"""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["Reports"], operation_id="aging_report")
    def get(self, request):
        denied = _forbid_non_admin_reports(request)
        if denied:
            return denied

        org = request.profile.org
        today = timezone.localdate()

        # Get unpaid invoices. select_related the account so the by-account
        # roll-up below does not fire a query per invoice.
        unpaid_invoices = Invoice.objects.filter(
            org=org,
            status__in=UNPAID_STATUSES,
            amount_due__gt=0,
        ).select_related("account")

        # Categorize by age
        current = []  # Not yet due
        days_1_30 = []
        days_31_60 = []
        days_61_90 = []
        over_90 = []
        # Overdue money rolled up by who owes it -- the "who owes it" table needs
        # a per-account view the capped per-bucket invoice lists cannot give.
        by_account = {}

        for invoice in unpaid_invoices:
            if not invoice.due_date:
                current.append(invoice)
                continue

            days_overdue = (today - invoice.due_date).days

            if days_overdue <= 0:
                current.append(invoice)
                continue

            if days_overdue <= 30:
                days_1_30.append(invoice)
            elif days_overdue <= 60:
                days_31_60.append(invoice)
            elif days_overdue <= 90:
                days_61_90.append(invoice)
            else:
                over_90.append(invoice)

            # Only genuinely-overdue invoices (past their due date) roll up here,
            # one row per account and currency so unlike money is never added.
            account_id = str(invoice.account_id) if invoice.account_id else None
            key = (account_id, invoice.currency)
            entry = by_account.get(key)
            if entry is None:
                entry = {
                    "id": account_id,
                    "currency": invoice.currency,
                    "name": (
                        invoice.account.name
                        if invoice.account_id
                        else (invoice.client_name or "No account")
                    ),
                    "count": 0,
                    "oldest_days": 0,
                    "amount": Decimal("0"),
                }
                by_account[key] = entry
            entry["count"] += 1
            entry["amount"] += invoice.amount_due or Decimal("0")
            entry["oldest_days"] = max(entry["oldest_days"], days_overdue)

        def tally(invoices):
            return currency_block(
                _tally_by_currency(invoices), money=("amount",), counts=("count",)
            )

        def summarize(invoices):
            return {
                **tally(invoices),
                "invoices": [
                    {
                        "id": str(inv.id),
                        "invoice_number": inv.invoice_number,
                        "client_name": inv.client_name,
                        "due_date": str(inv.due_date) if inv.due_date else None,
                        "amount_due": str(inv.amount_due),
                        "currency": inv.currency,
                        "days_overdue": (
                            (today - inv.due_date).days if inv.due_date else 0
                        ),
                    }
                    for inv in invoices[:10]  # Limit to 10 per category
                ],
            }

        overdue_invoices = days_1_30 + days_31_60 + days_61_90 + over_90

        # Largest first within each currency; amounts are only comparable there.
        by_account_rows = [
            {**row, "amount": str(row["amount"])}
            for row in sorted(
                by_account.values(), key=lambda r: (r["currency"], -r["amount"])
            )
        ]

        return Response(
            {
                "current": summarize(current),
                "1_30_days": summarize(days_1_30),
                "31_60_days": summarize(days_31_60),
                "61_90_days": summarize(days_61_90),
                "over_90_days": summarize(over_90),
                "overdue": tally(overdue_invoices),
                "by_account": by_account_rows,
                "total": tally(current + overdue_invoices),
            }
        )


# =============================================================================
# INVOICE FROM OPPORTUNITY
# =============================================================================


def _first_visible_contact(contacts, profile):
    """The contact a generated invoice is billed to, or None.

    ``contacts`` is a deal's or an account's contacts manager. A record can
    keep contacts linked that the caller may not open (an edit keeps what it
    cannot see), and a bare ``.first()`` could pick one of those and hand the
    caller its name on an invoice they then own. So only contacts the caller
    may open are candidates, which also keeps it inside their org. Newest
    first, the contact list's own order, with the id breaking ties so the
    answer does not change between calls.
    """
    return (
        contacts.filter(id__in=visible_contacts_qs(profile).values("id"))
        .order_by("-created_at", "id")
        .first()
    )


class InvoiceFromOpportunityView(APIView):
    """
    Create an invoice from a won opportunity.
    Copies all line items from the opportunity to the new invoice.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        operation_id="invoice_from_opportunity",
        tags=["Invoices"],
        description="Create an invoice from a won opportunity with line items",
        responses={
            201: InvoiceSerializer(),
            400: {"description": "Bad request - opportunity not won or no line items"},
            404: {"description": "Opportunity not found"},
        },
    )
    def post(self, request, opportunity_id, *args, **kwargs):
        """Create invoice from opportunity"""
        from opportunity.access import visible_deals_qs
        from opportunity.workflow import WON

        org = request.profile.org

        # A deal the caller may not open answers exactly like one that does
        # not exist, so this endpoint cannot be used to probe for deal ids.
        opportunity = (
            visible_deals_qs(request.profile, request.user)
            .filter(id=opportunity_id)
            .prefetch_related("line_items", "contacts")
            .first()
        )

        if not opportunity:
            return Response(
                {"error": True, "message": "Opportunity not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Verify the opportunity sits in a won stage, whatever it is called.
        stage = opportunity.current_stage()
        if stage is None or stage.kind != WON:
            return Response(
                {
                    "error": True,
                    "message": "Invoice can only be created from won opportunities",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Verify opportunity has line items
        line_items = opportunity.line_items.all()
        if not line_items.exists():
            return Response(
                {
                    "error": True,
                    "message": "Opportunity has no products/line items to invoice",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if opportunity has an account
        if not opportunity.account:
            return Response(
                {
                    "error": True,
                    "message": "Opportunity must have an account to create an invoice",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # The account the invoice is billed to, under the rule the create
        # serializers apply: seeing the deal is not access to its account, and
        # the invoice this makes would hand the caller the account's name.
        try:
            validate_document_account(opportunity.account_id, request.profile, None)
        except ValidationError as exc:
            return Response(
                {"error": True, "message": str(exc.detail[0])},
                status=status.HTTP_400_BAD_REQUEST,
            )

        primary_contact = _first_visible_contact(opportunity.contacts, request.profile)

        if not primary_contact:
            return Response(
                {
                    "error": True,
                    "message": "Opportunity must have at least one contact you can "
                    "open to create an invoice",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # Invoice.save() allocates the number from the org's own sequence
            # (generate_invoice_number).
            invoice = Invoice.objects.create(
                invoice_title=f"Invoice for {opportunity.name}"[
                    : Invoice._meta.get_field("invoice_title").max_length
                ],
                account=opportunity.account,
                contact=primary_contact,
                opportunity=opportunity,
                currency=opportunity.currency or org.default_currency,
                status="Draft",
                issue_date=timezone.localdate(),
                due_date=timezone.localdate() + timedelta(days=30),
                org=org,
            )

            # Copy opportunity assigned users to invoice
            if opportunity.assigned_to.exists():
                invoice.assigned_to.set(opportunity.assigned_to.all())

            # Copy line items from opportunity to invoice
            for opp_item in line_items:
                InvoiceLineItem.objects.create(
                    invoice=invoice,
                    product=opp_item.product,
                    name=opp_item.name,
                    description=opp_item.description,
                    quantity=opp_item.quantity,
                    unit_price=opp_item.unit_price,
                    discount_type=opp_item.discount_type or "",
                    discount_value=opp_item.discount_value,
                    tax_rate=Decimal("0"),  # No tax at creation, user can add later
                    order=opp_item.order,
                    org=org,
                )

            invoice.recalculate_totals()
            invoice.save()

        create_invoice_history.delay(
            str(invoice.id), str(request.profile.id), [], str(org.id)
        )

        # Return the created invoice
        return Response(
            {
                "error": False,
                "message": "Invoice created successfully from opportunity",
                "invoice": InvoiceSerializer(invoice).data,
            },
            status=status.HTTP_201_CREATED,
        )


class InvoiceFromTimeEntriesView(APIView):
    """``POST /api/invoices/from-time-entries/``, turn billable time entries
    into a draft invoice (Tier 3 time-tracking).

    Body: ``{"account_id": "<uuid>", "entry_ids": ["<uuid>", ...]}``.

    Org admins and Django superusers only; anyone else gets a 403 before the
    body is read. Billing time is a finance action over the whole org's
    logged hours, while a member may only see their own entries
    (``cases.time_views._visible_entry_qs``), so a member could otherwise
    bill a colleague's time and then read it back as the invoice's creator.

    Every entry must be on a ticket whose account is ``account_id``. An entry
    on a ticket with no account, or with another account, is a 400 and
    nothing is written. The account and the entries are looked up inside the
    caller's org, so another org's ids answer 404.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    def post(self, request, *args, **kwargs):
        from accounts.models import Account
        from cases.models import TimeEntry
        from cases.services.billing import (
            TimeEntryInvoicingError,
            attach_entries_to_invoice,
            build_invoice_lines_from_entries,
        )

        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {
                    "error": True,
                    "message": "Only admins can create invoices from time entries.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        org = request.profile.org
        account_id = request.data.get("account_id")
        if not account_id:
            return Response(
                {"error": True, "message": "account_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        validate_uuid(account_id, "account_id")
        # Deduplicated as UUIDs, not strings, so the same id sent twice (or
        # once upper-case, once lower) is one entry rather than a false 404.
        entry_ids = {
            uuid.UUID(str(i))
            for i in payload_id_list(request.data.get("entry_ids"), "entry_ids")
        }
        if not entry_ids:
            return Response(
                {"error": True, "message": "entry_ids must be a non-empty list."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        account = Account.objects.filter(id=account_id, org=org).first()
        if account is None:
            return Response(
                {"error": True, "message": "Account not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        with transaction.atomic():
            entries = list(
                TimeEntry.objects.select_for_update()
                .select_related("case")
                .filter(id__in=entry_ids, org=org)
            )
            if len(entries) != len(entry_ids):
                return Response(
                    {
                        "error": True,
                        "message": "One or more time entries were not found.",
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

            for entry in entries:
                if entry.case.account_id != account.id:
                    return Response(
                        {
                            "error": True,
                            "message": f"Time entry {entry.id} is not on a "
                            "ticket for this account.",
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            try:
                currency, lines = build_invoice_lines_from_entries(entries)
            except TimeEntryInvoicingError as exc:
                return Response(
                    {"error": True, "message": str(exc)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            primary_contact = _first_visible_contact(account.contacts, request.profile)

            # Invoice.save() generates invoice_number, public_token, due_date,
            # and recalculates totals. We just hand it the high-level fields.
            invoice = Invoice.objects.create(
                invoice_title=f"Time entries, {timezone.localdate():%Y-%m-%d}",
                account=account,
                contact=primary_contact,
                currency=currency,
                status="Draft",
                org=org,
            )
            for line in lines:
                InvoiceLineItem.objects.create(invoice=invoice, org=org, **line)

            attach_entries_to_invoice(entries, invoice)
            invoice.recalculate_totals()
            invoice.save()

        return Response(
            {
                "error": False,
                "message": "Draft invoice created from time entries.",
                "invoice_id": str(invoice.id),
                "invoice_number": invoice.invoice_number,
                "currency": currency,
                "line_count": len(lines),
                "entry_ids": [str(e.id) for e in entries],
            },
            status=status.HTTP_201_CREATED,
        )
