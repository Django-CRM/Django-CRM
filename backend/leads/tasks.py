import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.db.models import Q
from django.template.loader import render_to_string

from common.links import frontend_url
from common.models import Profile
from common.tasks import set_rls_context
from leads.models import Lead

logger = logging.getLogger(__name__)


def get_rendered_html(template_name, context=None):
    if context is None:
        context = {}
    html_content = render_to_string(template_name, context)
    return html_content


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
def send_lead_assigned_emails(lead_id, new_assigned_to_list, org_id):
    set_rls_context(org_id)
    lead_instance = Lead.objects.filter(
        ~Q(status="converted"), pk=lead_id, is_active=True
    ).first()
    if not (lead_instance and new_assigned_to_list):
        return False

    users = Profile.objects.filter(id__in=new_assigned_to_list).distinct()
    subject = f"Lead '{lead_instance}' has been assigned to you"
    from_email = settings.DEFAULT_FROM_EMAIL
    template_name = "assigned_to/leads_assigned.html"

    # The `site_address` argument this used to take was built by its one caller
    # from `request.META["HTTP_HOST"]`, on an endpoint an unauthenticated web
    # form posts to. That named the API host, where `/leads/<id>` does not
    # exist, and it put a client-supplied value into the link of an email this
    # system sends to its own users.
    context = {
        # `lead`, not `lead_instance`: the template's two senders used
        # different names for the same object and it hedged with
        # `{{ lead.title|default:lead_instance }}`. Django resolves a filter's
        # argument eagerly, so that expression raised `VariableDoesNotExist`
        # out of `render_to_string` for whichever sender supplied `lead`,
        # which is the one that runs on an ordinary assignment. That email
        # never rendered, let alone sent.
        "lead": lead_instance,
        "url": frontend_url(f"/leads/{lead_instance.id}"),
    }
    mail_kwargs = {"subject": subject, "from_email": from_email}
    for profile in users:
        if profile.user.email:
            context["user"] = profile.user
            html_content = get_rendered_html(template_name, context)
            mail_kwargs["html_content"] = html_content
            mail_kwargs["recipients"] = [profile.user.email]
            send_email.delay(**mail_kwargs)
    return None


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
