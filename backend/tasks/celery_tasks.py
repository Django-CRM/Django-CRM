from celery import shared_task
from django.core.mail import EmailMessage
from django.template.loader import render_to_string

from common.links import frontend_url
from common.models import Profile
from common.tasks import set_rls_context
from tasks.models import BoardTask, Task


def _send_assignment_emails(recipients, org_id, title, created_by, url):
    """One email per active profile of this org in ``recipients``."""
    for profile_id in recipients:
        profile = (
            Profile.objects.filter(id=profile_id, org_id=org_id, is_active=True)
            .select_related("user")
            .first()
        )
        if profile is None or not profile.user.email:
            continue
        html_content = render_to_string(
            "tasks_email_template.html",
            context={
                "task_title": title,
                "task_created_by": created_by,
                "url": url,
                "user": profile.user,
            },
        )
        msg = EmailMessage(
            "Assigned a task for you.", html_content, to=[profile.user.email]
        )
        msg.content_subtype = "html"
        msg.send()


@shared_task
def send_email_to_assigned_user(recipients, task_id, org_id):
    """Send Mail To Users When they are assigned to a task"""
    set_rls_context(org_id)
    task = Task.objects.filter(id=task_id, org_id=org_id).first()
    if task is None:
        return
    _send_assignment_emails(
        recipients,
        org_id,
        task.title,
        task.created_by,
        frontend_url(f"/tasks/{task.id}"),
    )


@shared_task
def send_board_card_email_to_assigned_user(recipients, card_id, org_id):
    """Send Mail To Users When they are assigned to a board card"""
    set_rls_context(org_id)
    card = (
        BoardTask.objects.filter(id=card_id, org_id=org_id)
        .select_related("column")
        .first()
    )
    if card is None:
        return
    _send_assignment_emails(
        recipients,
        org_id,
        card.title,
        card.created_by,
        frontend_url(f"/tasks/board?board={card.column.board_id}"),
    )
