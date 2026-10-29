"""App members: org members that are a connected app rather than a person.

An app member is a ``Profile`` with ``is_app`` set, an admin, acting through a
personal access token it owns. Its user cannot sign in: no usable password,
inactive, and an address on ``APP_MEMBER_EMAIL_DOMAIN``, which no email ever
reaches (``install_mail_guard``). ``Profile.objects`` leaves app members out, so
every list of people, assignee choice, alert and count leaves them out too;
``Profile.all_objects`` is for code that must find one. Nothing in the core
creates them; an extension does, with ``create_app_member``.
"""

import functools
import uuid
from email.utils import parseaddr

from django.core.mail import EmailMessage
from django.db import transaction

from common.models import Profile, User

# `.invalid` is reserved (RFC 2606), so these addresses can never be delivered.
APP_MEMBER_EMAIL_DOMAIN = "apps.bottlecrm.invalid"


def create_app_member(org, name):
    """A new admin member of ``org`` for a connected app, named ``name``."""
    with transaction.atomic():
        user = User.objects.create_user(
            email=f"app-{uuid.uuid4().hex}@{APP_MEMBER_EMAIL_DOMAIN}",
            password=None,
            name=name,
            is_active=False,
        )
        return Profile.all_objects.create(
            org=org, user=user, role="ADMIN", is_app=True, is_active=True
        )


def deactivate_app_member(profile):
    """Turn ``profile`` off, which refuses its tokens at once."""
    if not profile.is_app:
        raise ValueError("Only an app member is deactivated here.")
    profile.is_active = False
    profile.save(update_fields=["is_active", "updated_by", "updated_at"])


def app_member_name(profile):
    """The name ``profile`` is shown by when it is an app member, else None.

    Where a person's display name is derived from their email, an app member's
    would be its reserved address; callers use this first and keep their own
    rule for people.
    """
    if profile is None or not getattr(profile, "is_app", False):
        return None
    return profile.user.name or None


def is_app_member_address(address):
    """Whether ``address`` (bare, or ``Name <addr>``) is on the app member domain."""
    _, addr = parseaddr(str(address or ""))
    if "@" not in addr:
        return False
    domain = addr.rsplit("@", 1)[1].strip().rstrip(".").lower()
    return domain == APP_MEMBER_EMAIL_DOMAIN


def drop_app_member_recipients(message):
    """Remove every app member address from ``message``'s to, cc and bcc."""
    for field in ("to", "cc", "bcc"):
        addresses = getattr(message, field, None) or []
        setattr(
            message,
            field,
            [a for a in addresses if not is_app_member_address(a)],
        )


def install_mail_guard():
    """Make ``EmailMessage.send`` drop app member recipients before sending.

    Every email the core and enterprise send is an ``EmailMessage`` (or its
    subclass) sent with ``.send()``, so this is the one place they all pass.
    A message left with no recipient is not sent: ``send`` returns 0 for it.
    """
    send = EmailMessage.send
    if getattr(send, "_drops_app_members", False):
        return

    @functools.wraps(send)
    def guarded_send(self, *args, **kwargs):
        drop_app_member_recipients(self)
        return send(self, *args, **kwargs)

    guarded_send._drops_app_members = True
    EmailMessage.send = guarded_send
