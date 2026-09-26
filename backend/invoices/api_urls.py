from django.urls import path

from invoices import api_views, export_views

app_name = "api_invoices"

urlpatterns = [
    # ==========================================================================
    # INVOICES
    # ==========================================================================
    path("", api_views.InvoiceListView.as_view(), name="invoice_list"),
    path("export/", export_views.InvoiceExportView.as_view(), name="invoice_export"),
    path("<uuid:pk>/", api_views.InvoiceDetailView.as_view(), name="invoice_detail"),
    path("<uuid:pk>/send/", api_views.InvoiceSendView.as_view(), name="invoice_send"),
    path(
        "<uuid:pk>/mark-paid/",
        api_views.InvoiceMarkPaidView.as_view(),
        name="invoice_mark_paid",
    ),
    path(
        "<uuid:pk>/duplicate/",
        api_views.InvoiceDuplicateView.as_view(),
        name="invoice_duplicate",
    ),
    path(
        "<uuid:pk>/pdf/",
        api_views.InvoicePDFView.as_view(),
        name="invoice_pdf",
    ),
    path(
        "<uuid:pk>/cancel/",
        api_views.InvoiceCancelView.as_view(),
        name="invoice_cancel",
    ),
    # Invoice Line Items
    path(
        "<uuid:invoice_id>/line-items/",
        api_views.InvoiceLineItemListView.as_view(),
        name="invoice_line_items",
    ),
    path(
        "<uuid:invoice_id>/line-items/<uuid:pk>/",
        api_views.InvoiceLineItemDetailView.as_view(),
        name="invoice_line_item_detail",
    ),
    # Invoice Payments
    path(
        "<uuid:invoice_id>/payments/",
        api_views.PaymentListView.as_view(),
        name="invoice_payments",
    ),
    path(
        "<uuid:invoice_id>/payments/<uuid:pk>/",
        api_views.PaymentDetailView.as_view(),
        name="invoice_payment_detail",
    ),
    # Invoice Comments
    path(
        "<uuid:invoice_id>/comments/",
        api_views.InvoiceCommentView.as_view(),
        name="invoice_comments",
    ),
    path(
        "comments/<uuid:pk>/",
        api_views.InvoiceCommentDetailView.as_view(),
        name="invoice_comment_detail",
    ),
    # Invoice Attachments
    path(
        "<uuid:invoice_id>/attachments/",
        api_views.InvoiceAttachmentView.as_view(),
        name="invoice_attachments",
    ),
    path(
        "attachments/<uuid:pk>/",
        api_views.InvoiceAttachmentDetailView.as_view(),
        name="invoice_attachment_detail",
    ),
    # ==========================================================================
    # ESTIMATES
    # ==========================================================================
    path("estimates/", api_views.EstimateListView.as_view(), name="estimate_list"),
    path(
        "estimates/<uuid:pk>/",
        api_views.EstimateDetailView.as_view(),
        name="estimate_detail",
    ),
    path(
        "estimates/<uuid:pk>/convert/",
        api_views.EstimateConvertView.as_view(),
        name="estimate_convert",
    ),
    path(
        "estimates/<uuid:pk>/send/",
        api_views.EstimateSendView.as_view(),
        name="estimate_send",
    ),
    path(
        "estimates/<uuid:pk>/pdf/",
        api_views.EstimatePDFView.as_view(),
        name="estimate_pdf",
    ),
    # ==========================================================================
    # RECURRING INVOICES
    # ==========================================================================
    path(
        "recurring/",
        api_views.RecurringInvoiceListView.as_view(),
        name="recurring_list",
    ),
    path(
        "recurring/<uuid:pk>/",
        api_views.RecurringInvoiceDetailView.as_view(),
        name="recurring_detail",
    ),
    path(
        "recurring/<uuid:pk>/toggle/",
        api_views.RecurringInvoicePauseView.as_view(),
        name="recurring_toggle",
    ),
    # ==========================================================================
    # PRODUCTS
    # ==========================================================================
    path("products/", api_views.ProductListView.as_view(), name="product_list"),
    path(
        "products/<uuid:pk>/",
        api_views.ProductDetailView.as_view(),
        name="product_detail",
    ),
    # ==========================================================================
    # TEMPLATES
    # ==========================================================================
    path(
        "templates/",
        api_views.InvoiceTemplateListView.as_view(),
        name="template_list",
    ),
    path(
        "templates/<uuid:pk>/",
        api_views.InvoiceTemplateDetailView.as_view(),
        name="template_detail",
    ),
    path(
        "templates/<uuid:pk>/editor/",
        api_views.InvoiceTemplateEditorView.as_view(),
        name="template_editor",
    ),
    # ==========================================================================
    # REPORTS
    # ==========================================================================
    path(
        "reports/dashboard/",
        api_views.InvoiceDashboardView.as_view(),
        name="reports_dashboard",
    ),
    path(
        "reports/revenue/",
        api_views.RevenueReportView.as_view(),
        name="reports_revenue",
    ),
    path(
        "reports/aging/",
        api_views.AgingReportView.as_view(),
        name="reports_aging",
    ),
    # ==========================================================================
    # INVOICE FROM OPPORTUNITY
    # ==========================================================================
    path(
        "from-opportunity/<uuid:opportunity_id>/",
        api_views.InvoiceFromOpportunityView.as_view(),
        name="invoice_from_opportunity",
    ),
    # ==========================================================================
    # INVOICE FROM TIME ENTRIES (Tier 3 time-tracking)
    # ==========================================================================
    path(
        "from-time-entries/",
        api_views.InvoiceFromTimeEntriesView.as_view(),
        name="invoice_from_time_entries",
    ),
]
