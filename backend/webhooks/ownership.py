"""Pausing a webhook whose creator can no longer answer for it.

A webhook is a standing export of the org's records to a URL its creator
chose, and it keeps sending after that person has lost the standing to choose
it. So when the creator stops being an org admin, is deactivated (their
profile or their user), or leaves the org, every endpoint they created is
paused: turned off, with the reason shown beside it, and a `WEBHOOK_PAUSED`
row written to the security audit log. Any admin can turn it back on, and
doing so makes that admin its creator (`webhooks.views.WebhookDetailView`).

TWO LAYERS, ONE QUESTION
The receivers below pause at the moment of the change, so the settings page
shows it at once. Signals cannot see a queryset `update()`, raw SQL, or a
`created_by` nulled by a user delete, so `webhooks.tasks.attempt_delivery`
asks the same question (`creator_problem`) before every send and pauses there
too. Whichever path changed the creator, nothing more is sent.

RLS
A user can be deactivated from the Django admin, which runs with no org
context, and a user can belong to several orgs, only one of which is the
request's. Each lookup therefore runs as the endpoint's own org and puts the
previous context back afterwards (`_as_org`), and filters on that org as well.
"""

from contextlib import contextmanager

from crum import get_current_request
from django.db import connection
from django.db.models.signals import post_save, pre_delete

from common.audit_log import audit_log
from common.models import Org, Profile, User
from common.permissions import is_org_admin
from common.tasks import set_rls_context
from webhooks.models import WebhookEndpoint

NOT_ADMIN = "Paused because the admin who created it is no longer an admin."
DEACTIVATED = "Paused because the admin who created it was deactivated."
LEFT = "Paused because the admin who created it left the organization."
NOT_MEMBER = (
    "Paused because the person who created it is not a member of this organization."
)
DELETED_USER = "Paused because the user who created it was deleted."


def _problem(user, profile):
    """Why `user`, holding `profile` in the endpoint's org, cannot own it, or
    None when they can."""
    if user is None:
        return DELETED_USER
    if profile is None:
        return NOT_MEMBER
    if not user.is_active or not profile.is_active:
        return DEACTIVATED
    if not is_org_admin(profile):
        return NOT_ADMIN
    return None


def creator_problem(endpoint):
    """Why the endpoint's creator can no longer own it, or None.

    No creator means that user was deleted: nothing else empties the column.
    A creator with no profile here was never a member, or their profile went
    by a path no receiver saw.
    """
    user = endpoint.created_by
    profile = None
    if user is not None:
        profile = Profile.objects.filter(user=user, org_id=endpoint.org_id).first()
        if profile is not None:
            profile.user = user
    return _problem(user, profile)


def pause(endpoints, reason, creator_gone=False):
    """Turn each still-active endpoint off with `reason` and audit it.

    The conditional update means two paths pausing the same endpoint at once
    write one audit row, not two. `creator_gone` is set while the creator's
    user row is being deleted: an audit row pointing at it would break that
    delete, so the row names them by id in its metadata instead.
    """
    request = get_current_request()
    for endpoint in endpoints:
        paused = WebhookEndpoint.objects.filter(pk=endpoint.pk, is_active=True).update(
            is_active=False, disabled_reason=reason
        )
        if paused:
            audit_log.webhook_paused(
                endpoint,
                reason,
                creator=None if creator_gone else endpoint.created_by,
                request=request,
            )


@contextmanager
def _as_org(org_id):
    """Run the block with the RLS context set to `org_id`, then restore it."""
    if connection.vendor != "postgresql":
        yield
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.current_org', true)")
        previous = cursor.fetchone()[0] or ""
    set_rls_context(org_id)
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.current_org', %s, false)", [previous]
            )


def _pause_created_by(user_id, org_id, reason, creator_gone=False):
    with _as_org(org_id):
        endpoints = list(
            WebhookEndpoint.objects.filter(
                org_id=org_id, created_by_id=user_id, is_active=True
            ).select_related("org", "created_by")
        )
        pause(endpoints, reason, creator_gone=creator_gone)


def _deleting(origin, model):
    """Whether the delete that sent this signal was called on `model`, one
    instance or a queryset of them."""
    return isinstance(origin, model) or getattr(origin, "model", None) is model


def profile_saved(sender, instance, created, raw=False, **kwargs):
    """A role change or a deactivation, from any view, serializer or script."""
    if raw or created:
        return
    reason = _problem(instance.user, instance)
    if reason:
        _pause_created_by(instance.user_id, instance.org_id, reason)


def profile_deleting(sender, instance, **kwargs):
    """Removed from the org, directly or as part of deleting the user.

    `pre_delete`, not `post_delete`: deleting a user nulls `created_by` on
    their endpoints before any `post_delete` is sent, and by then there is
    nothing left to find them by. Inside the delete's transaction, so a delete
    that fails leaves the endpoints as they were.

    Deleting the org deletes its endpoints too, so there is nothing to pause,
    and an audit row naming that org would break the delete.
    """
    origin = kwargs.get("origin")
    if _deleting(origin, Org):
        return
    user_deleted = _deleting(origin, User)
    _pause_created_by(
        instance.user_id,
        instance.org_id,
        DELETED_USER if user_deleted else LEFT,
        creator_gone=user_deleted,
    )


def user_saved(sender, instance, created, raw=False, update_fields=None, **kwargs):
    """A user deactivated, or stripped of superuser, in every org at once.

    `is_org_admin` may admit a superuser whatever their role, so losing the
    flag can end their standing as an admin.
    """
    if raw or created:
        return
    if update_fields is not None and not {"is_active", "is_superuser"} & set(
        update_fields
    ):
        return
    for profile in Profile.objects.filter(user=instance):
        profile.user = instance
        reason = _problem(instance, profile)
        if reason:
            _pause_created_by(instance.pk, profile.org_id, reason)


def connect():
    post_save.connect(
        profile_saved, sender="common.Profile", dispatch_uid="webhooks_profile_saved"
    )
    pre_delete.connect(
        profile_deleting,
        sender="common.Profile",
        dispatch_uid="webhooks_profile_deleting",
    )
    post_save.connect(
        user_saved, sender="common.User", dispatch_uid="webhooks_user_saved"
    )


connect()
