import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.template.loader import render_to_string

from common.links import frontend_url
from common.models import Profile
from common.tasks import set_rls_context
from leads.models import Lead

logger = logging.getLogger(__name__)


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
        profile = Profile.objects.filter(id=user, is_active=True).first()
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
