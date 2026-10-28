import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.db.models import F, Q
from django.template.loader import render_to_string
from django.utils import timezone

from common.links import frontend_url
from common.models import Org, Profile
from common.modules import module_is_available
from common.push import push_configured
from common.tasks import clear_rls_context, set_rls_context
from leads.alerts import notify_waiting
from leads.models import Lead
from leads.response import unanswered

logger = logging.getLogger(__name__)

# A lead that has waited longer than this is not reminded about, so a
# scheduler that starts late cannot send a burst for old leads.
REMINDER_WINDOW = timedelta(hours=24)


@shared_task
def send_email(
    subject,
    html_content,
    text_content=None,
    from_email=None,
    recipients=None,
    attachments=None,
    bcc=None,
    cc=None,
):
    # send email to user with attachment
    if recipients is None:
        recipients = []
    if attachments is None:
        attachments = []
    if bcc is None:
        bcc = []
    if cc is None:
        cc = []
    if not from_email:
        from_email = settings.DEFAULT_FROM_EMAIL
    if not text_content:
        text_content = ""
    email = EmailMultiAlternatives(
        subject, text_content, from_email, recipients, bcc=bcc, cc=cc
    )
    email.attach_alternative(html_content, "text/html")
    for attachment in attachments:
        # Example: email.attach('design.png', img_data, 'image/png')
        email.attach(*attachment)
    email.send()


@shared_task
def send_email_to_assigned_user(recipients, lead_id, org_id, source=""):
    """Send Mail To Users When they are assigned to a lead"""
    set_rls_context(org_id)
    lead = Lead.objects.get(id=lead_id)
    created_by = lead.created_by
    for user in recipients:
        recipients_list = []
        profile = Profile.objects.filter(
            id=user, is_active=True, lead_alerts_email=True
        ).first()
        if profile:
            recipients_list.append(profile.user.email)
            context = {}
            context["url"] = frontend_url(f"/leads/{lead.id}")
            context["user"] = profile.user
            context["lead"] = lead
            context["created_by"] = created_by
            context["source"] = source
            subject = "Assigned a lead for you. "
            html_content = render_to_string(
                "assigned_to/leads_assigned.html", context=context
            )
            msg = EmailMessage(subject, html_content, to=recipients_list)
            msg.content_subtype = "html"
            try:
                msg.send()
            except Exception as e:
                logger.error(
                    "Failed to send lead assignment email to %s: %s",
                    profile.user.email,
                    e,
                )


@shared_task
def send_enquired_again_email(org_id, lead_id=None, contact_id=None, channel_label=""):
    """Tell a lead's active owners, or the owners of the contact a converted
    lead became, that the person enquired again.

    The mail names the person and links to the record. What they wrote is on
    the record as a comment, so it never travels through the task queue.
    """
    from contacts.models import Contact

    set_rls_context(org_id)
    if lead_id:
        record = Lead.objects.filter(id=lead_id, org_id=org_id).first()
        path = f"/leads/{lead_id}"
    else:
        record = Contact.objects.filter(id=contact_id, org_id=org_id).first()
        path = f"/contacts/{contact_id}"
    if record is None:
        return
    owners = record.assigned_to.filter(
        org_id=org_id, is_active=True, lead_alerts_email=True
    ).select_related("user")
    recipients = sorted({p.user.email for p in owners if p.user and p.user.email})
    if not recipients:
        return
    # Collapsed to one line: a name with a newline in it would make the
    # subject an invalid header and the mail would never go.
    name = " ".join(
        " ".join(filter(None, [record.first_name, record.last_name])).split()
    )[:100]
    name = name or "Someone"
    html_content = render_to_string(
        "leads/enquired_again_email.html",
        {
            "name": name,
            "channel_label": channel_label,
            "record_url": frontend_url(path),
            "is_contact": not lead_id,
        },
    )
    send_email(
        subject=f"{name} enquired again",
        html_content=html_content,
        recipients=recipients,
    )


@shared_task
def remind_waiting_leads():
    """Remind about each lead that has waited `Org.lead_reminder_minutes` for
    an answer: its members, else the org's admins (`leads.alerts`).

    Once per arrival, the latest enquiry else the creation, and only for
    arrivals in the last 24 hours and since the reminder was switched on
    (`Org.lead_reminder_since`). The stamp is a conditional update, so a
    late or doubled beat reminds nobody twice.
    """
    now = timezone.now()
    reminded = 0
    pushing = push_configured()
    try:
        orgs = Org.objects.filter(is_active=True, lead_reminder_minutes__isnull=False)
        for org in orgs:
            set_rls_context(org.id)
            try:
                if not module_is_available(org, "leads"):
                    continue
                for lead in _due(org, now):
                    claimed = (
                        Lead.objects.filter(pk=lead.pk, org=org)
                        .filter(
                            Q(waiting_reminded_at__isnull=True)
                            | Q(waiting_reminded_at__lt=lead.arrival_at)
                        )
                        .update(waiting_reminded_at=now)
                    )
                    if claimed:
                        notify_waiting(lead, pushing=pushing)
                        reminded += 1
            except Exception:
                logger.exception("Waiting reminders failed for org=%s", org.id)
    finally:
        clear_rls_context()
    return reminded


def _due(org, now):
    waited = timedelta(minutes=org.lead_reminder_minutes)
    window = now - REMINDER_WINDOW
    # The window again on the stored columns, so older leads drop out before
    # the Coalesce behind `arrival_at` is worked out for them.
    recent = Q(created_at__gt=window) | Q(last_enquiry_at__gt=window)
    return (
        unanswered(Lead.objects.filter(recent, is_active=True), org)
        .filter(arrival_at__lte=now - waited, arrival_at__gt=window)
        .filter(arrival_at__gte=org.lead_reminder_since)
        .filter(
            Q(waiting_reminded_at__isnull=True)
            | Q(waiting_reminded_at__lt=F("arrival_at"))
        )
        .select_related("org")
        .order_by("arrival_at")
    )
