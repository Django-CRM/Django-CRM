"""Background work for web forms.

A Celery worker does not run Django middleware, so `set_rls_context` has to be
called explicitly before any ORM query. Without it every query below runs with
an empty `app.current_org` and returns zero rows, which reads as "no submission
found" rather than as a bug.
"""

import logging

from celery import shared_task
from django.template.loader import render_to_string

from common.links import frontend_url
from common.tasks import set_rls_context
from leads.tasks import send_email
from webforms.models import WebFormSubmission
from webforms.service import active_assignee

logger = logging.getLogger(__name__)


@shared_task
def send_webform_submission_email(submission_id, org_id):
    """Tell the form's recipients that a lead or ticket came in.

    Deliberately REPLACES `send_lead_assigned_emails` on this path rather than
    firing alongside it, so an assignee who is also a notify recipient receives
    one email rather than two.

    Rejected submissions notify nobody. Telling an org about every bot that
    hits their form is how they learn to ignore the notification, and then to
    miss the real one.

    `send_email` is called directly rather than through `.delay()`. This is
    already inside a task, so enqueuing a second one buys nothing and would
    only add a hop where the message can be lost.
    """
    set_rls_context(org_id)

    submission = (
        WebFormSubmission.objects.filter(id=submission_id, org_id=org_id)
        .select_related("form", "lead", "case", "form__assign_to")
        .first()
    )
    if submission is None:
        logger.info(
            "Web form submission %s is gone or belongs to another org; "
            "nothing to notify.",
            submission_id,
        )
        return

    if submission.status not in WebFormSubmission.ACCEPTED_STATUSES:
        return

    form = submission.form
    # Active members only: a deactivated one has left, and this mail carries
    # the prospect's details.
    profiles = set(
        form.notify_profiles.filter(is_active=True).select_related("user").all()
    )
    assignee = active_assignee(form)
    if assignee is not None:
        profiles.add(assignee)

    recipients = sorted(
        {
            profile.user.email
            for profile in profiles
            if getattr(profile, "user", None) and profile.user.email
        }
    )
    if not recipients:
        logger.info("Web form %s has no notification recipients.", form.id)
        return

    if submission.case_id:
        record_url = frontend_url(f"/tickets/{submission.case_id}")
        subject = f"New ticket: {form.name}"
    else:
        record_url = frontend_url(f"/leads/{submission.lead_id}")
        subject = f"New submission: {form.name}"
    html_content = render_to_string(
        "webforms/submission_email.html",
        {
            "form": form,
            "submission": submission,
            "lead": submission.lead,
            "case": submission.case,
            "record_url": record_url,
            "is_duplicate": (submission.status == WebFormSubmission.ACCEPTED_DUPLICATE),
        },
    )
    send_email(
        subject=subject,
        html_content=html_content,
        recipients=recipients,
    )
