"""
Celery tasks for Invoice module.

Tasks include:
- Email notifications
- Recurring invoice generation
- Overdue invoice checking
- Payment reminders
- Estimate expiry checking
"""

import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.utils import timezone

from common.links import frontend_url
from common.models import Org, Profile
from common.org_time import activate_org_timezone
from common.tasks import clear_rls_context, set_rls_context

logger = logging.getLogger(__name__)


@shared_task
def send_email(invoice_id, recipients, org_id, domain="localhost", protocol="http"):
    """
    Send notification email when invoice is assigned to users.

    Args:
        invoice_id: UUID of the invoice
        recipients: List of user profile IDs
        org_id: UUID of the organization
        domain: Ignored. Kept so a task already queued with this kwarg still
            unpacks after deploy. Links now come from `common.links.frontend_url`,
            because these paths live on the web app and this argument was
            passed `settings.DOMAIN_NAME`, which names the API and carries its
            own scheme. See that module for the four ways it was wrong.
        protocol: Ignored, for the same reason.
    """
    from invoices.models import Invoice

    set_rls_context(org_id)
    invoice = Invoice.objects.select_related("org").filter(id=invoice_id).first()
    if not invoice:
        logger.warning("Invoice %s not found", invoice_id)
        return

    # "N days overdue" in the email is counted on the customer's calendar.
    activate_org_timezone(invoice.org)

    for user_id in recipients:
        profile = Profile.objects.filter(id=user_id, is_active=True).first()
        if profile and profile.user and profile.user.email:
            subject = f"Invoice #{invoice.invoice_number} has been assigned to you"
            context = {
                "invoice": invoice,
                "invoice_title": invoice.invoice_title,
                "invoice_number": invoice.invoice_number,
                "url": frontend_url(f"/invoices/{invoice.id}"),
                "user": profile.user,
                "assigned_by": invoice.created_by.user if invoice.created_by else None,
            }
            html_content = render_to_string(
                "invoices/emails/assigned_to_email.html", context=context
            )
            msg = EmailMessage(
                subject=subject,
                body=html_content,
                to=[profile.user.email],
            )
            msg.content_subtype = "html"
            try:
                msg.send()
                logger.info(
                    "Sent assignment email for invoice %s to %s",
                    invoice_id,
                    profile.user.email,
                )
            except Exception as e:
                logger.error("Failed to send email: %s", e)


@shared_task
def send_invoice_to_client(
    invoice_id, org_id, domain="localhost", protocol="http", include_pdf=True
):
    """
    Send invoice to client via email with optional PDF attachment.

    Args:
        invoice_id: UUID of the invoice
        org_id: UUID of the organization
        domain: Ignored. Kept so a task already queued with this kwarg still
            unpacks after deploy. Links now come from `common.links.frontend_url`,
            because these paths live on the web app and this argument was
            passed `settings.DOMAIN_NAME`, which names the API and carries its
            own scheme. See that module for the four ways it was wrong.
        protocol: Ignored, for the same reason.
        include_pdf: Whether to attach PDF
    """
    from invoices.models import Invoice
    from invoices.pdf import generate_invoice_filename, generate_invoice_pdf

    set_rls_context(org_id)
    invoice = Invoice.objects.filter(id=invoice_id).first()
    if not invoice:
        logger.warning("Invoice %s not found", invoice_id)
        return

    if not invoice.client_email:
        logger.warning("Invoice %s has no client email", invoice_id)
        return

    subject = f"Invoice #{invoice.invoice_number} from {invoice.org.name}"

    # Build public URL if enabled
    public_url = None
    if invoice.public_link_enabled and invoice.public_token:
        public_url = frontend_url(f"/portal/invoice/{invoice.public_token}")

    context = {
        "invoice": invoice,
        "public_url": public_url,
        "org": invoice.org,
    }
    html_content = render_to_string(
        "invoices/emails/invoice_to_client.html", context=context
    )

    msg = EmailMessage(
        subject=subject,
        body=html_content,
        to=[invoice.client_email],
    )
    msg.content_subtype = "html"

    # Attach PDF if requested
    if include_pdf:
        try:
            pdf_content = generate_invoice_pdf(invoice)
            filename = generate_invoice_filename(invoice)
            msg.attach(filename, pdf_content, "application/pdf")
        except Exception as e:
            logger.error("Failed to generate PDF for invoice %s: %s", invoice_id, e)

    try:
        msg.send()
        # Update invoice
        invoice.is_email_sent = True
        invoice.sent_at = timezone.now()
        if invoice.status == "Draft":
            invoice.status = "Sent"
        invoice.save()
        logger.info("Sent invoice %s to %s", invoice_id, invoice.client_email)
    except Exception as e:
        logger.error("Failed to send invoice email: %s", e)


@shared_task
def create_invoice_history(invoice_id, updated_by_user_id, changed_fields, org_id):
    """
    Create a history record for invoice changes.

    Args:
        invoice_id: UUID of the invoice
        updated_by_user_id: UUID of the profile who made changes
        changed_fields: List of field names that changed
        org_id: UUID of the organization
    """
    from invoices.models import Invoice, InvoiceHistory

    set_rls_context(org_id)
    invoice = Invoice.objects.filter(id=invoice_id).first()
    if not invoice:
        logger.warning("Invoice %s not found", invoice_id)
        return

    updated_by = Profile.objects.filter(id=updated_by_user_id).first()

    # Format changed fields message
    if changed_fields:
        changed_data = [
            (" ".join(field.split("_")).title()) for field in changed_fields
        ]
        if len(changed_data) > 1:
            changed_data = (
                ", ".join(changed_data[:-1])
                + " and "
                + changed_data[-1]
                + " have changed."
            )
        elif len(changed_data) == 1:
            changed_data = changed_data[0] + " has changed."
        else:
            changed_data = None
    else:
        changed_data = None

    # First history entry
    if invoice.invoice_history.count() == 0:
        changed_data = "Invoice created."

    InvoiceHistory.objects.create(
        invoice=invoice,
        invoice_title=invoice.invoice_title,
        invoice_number=invoice.invoice_number,
        total_amount=invoice.total_amount,
        currency=invoice.currency,
        updated_by=updated_by,
        created_by=invoice.created_by,
        amount_due=invoice.amount_due,
        client_name=invoice.client_name,
        client_email=invoice.client_email,
        status=invoice.status,
        details=changed_data,
        due_date=invoice.due_date,
        org=invoice.org,
    )
    logger.info("Created history entry for invoice %s", invoice_id)


@shared_task
def generate_recurring_invoices():
    """
    Generate invoices from active recurring invoice templates.
    Should be scheduled to run daily.
    """
    from invoices.models import Invoice, InvoiceLineItem, RecurringInvoice

    logger.info("Starting recurring invoice generation")

    try:
        for org in Org.objects.all():
            # Set before any org-scoped query: `recurring_invoice` is
            # org-scoped and a worker runs no middleware, so under an RLS-bound
            # role a lookup made with no context matches nothing.
            set_rls_context(org.id)
            # Due "today" on the org's calendar, not the server's: generating a
            # month's invoice a day early because the server is in a different
            # timezone from the customer is a billing error, not a rounding one.
            activate_org_timezone(org)
            today = timezone.localdate()
            recurring_invoices = RecurringInvoice.objects.filter(
                org=org,
                is_active=True,
                next_generation_date__lte=today,
            ).select_related("org", "account", "contact")

            for recurring in recurring_invoices:
                # Check end date
                if recurring.end_date and recurring.end_date < today:
                    recurring.is_active = False
                    recurring.save()
                    logger.info(
                        "Deactivated recurring invoice %s - end date reached",
                        recurring.id,
                    )
                    continue

                try:
                    # Create new invoice
                    invoice = Invoice.objects.create(
                        invoice_title=recurring.title,
                        status="Draft" if not recurring.auto_send else "Sent",
                        account=recurring.account,
                        contact=recurring.contact,
                        client_name=recurring.client_name,
                        client_email=recurring.client_email,
                        discount_type=recurring.discount_type,
                        discount_value=recurring.discount_value,
                        tax_rate=recurring.tax_rate,
                        currency=recurring.currency,
                        issue_date=today,
                        payment_terms=recurring.payment_terms,
                        notes=recurring.notes,
                        terms=recurring.terms,
                        org=recurring.org,
                    )

                    # Copy line items
                    for item in recurring.line_items.all():
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
                            org=recurring.org,
                        )

                    # Recalculate totals
                    invoice.recalculate_totals()
                    invoice.save()

                    # Update recurring invoice
                    recurring.next_generation_date = recurring.calculate_next_date()
                    recurring.invoices_generated += 1
                    recurring.save()

                    logger.info(
                        "Created invoice %s from recurring %s", invoice.id, recurring.id
                    )

                    # Auto-send if enabled
                    if recurring.auto_send:
                        send_invoice_to_client.delay(
                            str(invoice.id),
                            str(recurring.org.id),
                            domain=getattr(settings, "DOMAIN_NAME", "localhost"),
                            protocol="https"
                            if getattr(settings, "USE_HTTPS", False)
                            else "http",
                        )

                except Exception as e:
                    logger.error(
                        "Failed to generate invoice from recurring %s: %s",
                        recurring.id,
                        e,
                    )
    finally:
        # Worker threads are reused between tasks; leaving the last org's
        # timezone or RLS context active would hand it to whatever runs next.
        timezone.deactivate()
        clear_rls_context()

    logger.info("Finished recurring invoice generation")


@shared_task
def check_overdue_invoices():
    """
    Mark invoices as overdue if past due date.
    Should be scheduled to run daily.
    """
    from invoices.models import Invoice

    logger.info("Checking for overdue invoices")

    count = 0
    try:
        for org in Org.objects.all():
            # Set before any org-scoped query: `invoice` is org-scoped and a
            # worker runs no middleware, so under an RLS-bound role a lookup
            # made with no context matches nothing.
            set_rls_context(org.id)
            # A daily sweep across every org cannot use one "today": each org's
            # day starts at a different instant. Marking a New York customer's
            # invoice overdue four hours before their day ends is the kind of
            # thing they email about.
            activate_org_timezone(org)
            overdue_invoices = Invoice.objects.filter(
                org=org,
                due_date__lt=timezone.localdate(),
                status__in=["Sent", "Viewed", "Partially_Paid"],
            )
            for invoice in overdue_invoices:
                invoice.status = "Overdue"
                invoice.save()
                count += 1
                logger.info("Marked invoice %s as overdue", invoice.id)
    finally:
        # Worker threads are reused between tasks; leaving the last org's
        # timezone or RLS context active would hand it to whatever runs next.
        timezone.deactivate()
        clear_rls_context()

    logger.info("Marked %d invoices as overdue", count)


@shared_task
def send_payment_reminder(invoice_id, org_id, domain="localhost", protocol="http"):
    """
    Send a payment reminder for an invoice.

    Args:
        invoice_id: UUID of the invoice
        org_id: UUID of the organization
        domain: Ignored. Kept so a task already queued with this kwarg still
            unpacks after deploy. Links now come from `common.links.frontend_url`,
            because these paths live on the web app and this argument was
            passed `settings.DOMAIN_NAME`, which names the API and carries its
            own scheme. See that module for the four ways it was wrong.
        protocol: Ignored, for the same reason.
    """
    from invoices.models import Invoice

    set_rls_context(org_id)
    invoice = Invoice.objects.filter(id=invoice_id).first()
    if not invoice:
        logger.warning("Invoice %s not found", invoice_id)
        return

    if not invoice.client_email:
        logger.warning("Invoice %s has no client email", invoice_id)
        return

    # Don't send reminders for paid or cancelled invoices
    if invoice.status in ["Paid", "Cancelled"]:
        logger.info(
            "Skipping reminder for %s - status is %s", invoice_id, invoice.status
        )
        return

    subject = f"Payment Reminder: Invoice #{invoice.invoice_number}"

    public_url = None
    if invoice.public_link_enabled and invoice.public_token:
        public_url = frontend_url(f"/portal/invoice/{invoice.public_token}")

    context = {
        "invoice": invoice,
        "public_url": public_url,
        "org": invoice.org,
        "days_overdue": (timezone.localdate() - invoice.due_date).days
        if invoice.due_date
        else 0,
    }
    html_content = render_to_string(
        "invoices/emails/payment_reminder.html", context=context
    )

    msg = EmailMessage(
        subject=subject,
        body=html_content,
        to=[invoice.client_email],
    )
    msg.content_subtype = "html"

    try:
        msg.send()
        invoice.last_reminder_sent = timezone.now()
        invoice.reminder_count += 1
        invoice.save()
        logger.info("Sent payment reminder for invoice %s", invoice_id)
    except Exception as e:
        logger.error("Failed to send payment reminder: %s", e)


@shared_task
def process_payment_reminders():
    """
    Process all invoices that need payment reminders.
    Should be scheduled to run daily.
    """
    from invoices.models import Invoice

    logger.info("Processing payment reminders")

    try:
        for org in Org.objects.all():
            # Set before any org-scoped query: `invoice` is org-scoped and a
            # worker runs no middleware, so under an RLS-bound role a lookup
            # made with no context matches nothing.
            set_rls_context(org.id)
            # "Three days before it is due" has to mean three days on the
            # customer's calendar, not the server's.
            activate_org_timezone(org)
            today = timezone.localdate()

            # Get invoices with reminders enabled
            invoices = (
                Invoice.objects.filter(
                    org=org,
                    reminder_enabled=True,
                    status__in=["Sent", "Viewed", "Partially_Paid", "Overdue"],
                )
                .exclude(client_email__isnull=True)
                .exclude(client_email="")
            )

            for invoice in invoices:
                # Check if we should send a reminder
                should_send = False
                days_until_due = (
                    (invoice.due_date - today).days if invoice.due_date else None
                )

                # Determine reminder interval based on frequency setting
                reminder_interval_days = 7  # default
                if invoice.reminder_frequency == "ONCE":
                    reminder_interval_days = 9999  # effectively only once
                elif invoice.reminder_frequency == "WEEKLY":
                    reminder_interval_days = 7

                # Before due date reminder
                if days_until_due is not None and days_until_due > 0:
                    if (
                        invoice.reminder_days_before
                        and days_until_due <= invoice.reminder_days_before
                    ):
                        # Check if we haven't sent a reminder recently
                        if not invoice.last_reminder_sent or (
                            (
                                today - timezone.localdate(invoice.last_reminder_sent)
                            ).days
                            >= reminder_interval_days
                        ):
                            should_send = True

                # After due date reminder
                elif days_until_due is not None and days_until_due < 0:
                    days_overdue = abs(days_until_due)
                    if (
                        invoice.reminder_days_after
                        and days_overdue >= invoice.reminder_days_after
                    ):
                        # Check reminder frequency
                        if not invoice.last_reminder_sent or (
                            (
                                today - timezone.localdate(invoice.last_reminder_sent)
                            ).days
                            >= reminder_interval_days
                        ):
                            should_send = True

                if should_send:
                    send_payment_reminder.delay(
                        str(invoice.id),
                        str(org.id),
                        domain=getattr(settings, "DOMAIN_NAME", "localhost"),
                        protocol="https"
                        if getattr(settings, "USE_HTTPS", False)
                        else "http",
                    )
    finally:
        # Worker threads are reused between tasks; leaving the last org's
        # timezone or RLS context active would hand it to whatever runs next.
        timezone.deactivate()
        clear_rls_context()

    logger.info("Finished processing payment reminders")


@shared_task
def check_expired_estimates():
    """
    Mark estimates as expired if past valid_until date.
    Should be scheduled to run daily.
    """
    from invoices.models import Estimate

    logger.info("Checking for expired estimates")

    count = 0
    try:
        for org in Org.objects.all():
            # Set before any org-scoped query, same as `check_overdue_invoices`.
            set_rls_context(org.id)
            # Expired on the org's calendar, not the server's.
            activate_org_timezone(org)
            expired_estimates = Estimate.objects.filter(
                org=org,
                expiry_date__lt=timezone.localdate(),
                status__in=["Draft", "Sent", "Viewed"],
            )
            for estimate in expired_estimates:
                estimate.status = "Expired"
                estimate.save()
                count += 1
                logger.info("Marked estimate %s as expired", estimate.id)
    finally:
        timezone.deactivate()
        clear_rls_context()

    logger.info("Marked %d estimates as expired", count)


@shared_task
def send_estimate_to_client(
    estimate_id, org_id, domain="localhost", protocol="http", include_pdf=True
):
    """
    Send estimate to client via email with optional PDF attachment.

    Args:
        estimate_id: UUID of the estimate
        org_id: UUID of the organization
        domain: Ignored. Kept so a task already queued with this kwarg still
            unpacks after deploy. Links now come from `common.links.frontend_url`,
            because these paths live on the web app and this argument was
            passed `settings.DOMAIN_NAME`, which names the API and carries its
            own scheme. See that module for the four ways it was wrong.
        protocol: Ignored, for the same reason.
        include_pdf: Whether to attach PDF
    """
    from invoices.models import Estimate
    from invoices.pdf import generate_estimate_filename, generate_estimate_pdf

    set_rls_context(org_id)
    estimate = Estimate.objects.filter(id=estimate_id).first()
    if not estimate:
        logger.warning("Estimate %s not found", estimate_id)
        return

    if not estimate.client_email:
        logger.warning("Estimate %s has no client email", estimate_id)
        return

    subject = f"Estimate #{estimate.estimate_number} from {estimate.org.name}"

    public_url = None
    if estimate.public_link_enabled and estimate.public_token:
        public_url = frontend_url(f"/portal/estimate/{estimate.public_token}")

    context = {
        "estimate": estimate,
        "public_url": public_url,
        "org": estimate.org,
    }
    html_content = render_to_string(
        "invoices/emails/estimate_to_client.html", context=context
    )

    msg = EmailMessage(
        subject=subject,
        body=html_content,
        to=[estimate.client_email],
    )
    msg.content_subtype = "html"

    if include_pdf:
        try:
            pdf_content = generate_estimate_pdf(estimate)
            filename = generate_estimate_filename(estimate)
            msg.attach(filename, pdf_content, "application/pdf")
        except Exception as e:
            logger.error("Failed to generate PDF for estimate %s: %s", estimate_id, e)

    try:
        msg.send()
        estimate.sent_at = timezone.now()
        if estimate.status == "Draft":
            estimate.status = "Sent"
        estimate.save()
        logger.info("Sent estimate %s to %s", estimate_id, estimate.client_email)
    except Exception as e:
        logger.error("Failed to send estimate email: %s", e)
