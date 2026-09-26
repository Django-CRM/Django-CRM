"""
Public views for client portal.

These views do not require authentication and are accessed via public tokens.
"""

import logging

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from common.portal_tokens import resolve_portal_org
from common.tasks import set_rls_context
from invoices.models import Estimate, Invoice, InvoiceTemplate
from invoices.pdf import (
    generate_estimate_filename,
    generate_estimate_pdf,
    generate_invoice_filename,
    generate_invoice_pdf,
)

logger = logging.getLogger(__name__)


def _resolve_org_context(token, resource_type):
    """Resolve the org from the URL token and set RLS context for this request.

    Public endpoints reach the view with an empty ``app.current_org`` (the
    middleware exempts them but sets no context), so under a correctly-configured
    non-superuser DB role the resource query below would match zero rows and 404
    every customer. Resolving the org from the unscoped token lookup and setting
    the context first lets the query run under full RLS.

    An unknown token leaves the context empty; the scoped query then returns
    nothing and the caller 404s. The same answer a disabled link gives, so a
    stranger learns nothing about whether a token is real.
    """
    org_id = resolve_portal_org(token, resource_type)
    if org_id:
        set_rls_context(org_id)
    return org_id


def _client_ip(request):
    """Best-effort client IP for the acceptance record.

    ``X-Forwarded-For`` is client-spoofable, so this is evidence, not proof;
    the leftmost hop is recorded when a proxy set the header, else REMOTE_ADDR.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class PublicInvoiceView(APIView):
    """
    Public view for invoice - accessible via public token.
    No authentication required.
    """

    authentication_classes = []
    permission_classes = []

    def get(self, request, token):
        """Get invoice by public token"""
        _resolve_org_context(token, "invoice")
        invoice = Invoice.objects.filter(
            public_token=token, public_link_enabled=True
        ).first()

        if not invoice:
            return Response(
                {"error": True, "message": "Invoice not found or link expired"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Track view if first time
        if not invoice.viewed_at:
            invoice.viewed_at = timezone.now()
            if invoice.status == "Sent":
                invoice.status = "Viewed"
            invoice.save()

        # Serialize invoice data for public view
        data = {
            "id": str(invoice.id),
            "invoice_number": invoice.invoice_number,
            "invoice_title": invoice.invoice_title,
            "status": invoice.status,
            "client_name": invoice.client_name,
            "client_email": invoice.client_email,
            "issue_date": invoice.issue_date,
            "due_date": invoice.due_date,
            "subtotal": str(invoice.subtotal),
            "discount_type": invoice.discount_type,
            "discount_value": str(invoice.discount_value),
            "discount_amount": str(invoice.discount_amount),
            "tax_rate": str(invoice.tax_rate),
            "tax_amount": str(invoice.tax_amount),
            "shipping_amount": str(invoice.shipping_amount),
            "total_amount": str(invoice.total_amount),
            "amount_paid": str(invoice.amount_paid),
            "amount_due": str(invoice.amount_due),
            "currency": invoice.currency,
            "notes": invoice.notes,
            "terms": invoice.terms,
            "billing_address": {
                "line": invoice.billing_address_line,
                "city": invoice.billing_city,
                "state": invoice.billing_state,
                "postcode": invoice.billing_postcode,
                "country": invoice.billing_country,
            },
            "line_items": [
                {
                    "name": item.name or "",
                    "description": item.description,
                    "quantity": str(item.quantity),
                    "unit_price": str(item.unit_price),
                    # net_amount, after the line's own discount: the lines add
                    # up to "subtotal" above.
                    "amount": str(item.net_amount),
                }
                for item in invoice.line_items.all().order_by("order")
            ],
            "payments": [
                {
                    "amount": str(payment.amount),
                    "payment_date": payment.payment_date,
                    "payment_method": payment.payment_method,
                }
                for payment in invoice.payments.all().order_by("-payment_date")
            ],
            "org": {
                "name": invoice.org.name if invoice.org else "",
            },
        }

        # Get default template for styling
        template = invoice.template
        if not template and invoice.org:
            template = InvoiceTemplate.objects.filter(
                org=invoice.org, is_default=True
            ).first()

        if template:
            data["template"] = {
                "primary_color": template.primary_color,
                "secondary_color": template.secondary_color,
                "footer_text": template.footer_text or "",
            }
        else:
            data["template"] = {
                "primary_color": "#3B82F6",
                "secondary_color": "#1E40AF",
                "footer_text": "",
            }

        return Response(data)


class PublicInvoicePDFView(APIView):
    """
    Public PDF download for invoice - accessible via public token.
    No authentication required.
    """

    authentication_classes = []
    permission_classes = []

    def get(self, request, token):
        """Download invoice PDF by public token"""
        _resolve_org_context(token, "invoice")
        invoice = Invoice.objects.filter(
            public_token=token, public_link_enabled=True
        ).first()

        if not invoice:
            return Response(
                {"error": True, "message": "Invoice not found or link expired"},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            pdf_content = generate_invoice_pdf(invoice, include_payments=True)
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


class PublicEstimateView(APIView):
    """
    Public view for estimate - accessible via public token.
    No authentication required.
    """

    authentication_classes = []
    permission_classes = []

    def get(self, request, token):
        """Get estimate by public token"""
        _resolve_org_context(token, "estimate")
        estimate = Estimate.objects.filter(
            public_token=token, public_link_enabled=True
        ).first()

        if not estimate:
            return Response(
                {"error": True, "message": "Estimate not found or link expired"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Track view if first time
        if not estimate.viewed_at:
            estimate.viewed_at = timezone.now()
            if estimate.status == "Sent":
                estimate.status = "Viewed"
            estimate.save()

        # Serialize estimate data for public view
        data = {
            "id": str(estimate.id),
            "estimate_number": estimate.estimate_number,
            "title": estimate.title,
            "status": estimate.status,
            "client_name": estimate.client_name,
            "client_email": estimate.client_email,
            "issue_date": estimate.issue_date,
            "expiry_date": estimate.expiry_date,
            "subtotal": str(estimate.subtotal),
            "discount_type": estimate.discount_type,
            "discount_value": str(estimate.discount_value),
            "discount_amount": str(estimate.discount_amount),
            "tax_rate": str(estimate.tax_rate),
            "tax_amount": str(estimate.tax_amount),
            "total_amount": str(estimate.total_amount),
            "currency": estimate.currency,
            "notes": estimate.notes,
            "terms": estimate.terms,
            "client_address": {
                "line": estimate.client_address_line,
                "city": estimate.client_city,
                "state": estimate.client_state,
                "postcode": estimate.client_postcode,
                "country": estimate.client_country,
            },
            "line_items": [
                {
                    "name": item.name or "",
                    "description": item.description,
                    "quantity": str(item.quantity),
                    "unit_price": str(item.unit_price),
                    # net_amount, after the line's own discount: the lines add
                    # up to "subtotal" above.
                    "amount": str(item.net_amount),
                }
                for item in estimate.line_items.all().order_by("order")
            ],
            "org": {
                "name": estimate.org.name if estimate.org else "",
            },
        }

        # Get default template for styling
        if estimate.org:
            template = InvoiceTemplate.objects.filter(
                org=estimate.org, is_default=True
            ).first()
        else:
            template = None

        if template:
            data["template"] = {
                "primary_color": template.primary_color,
                "secondary_color": template.secondary_color,
                "footer_text": template.footer_text or "",
            }
        else:
            data["template"] = {
                "primary_color": "#3B82F6",
                "secondary_color": "#1E40AF",
                "footer_text": "",
            }

        return Response(data)


class PublicEstimatePDFView(APIView):
    """
    Public PDF download for estimate - accessible via public token.
    No authentication required.
    """

    authentication_classes = []
    permission_classes = []

    def get(self, request, token):
        """Download estimate PDF by public token"""
        _resolve_org_context(token, "estimate")
        estimate = Estimate.objects.filter(
            public_token=token, public_link_enabled=True
        ).first()

        if not estimate:
            return Response(
                {"error": True, "message": "Estimate not found or link expired"},
                status=status.HTTP_404_NOT_FOUND,
            )

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


class PublicEstimateAcceptView(APIView):
    """
    Public endpoint to accept an estimate - accessible via public token.
    No authentication required.
    """

    authentication_classes = []
    permission_classes = []

    def post(self, request, token):
        """Accept estimate by public token.

        Accepting a quote authorises its price, so this is enforced, not
        cosmetic: the token holder must be within the estimate's validity
        window and must identify themselves. Both checks live here because the
        frontend is not a trust boundary, hiding the button past expiry, or
        collecting a name the client could omit, protects nothing.
        """
        _resolve_org_context(token, "estimate")
        estimate = Estimate.objects.filter(
            public_token=token, public_link_enabled=True
        ).first()

        if not estimate:
            return Response(
                {"error": True, "message": "Estimate not found or link expired"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if estimate.status not in ["Sent", "Viewed"]:
            return Response(
                {
                    "error": True,
                    "message": "Estimate cannot be accepted in current state",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # An expired quote is not acceptable at its original price. expiry_date
        # is stored and printed on the estimate but was previously enforced
        # nowhere; the check belongs on the server.
        if estimate.is_expired:
            return Response(
                {
                    "error": True,
                    "message": (
                        f"This estimate expired on {estimate.expiry_date} and can "
                        "no longer be accepted. Please request an updated quote."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Acceptance is an authorisation; record who gave it. Name and a valid
        # email are required so the record is more than "whoever held the link".
        name = (request.data.get("name") or "").strip()
        email = (request.data.get("email") or "").strip()
        if not name or not email:
            return Response(
                {
                    "error": True,
                    "message": "Your name and email are required to accept this estimate.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            validate_email(email)
        except ValidationError:
            return Response(
                {"error": True, "message": "Please enter a valid email address."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        estimate.status = "Accepted"
        estimate.accepted_at = timezone.now()
        estimate.accepted_by_name = name[:255]
        estimate.accepted_by_email = email[:254]
        estimate.accepted_ip = _client_ip(request)
        estimate.accepted_user_agent = (request.META.get("HTTP_USER_AGENT") or "")[
            :1024
        ]
        estimate.save()

        return Response({"error": False, "message": "Estimate accepted successfully"})


class PublicEstimateDeclineView(APIView):
    """
    Public endpoint to decline an estimate - accessible via public token.
    No authentication required.
    """

    authentication_classes = []
    permission_classes = []

    def post(self, request, token):
        """Decline estimate by public token"""
        _resolve_org_context(token, "estimate")
        estimate = Estimate.objects.filter(
            public_token=token, public_link_enabled=True
        ).first()

        if not estimate:
            return Response(
                {"error": True, "message": "Estimate not found or link expired"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if estimate.status not in ["Sent", "Viewed"]:
            return Response(
                {
                    "error": True,
                    "message": "Estimate cannot be declined in current state",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        estimate.status = "Declined"
        estimate.declined_at = timezone.now()
        estimate.save()

        return Response({"error": False, "message": "Estimate declined"})
