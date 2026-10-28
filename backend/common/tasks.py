import logging
import uuid
from datetime import timedelta

from botocore.exceptions import ClientError
from celery import shared_task
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.db import connection
from django.template.loader import render_to_string
from django.utils import timezone

from common.links import frontend_url
from common.models import (
    MagicLinkToken,
    Notification,
    Org,
    PortalLoginToken,
    Profile,
    Teams,
    User,
)
from contacts.models import Contact

logger = logging.getLogger(__name__)


def set_rls_context(org_id):
    """
    Set RLS context for Celery tasks that query org-scoped tables.

    Celery workers don't go through Django middleware, so RLS context
    must be set explicitly before querying org-scoped data.

    Args:
        org_id: Organization UUID (string or UUID object)
    """
    # SQLite (test backend) has no set_config(). RLS is a Postgres feature.
    if connection.vendor != "postgresql":
        return
    if org_id:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.current_org', %s, false)", [str(org_id)]
            )


def clear_rls_context():
    """Drop the session-scoped RLS context set by `set_rls_context`.

    Between worker tasks this is a second guard: Celery's Django fixup already
    closes every connection around each task (test_periodic_tasks_rls.py). It is
    the only guard where the connection lives on, such as a streamed response
    that runs after the middleware's reset.
    """
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_org', '', false)")


@shared_task
def send_welcome_email(user_id):
    """Send welcome email to newly created users."""
    user_obj = User.objects.filter(id=user_id).first()
    if not user_obj:
        return

    email = user_obj.email.strip()
    try:
        validate_email(email)
    except ValidationError:
        logger.warning("Welcome email skipped: invalid email for user %s", user_id)
        return

    context = {"url": settings.FRONTEND_URL}
    subject = "Welcome to BottleCRM"
    html_content = render_to_string("welcome_email.html", context=context)

    msg = EmailMessage(
        subject,
        html_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    msg.content_subtype = "html"
    try:
        msg.send()
    except (ClientError, OSError):
        logger.exception("Failed to send welcome email for user %s", user_id)


@shared_task
def send_magic_link_email(token_id, raw_code=None):
    """Send a magic-link or OTP-code email for passwordless authentication.

    For `delivery == "code"` rows, the caller passes `raw_code` (the plaintext
    OTP), only the hash is stored on the token row, so the plaintext can't be
    recovered from the DB by this task.
    """
    magic_token = MagicLinkToken.objects.filter(id=token_id).first()
    if not magic_token:
        return

    email = magic_token.email.strip()
    try:
        validate_email(email)
    except ValidationError:
        logger.warning(
            "Magic link skipped: invalid email format for token %s", token_id
        )
        return

    if magic_token.delivery == "code":
        if not raw_code:
            logger.warning(
                "Magic link skipped: raw_code missing for code-delivery token %s",
                token_id,
            )
            return
        subject = f"Your BottleCRM sign-in code: {raw_code}"
        html_content = render_to_string(
            "magic_link_code_email.html",
            {"code": raw_code},
        )
    else:
        magic_link_url = (
            f"{settings.FRONTEND_URL}/login/verify?token={magic_token.token}"
        )
        subject = "Your BottleCRM sign-in link"
        html_content = render_to_string(
            "magic_link_email.html",
            {"magic_link_url": magic_link_url},
        )

    msg = EmailMessage(
        subject,
        html_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    msg.content_subtype = "html"
    try:
        msg.send()
    except (ClientError, OSError):
        logger.exception("Failed to send email for magic link token %s", token_id)


@shared_task
def send_portal_login_email(token_id, raw_code):
    """Deliver a customer portal sign-in code.

    Runs in a worker, so it sets its own RLS context before reading the
    org-scoped contact. The org comes off the token row, which is unscoped and
    therefore readable with no context at all; that is the whole reason
    PortalLoginToken is not in ORG_SCOPED_TABLES.

    The code is passed in rather than read from the row because the row only
    ever holds its hash.
    """
    if not raw_code:
        logger.warning("Portal login skipped: no code for token %s", token_id)
        return None

    token_obj = PortalLoginToken.objects.filter(id=token_id).first()
    if not token_obj:
        return None

    set_rls_context(str(token_obj.org_id))

    contact = Contact.objects.filter(id=token_obj.contact_id).first()
    if contact is None or not contact.email:
        logger.warning("Portal login skipped: no contact email for token %s", token_id)
        return None

    email = contact.email.strip()
    try:
        validate_email(email)
    except ValidationError:
        logger.warning(
            "Portal login skipped: invalid email format for token %s", token_id
        )
        return None

    org_name = token_obj.org.name or ""
    html_content = render_to_string(
        "portal/login_email.html",
        {
            "org_name": org_name,
            "contact_name": contact.first_name or "",
            "code": raw_code,
        },
    )

    msg = EmailMessage(
        # The code is in the subject so it shows in a phone's notification
        # preview, which is where the customer is reading it. Same trade the
        # internal magic-link mail already makes.
        f"Your sign-in code: {raw_code}",
        html_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    msg.content_subtype = "html"
    try:
        msg.send()
    except (ClientError, OSError):
        logger.exception("Failed to send email for portal login token %s", token_id)
    return str(token_obj.id)


@shared_task
def send_email_user_status(
    user_id,
    status_changed_user="",
):
    """Send Mail To Users Regarding their status i.e active or inactive"""
    user = User.objects.filter(id=user_id).first()
    if user:
        context = {}
        context["message"] = "deactivated"
        context["email"] = user.email
        # The web app's root is the dashboard. There is no `/marketing` route,
        # and `has_marketing_access` is a profile flag rather than a user one,
        # so the branch that appended it could never have been reached from a
        # `User` in the first place.
        context["url"] = frontend_url("/")
        if user.is_active:
            context["message"] = "activated"
        context["status_changed_user"] = status_changed_user
        if context["message"] == "activated":
            subject = "Account Activated "
            html_content = render_to_string(
                "user_status_activate.html", context=context
            )
        else:
            subject = "Account Deactivated "
            html_content = render_to_string(
                "user_status_deactivate.html", context=context
            )
        recipients = []
        recipients.append(user.email)
        if recipients:
            msg = EmailMessage(
                subject,
                html_content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=recipients,
            )
            msg.content_subtype = "html"
            msg.send()


@shared_task
def send_email_user_delete(
    user_email,
    deleted_by="",
):
    """Send Mail To Users When their account is deleted"""
    if user_email:
        context = {}
        context["message"] = "deleted"
        context["deleted_by"] = deleted_by
        context["email"] = user_email
        recipients = []
        recipients.append(user_email)
        subject = "CRM : Your account is Deleted. "
        html_content = render_to_string("user_delete_email.html", context=context)
        if recipients:
            msg = EmailMessage(
                subject,
                html_content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=recipients,
            )
            msg.content_subtype = "html"
            msg.send()


#: Each kind of record a team carries: the team's related name on it, and the
#: field holding the people a team membership puts on the record.
TEAM_RECORDS = (
    ("account_teams", "assigned_to"),
    ("contact_teams", "assigned_to"),
    ("lead_teams", "assigned_to"),
    ("opportunity_teams", "assigned_to"),
    ("cases_teams", "assigned_to"),
    ("document_teams", "shared_to"),
    ("tasks_teams", "assigned_to"),
    ("invoices_teams", "assigned_to"),
)


def _org_team(team_id, org_id):
    if not org_id:
        return None
    return Teams.objects.filter(id=team_id, org_id=org_id).first()


def _team_records(team):
    """``(record, people manager, ids of the people on it)`` for every record
    of the team's own org that the team is on."""
    for related, people in TEAM_RECORDS:
        records = (
            getattr(team, related)
            .filter(org_id=team.org_id)
            .prefetch_related(people, "teams")
        )
        for record in records:
            manager = getattr(record, people)
            yield record, manager, {profile.id for profile in manager.all()}


def _uuid_strings(values):
    ids = []
    for value in values or ():
        if not isinstance(value, str):
            continue
        try:
            ids.append(uuid.UUID(value))
        except ValueError:
            continue
    return ids


@shared_task
def remove_users(removed_users_list, team_id, org_id=None):
    """Take the people who left a team, active or not, off the team's records,
    except a record where one of its teams still includes them. That counts
    this team too: a removal that runs after the person rejoined strips nothing.
    """
    set_rls_context(org_id)
    try:
        team = _org_team(team_id, org_id)
        ids = _uuid_strings(removed_users_list)
        if team is None or not ids:
            return
        leaving = list(Profile.objects.filter(id__in=ids, org_id=team.org_id))
        teams_of = {
            profile.id: set(
                profile.user_teams.filter(org_id=team.org_id).values_list(
                    "id", flat=True
                )
            )
            for profile in leaving
        }
        for record, manager, on_record in _team_records(team):
            record_teams = {other.id for other in record.teams.all()}
            drop = [
                profile
                for profile in leaving
                if profile.id in on_record and not teams_of[profile.id] & record_teams
            ]
            if drop:
                manager.remove(*drop)
    finally:
        clear_rls_context()


@shared_task
def update_team_users(team_id, org_id=None):
    """Put every active member of a team on each of the team's records."""
    from leads.alerts import quiet

    set_rls_context(org_id)
    try:
        team = _org_team(team_id, org_id)
        if team is None:
            return
        members = list(team.users.filter(org_id=team.org_id, is_active=True))
        # Joining a team that holds hundreds of leads is one change, not
        # hundreds of lead alerts.
        with quiet():
            for _record, manager, on_record in _team_records(team):
                missing = [m for m in members if m.id not in on_record]
                if missing:
                    manager.add(*missing)
    finally:
        clear_rls_context()


# Default cutoff for purging read notifications. Per
# `docs/cases/tier2/in-app-notifications.md` "Storage growth".
NOTIFICATION_PURGE_DAYS = 90


@shared_task
def purge_read_notifications(days=NOTIFICATION_PURGE_DAYS):
    """Delete already-read notifications older than ``days`` days.

    Schedule via celery-beat (recommended cadence: nightly). Walks orgs and sets
    the RLS context for each, because `notification` is org-scoped.

    THIS USED TO DELETE NOTHING IN PRODUCTION. The previous version ran one
    unscoped DELETE and justified it in its own docstring: "RLS does not need a
    per-org context here because the query targets `read_at`, which is intrinsic
    to the row". That is not how RLS works. A policy filters which rows the
    statement can see at all, whatever the WHERE clause names, and the isolation
    policy is `org_id::text = NULLIF(current_setting('app.current_org', true),
    '')`, which matches nothing when the context is empty. A Celery worker runs
    no middleware, so the context was always empty, and under the non-superuser
    role the deployment docs mandate, the nightly DELETE removed zero rows every
    night while logging nothing (it only logs when `deleted` is truthy).

    It passed its tests because `crm.test_settings` is SQLite, which has no RLS
    at all. `set_rls_context` returns early on any non-Postgres vendor, so the
    per-org loop is a no-op there and the tests still describe real behaviour on
    both backends.

    The context is cleared at the end. It is set at SESSION scope, so leaving
    the last org's id on a pooled connection would hand that tenant's context to
    whatever borrows the connection next.
    """
    cutoff = timezone.now() - timedelta(days=days)
    deleted = 0
    try:
        for org_id in Org.objects.values_list("id", flat=True).iterator():
            set_rls_context(org_id)
            removed, _ = Notification.objects.filter(
                read_at__isnull=False, read_at__lt=cutoff
            ).delete()
            deleted += removed
    finally:
        clear_rls_context()
    if deleted:
        logger.info("Purged %s read notifications older than %s days", deleted, days)
    return deleted


@shared_task
def flush_expired_refresh_tokens():
    """Delete refresh-token bookkeeping rows whose tokens have already expired.

    `OrgAwareTokenRefreshView` rotates refresh tokens, so simplejwt writes one
    `OutstandingToken` row per token minted and one `BlacklistedToken` row per
    rotation. An expired token is rejected on its `exp` claim regardless of
    blacklist state, so those rows stop carrying security value once
    `expires_at` passes, and would otherwise grow unbounded, one row per login
    and per refresh. Deleting the outstanding row cascades to its blacklist
    entry.

    Equivalent to simplejwt's `manage.py flushexpiredtokens`; schedule via
    celery-beat (nightly is plenty). Not org-scoped; `expires_at` is intrinsic
    to the row, so no RLS context is needed.

    Returns the number of outstanding-token rows removed.
    """
    from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

    expired = OutstandingToken.objects.filter(expires_at__lte=timezone.now())
    count = expired.count()
    if count:
        expired.delete()
        logger.info("Flushed %s expired refresh token records", count)
    return count
