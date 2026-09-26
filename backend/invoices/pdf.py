"""
PDF Generation for Invoices and Estimates using WeasyPrint.

This module provides functions to generate professional PDF documents
for invoices and estimates with customizable templates.
"""

import io
import os
from decimal import Decimal
from urllib.parse import unquote, urlparse

from django.conf import settings
from django.template.loader import render_to_string

try:
    from weasyprint import CSS, HTML, default_url_fetcher
    from weasyprint.text.fonts import FontConfiguration

    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False


def check_weasyprint():
    """Check if WeasyPrint is available."""
    if not WEASYPRINT_AVAILABLE:
        raise ImportError(
            "WeasyPrint is not installed. Install it with: pip install weasyprint"
        )


def _within(path, root):
    """True only if `path` resolves to a location inside `root`.

    Both are canonicalised with realpath first so that `..` segments and
    symlinks cannot escape `root`.
    """
    if not root:
        return False
    try:
        root_real = os.path.realpath(str(root))
        path_real = os.path.realpath(str(path))
        return os.path.commonpath([root_real, path_real]) == root_real
    except (ValueError, OSError):
        return False


# The ONLY files a PDF render legitimately loads are logos. Restrict local file
# access to these subtrees of MEDIA_ROOT so a crafted template cannot embed
# another tenant's uploaded attachments/documents (MEDIA_ROOT is a flat,
# cross-tenant tree: `attachments/…`, `docs/…` are NOT org-partitioned). Only a
# cross-org LOGO read remains possible, which is low-sensitivity branding.
_ALLOWED_MEDIA_SUBDIRS = ("org_logos/", "invoice_templates/logos/")


def _allowed_media_file(parsed):
    """Map an allowed local MEDIA logo url onto its on-disk path, or return None.

    Three walls: the url path must sit under MEDIA_URL; the remainder must be one
    of the logo subtrees in ``_ALLOWED_MEDIA_SUBDIRS`` (so attachments/docs of
    any org are unreachable); and the joined path, once canonicalised, must not
    escape MEDIA_ROOT (blocks ``..``/symlink traversal). Anything else, app
    source, ``crm/settings.py``, ``.env``, keys, other orgs' attachments, is
    outside the allowlist and returns None.
    """
    media_root = getattr(settings, "MEDIA_ROOT", "")
    if not media_root or not os.path.isabs(str(media_root)):
        # No usable local MEDIA root (e.g. the S3 marker in prod), no local
        # file is legitimately loadable; the http(s) allowlist handles prod.
        return None

    url_path = unquote(parsed.path or "")
    media_url = getattr(settings, "MEDIA_URL", "/media/") or "/media/"
    prefix = urlparse(media_url).path or media_url
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    if not prefix.endswith("/"):
        prefix += "/"
    if not url_path.startswith(prefix):
        return None

    rel = url_path[len(prefix) :].lstrip("/")
    if not rel.startswith(_ALLOWED_MEDIA_SUBDIRS):
        return None

    candidate = os.path.join(str(media_root), rel)
    if _within(candidate, media_root) and os.path.isfile(candidate):
        return candidate
    return None


def safe_pdf_url_fetcher(url):
    """Restrict what WeasyPrint may fetch while rendering an invoice/estimate.

    Invoice templates carry org-authored HTML/CSS (``template_html`` /
    ``template_css``) that is rendered server-side with ``base_url=BASE_DIR``.
    Left unrestricted (the WeasyPrint default fetcher allows ``file://`` and any
    ``http(s)``), an ``<img>`` or CSS ``url()`` in that markup turns PDF
    generation into an SSRF (cloud metadata at 169.254.169.254, localhost,
    internal services) and a local-file read (``/etc/passwd``, app source,
    ``.env``, secret keys). This fetcher denies by default and only permits the
    three things a legitimate render actually needs:

    * ``data:`` URIs, inert, no disk or network.
    * A local logo file inside MEDIA_ROOT (see ``_allowed_media_file``), dev.
    * HTTPS to the configured S3 media host only. The org logo in prod.

    Must be passed to BOTH ``HTML(...)`` and ``CSS(...)``, WeasyPrint resolves
    ``@import`` / ``@font-face`` / ``@color-profile`` at CSS parse time through
    the CSS object's own fetcher, so a CSS() left on the default fetcher would
    reopen the whole SSRF/LFI through ``template_css``.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()

    if scheme == "data":
        return default_url_fetcher(url)

    # http(s), and protocol-relative //host/path, only to the S3 media host.
    if scheme in ("http", "https") or (scheme == "" and parsed.netloc):
        s3_host = (getattr(settings, "AWS_S3_CUSTOM_DOMAIN", "") or "").lower()
        host = (parsed.hostname or "").lower()
        if s3_host and host == s3_host:
            # Residual: default_url_fetcher follows 30x, and only the first hop
            # is host-checked. Low risk. The S3 host is trusted and fixed, and
            # only serves the org's own logo object; urllib refuses redirects to
            # non-http(s)/ftp, so no redirect->file://.
            return default_url_fetcher(url)
        raise ValueError(f"Blocked non-allowlisted host in PDF template: {url!r}")

    # file:// or a bare local path, only files inside MEDIA_ROOT.
    if scheme in ("", "file"):
        candidate = _allowed_media_file(parsed)
        if candidate:
            return default_url_fetcher("file://" + candidate)
        raise ValueError(f"Blocked local-file access in PDF template: {url!r}")

    # ftp:, gopher:, jar:, dict: .... Deny by default.
    raise ValueError(f"Blocked disallowed URL scheme in PDF template: {url!r}")


def get_default_css():
    """Get the default CSS for PDF generation."""
    css_path = os.path.join(
        os.path.dirname(__file__), "templates", "invoices", "pdf", "invoice.css"
    )
    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as f:
            return f.read()
    return get_fallback_css()


def get_fallback_css():
    """Fallback CSS if the template file doesn't exist."""
    return """
    @page {
        size: A4;
        margin: 1.5cm;
    }

    * {
        box-sizing: border-box;
        margin: 0;
        padding: 0;
    }

    body {
        font-family: 'Helvetica', 'Arial', sans-serif;
        font-size: 10pt;
        line-height: 1.4;
        color: #333;
    }

    .invoice-container {
        width: 100%;
        max-width: 800px;
        margin: 0 auto;
    }

    /* Header */
    .header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        margin-bottom: 30px;
        padding-bottom: 20px;
        border-bottom: 2px solid #3B82F6;
    }

    .logo {
        max-height: 60px;
        max-width: 200px;
    }

    .invoice-title {
        text-align: right;
    }

    .invoice-title h1 {
        font-size: 28pt;
        color: #3B82F6;
        margin-bottom: 5px;
        text-transform: uppercase;
    }

    .invoice-number {
        font-size: 12pt;
        color: #666;
    }

    /* Addresses */
    .addresses {
        display: flex;
        justify-content: space-between;
        margin-bottom: 30px;
    }

    .address-block {
        width: 45%;
    }

    .address-block h3 {
        font-size: 9pt;
        color: #666;
        text-transform: uppercase;
        margin-bottom: 8px;
        letter-spacing: 0.5px;
    }

    .address-block p {
        margin-bottom: 3px;
    }

    .company-name {
        font-weight: bold;
        font-size: 11pt;
        color: #333;
    }

    /* Invoice Details */
    .invoice-details {
        margin-bottom: 30px;
        padding: 15px;
        background-color: #f8fafc;
        border-radius: 4px;
    }

    .details-row {
        display: flex;
        justify-content: space-between;
        margin-bottom: 8px;
    }

    .details-row:last-child {
        margin-bottom: 0;
    }

    .details-label {
        font-weight: bold;
        color: #666;
    }

    .details-value {
        text-align: right;
    }

    .status-badge {
        display: inline-block;
        padding: 3px 10px;
        border-radius: 12px;
        font-size: 9pt;
        font-weight: bold;
        text-transform: uppercase;
    }

    .status-draft { background-color: #e5e7eb; color: #374151; }
    .status-sent { background-color: #dbeafe; color: #1d4ed8; }
    .status-paid { background-color: #d1fae5; color: #047857; }
    .status-overdue { background-color: #fee2e2; color: #dc2626; }
    .status-cancelled { background-color: #fef3c7; color: #d97706; }

    /* Line Items Table */
    .line-items {
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 30px;
    }

    .line-items th {
        background-color: #3B82F6;
        color: white;
        text-align: left;
        padding: 12px 10px;
        font-size: 9pt;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }

    .line-items th:last-child,
    .line-items td:last-child {
        text-align: right;
    }

    .line-items td {
        padding: 12px 10px;
        border-bottom: 1px solid #e5e7eb;
    }

    .line-items tr:nth-child(even) td {
        background-color: #f9fafb;
    }

    .item-description {
        max-width: 250px;
    }

    /* Totals */
    .totals-section {
        display: flex;
        justify-content: flex-end;
        margin-bottom: 30px;
    }

    .totals-table {
        width: 300px;
    }

    .totals-row {
        display: flex;
        justify-content: space-between;
        padding: 8px 0;
        border-bottom: 1px solid #e5e7eb;
    }

    .totals-row.grand-total {
        border-top: 2px solid #333;
        border-bottom: none;
        font-size: 14pt;
        font-weight: bold;
        color: #3B82F6;
        padding-top: 12px;
    }

    .totals-label {
        color: #666;
    }

    .totals-value {
        text-align: right;
        font-weight: 500;
    }

    /* Notes and Terms */
    .notes-section {
        margin-bottom: 20px;
    }

    .notes-section h3 {
        font-size: 10pt;
        color: #333;
        margin-bottom: 8px;
        text-transform: uppercase;
    }

    .notes-section p {
        color: #666;
        font-size: 9pt;
        line-height: 1.5;
    }

    /* Footer */
    .footer {
        margin-top: 40px;
        padding-top: 20px;
        border-top: 1px solid #e5e7eb;
        text-align: center;
        font-size: 9pt;
        color: #999;
    }

    /* Payment Info */
    .payment-info {
        background-color: #f0f9ff;
        padding: 15px;
        border-radius: 4px;
        margin-bottom: 20px;
    }

    .payment-info h3 {
        color: #0369a1;
        margin-bottom: 10px;
    }
    """


def format_currency(amount, currency="USD"):
    """Format amount with currency symbol."""
    symbols = {
        "USD": "$",
        "EUR": "\u20ac",
        "GBP": "\u00a3",
        "INR": "\u20b9",
        "JPY": "\u00a5",
        "CAD": "C$",
        "AUD": "A$",
        "AED": "AED ",
        "SAR": "SAR ",
        "QAR": "QAR ",
        "KWD": "KWD ",
        "BHD": "BHD ",
        "OMR": "OMR ",
        "CHF": "CHF ",
        "SGD": "S$",
        "HKD": "HK$",
        "NZD": "NZ$",
        "ZAR": "R",
        "MXN": "MX$",
        "BRL": "R$",
        "CNY": "\u00a5",
        "KRW": "\u20a9",
        "THB": "\u0e3f",
        "MYR": "RM",
        "PHP": "\u20b1",
        "IDR": "Rp",
        "VND": "\u20ab",
        "SEK": "kr",
        "NOK": "kr",
        "DKK": "kr",
        "PLN": "z\u0142",
        "CZK": "K\u010d",
        "HUF": "Ft",
        "RUB": "\u20bd",
        "TRY": "\u20ba",
        "ILS": "\u20aa",
        "EGP": "E\u00a3",
        "NGN": "\u20a6",
        "KES": "KSh",
    }
    symbol = symbols.get(currency, currency + " ")
    if amount is None:
        amount = Decimal("0.00")
    return f"{symbol}{amount:,.2f}"


def generate_invoice_pdf(invoice, include_payments=True):
    """
    Generate a PDF for an invoice.

    Args:
        invoice: Invoice model instance
        include_payments: Whether to include payment history

    Returns:
        bytes: PDF file content
    """
    check_weasyprint()

    # Get the template - use invoice's template, or fall back to org's default template
    template = invoice.template
    if not template:
        from invoices.models import InvoiceTemplate

        template = InvoiceTemplate.objects.filter(
            org=invoice.org, is_default=True
        ).first()

    # Pre-format line items with currency
    line_items = []
    for item in invoice.line_items.all().order_by("order"):
        # Use name field if present, otherwise fall back to description
        line_items.append(
            {
                "name": item.name if item.name else item.description,
                "description": (
                    item.description if item.name else ""
                ),  # Show description as subtitle if name is set
                "quantity": item.quantity,
                "unit_price": format_currency(item.unit_price, invoice.currency),
                "tax_rate": item.tax_rate,
                # net_amount, so the column adds up to the subtotal below it.
                "total": format_currency(item.net_amount, invoice.currency),
            }
        )

    # Pre-format payments
    payments = []
    if include_payments:
        for payment in invoice.payments.all().order_by("-payment_date"):
            payments.append(
                {
                    "payment_date": payment.payment_date,
                    "payment_method": payment.payment_method,
                    "get_payment_method_display": payment.get_payment_method_display(),
                    "reference_number": payment.reference_number,
                    "amount": format_currency(payment.amount, invoice.currency),
                }
            )

    # Prepare context with pre-formatted currency values
    context = {
        "invoice": invoice,
        "line_items": line_items,
        "payments": payments,
        "org": invoice.org,
        "status_class": f"status-{invoice.status.lower().replace('_', '-')}",
        # Pre-formatted currency values
        "subtotal": format_currency(invoice.subtotal, invoice.currency),
        "discount_amount": format_currency(invoice.discount_amount, invoice.currency),
        "tax_amount": format_currency(invoice.tax_amount, invoice.currency),
        "shipping_amount": format_currency(invoice.shipping_amount, invoice.currency),
        "total_amount": format_currency(invoice.total_amount, invoice.currency),
        "amount_paid": format_currency(invoice.amount_paid, invoice.currency),
        "amount_due": format_currency(invoice.amount_due, invoice.currency),
    }

    # Check for custom template HTML
    if template and template.template_html:
        html_content = template.template_html
        # Replace placeholders with actual values
        html_content = render_invoice_template(html_content, context)
    else:
        # Use default template
        html_content = render_to_string("invoices/pdf/invoice.html", context)

    # Get CSS
    if template and template.template_css:
        css_content = template.template_css
    else:
        css_content = get_default_css()

    # Apply template colors if set
    if template:
        css_content = css_content.replace("#3B82F6", template.primary_color)
        if template.secondary_color:
            css_content = css_content.replace("#1E40AF", template.secondary_color)

    # Generate PDF
    font_config = FontConfiguration()
    html = HTML(
        string=html_content,
        base_url=settings.BASE_DIR,
        url_fetcher=safe_pdf_url_fetcher,
    )
    css = CSS(
        string=css_content,
        base_url=settings.BASE_DIR,
        url_fetcher=safe_pdf_url_fetcher,
        font_config=font_config,
    )

    pdf_buffer = io.BytesIO()
    html.write_pdf(pdf_buffer, stylesheets=[css], font_config=font_config)
    pdf_buffer.seek(0)

    return pdf_buffer.getvalue()


def generate_estimate_pdf(estimate):
    """
    Generate a PDF for an estimate.

    Args:
        estimate: Estimate model instance

    Returns:
        bytes: PDF file content
    """
    check_weasyprint()

    # Get the org's default template for styling
    from invoices.models import InvoiceTemplate

    template = InvoiceTemplate.objects.filter(org=estimate.org, is_default=True).first()

    # Pre-format line items with currency
    line_items = []
    for item in estimate.line_items.all().order_by("order"):
        # Use name field if present, otherwise fall back to description
        line_items.append(
            {
                "name": item.name if item.name else item.description,
                "description": (
                    item.description if item.name else ""
                ),  # Show description as subtitle if name is set
                "quantity": item.quantity,
                "unit_price": format_currency(item.unit_price, estimate.currency),
                "tax_rate": item.tax_rate,
                # net_amount, so the column adds up to the subtotal below it.
                "total": format_currency(item.net_amount, estimate.currency),
            }
        )

    # Prepare context with pre-formatted currency values
    context = {
        "estimate": estimate,
        "line_items": line_items,
        "org": estimate.org,
        "status_class": f"status-{estimate.status.lower().replace('_', '-')}",
        "is_expired": estimate.is_expired,
        # Pre-formatted currency values
        "subtotal": format_currency(estimate.subtotal, estimate.currency),
        "discount_amount": format_currency(estimate.discount_amount, estimate.currency),
        "tax_amount": format_currency(estimate.tax_amount, estimate.currency),
        "total_amount": format_currency(estimate.total_amount, estimate.currency),
    }

    # Use default template
    html_content = render_to_string("invoices/pdf/estimate.html", context)
    css_content = get_default_css()

    # Apply template colors if set
    if template:
        css_content = css_content.replace("#3B82F6", template.primary_color)
        if template.secondary_color:
            css_content = css_content.replace("#1E40AF", template.secondary_color)

    # Generate PDF
    font_config = FontConfiguration()
    html = HTML(
        string=html_content,
        base_url=settings.BASE_DIR,
        url_fetcher=safe_pdf_url_fetcher,
    )
    css = CSS(
        string=css_content,
        base_url=settings.BASE_DIR,
        url_fetcher=safe_pdf_url_fetcher,
        font_config=font_config,
    )

    pdf_buffer = io.BytesIO()
    html.write_pdf(pdf_buffer, stylesheets=[css], font_config=font_config)
    pdf_buffer.seek(0)

    return pdf_buffer.getvalue()


def render_invoice_template(template_html, context):
    """
    Render a custom invoice template with context variables.

    Supports basic variable substitution like {{invoice.invoice_number}}.
    """
    invoice = context.get("invoice")
    org = context.get("org")
    currency = invoice.currency if invoice else "USD"

    # Basic variable substitutions
    replacements = {
        "{{invoice.invoice_number}}": invoice.invoice_number,
        "{{invoice.invoice_title}}": invoice.invoice_title,
        "{{invoice.status}}": invoice.status,
        "{{invoice.client_name}}": invoice.client_name,
        "{{invoice.client_email}}": invoice.client_email,
        "{{invoice.client_phone}}": invoice.client_phone or "",
        "{{invoice.issue_date}}": str(invoice.issue_date) if invoice.issue_date else "",
        "{{invoice.due_date}}": str(invoice.due_date) if invoice.due_date else "",
        "{{invoice.subtotal}}": format_currency(invoice.subtotal, currency),
        "{{invoice.discount_amount}}": format_currency(
            invoice.discount_amount, currency
        ),
        "{{invoice.tax_amount}}": format_currency(invoice.tax_amount, currency),
        "{{invoice.total_amount}}": format_currency(invoice.total_amount, currency),
        "{{invoice.amount_paid}}": format_currency(invoice.amount_paid, currency),
        "{{invoice.amount_due}}": format_currency(invoice.amount_due, currency),
        "{{invoice.notes}}": invoice.notes or "",
        "{{invoice.terms}}": invoice.terms or "",
        "{{org.name}}": org.name if org else "",
    }

    # Address fields
    address_fields = [
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
    ]
    for field in address_fields:
        replacements[f"{{{{invoice.{field}}}}}"] = getattr(invoice, field, "") or ""

    for placeholder, value in replacements.items():
        template_html = template_html.replace(placeholder, str(value))

    return template_html


def generate_invoice_filename(invoice):
    """Generate a filename for the invoice PDF."""
    return f"Invoice_{invoice.invoice_number}.pdf"


def generate_estimate_filename(estimate):
    """Generate a filename for the estimate PDF."""
    return f"Estimate_{estimate.estimate_number}.pdf"
