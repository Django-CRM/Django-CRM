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

logger = logging.getLogger(__name__)


@shared_task
def send_webform_submission_email(submission_id, org_id):
    """Tell the form's recipients that a lead came in.

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
        .select_related("form", "lead", "form__assign_to")
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
    profiles = set(form.notify_profiles.select_related("user").all())
    if form.assign_to is not None:
        profiles.add(form.assign_to)

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

    html_content = render_to_string(
        "webforms/submission_email.html",
        {
            "form": form,
            "submission": submission,
            "lead": submission.lead,
            "lead_url": frontend_url(f"/leads/{submission.lead_id}"),
            "is_duplicate": (submission.status == WebFormSubmission.ACCEPTED_DUPLICATE),
        },
    )
    send_email(
        subject=f"New submission: {form.name}",
        html_content=html_content,
        recipients=recipients,
    )
