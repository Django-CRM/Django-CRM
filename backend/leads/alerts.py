"""Lead alerts: an in-app notification, and a phone push where push is on.

Four verbs, each with the lead as its entity and `/leads/<id>` as its link
(but for a converted lead's repeat enquiry, below):

- `lead.assigned` to each member newly put on a lead, from one hook on
  `Lead.assigned_to` (`m2m_changed`, `post_add`), so every path that assigns
  is covered once. Whoever made the change is not told: the request's
  profile, or the one `update_lead` names. A running automation rule is no
  person, so everyone it adds is told.
- `lead.created` to the org's admins, for a lead that arrived through intake
  (a lead webhook, a lead address or a web form) and still has no active
  member on it a minute later (`notify_created`).
- `lead.enquired` to the assignees when a lead enquires again (`notify_enquired`).
  On a converted lead it goes to the owners of the contact the lead became,
  with the contact as its entity and `/contacts/<id>` as its link.
- `lead.waiting` from the waiting reminder (`notify_waiting`).

Bulk writes (a spreadsheet import, sample data, `seed_data`, a merge, a team's
members joining its leads) run inside `quiet()`, which writes no alert and
sends no push; the existing emails are unaffected.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial

from celery import shared_task
from crum import get_current_request
from django.db import transaction
from django.db.models import Q
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from common import notifications
from common.actor import current_actor
from common.models import DeviceToken, Profile
from common.push import push_configured, send_push
from common.tasks import clear_rls_context, set_rls_context
from leads.models import Lead
from leads.sources import source_labels

ASSIGNED = "lead.assigned"
CREATED = "lead.created"
ENQUIRED = "lead.enquired"
WAITING = "lead.waiting"

PUSH_TITLE = "BottleCRM"
PUSH_TEXT = {
    ASSIGNED: "New lead assigned: {name}{source}",
    CREATED: "New lead: {name}{source}",
    ENQUIRED: "{name} enquired again{source}",
    WAITING: "{name} is waiting for a reply",
}
NAME_MAX = 100
# Long enough for a web form's rotation, and an automation rule, which runs
# after the intake's transaction commits, to put someone on a new lead.
CREATED_DELAY_SECONDS = 60

_quiet = ContextVar("lead_alerts_quiet", default=False)
_assigner = ContextVar("lead_alerts_assigner", default=None)


@contextmanager
def quiet():
    """Write no lead alert and send no push inside the block."""
    token = _quiet.set(True)
    try:
        yield
    finally:
        _quiet.reset(token)


@contextmanager
def assigned_by(profile):
    """Name who is assigning inside the block, for a caller with no request."""
    token = _assigner.set(profile)
    try:
        yield
    finally:
        _assigner.reset(token)


def _one_line(name):
    return " ".join(name.split())[:NAME_MAX]


def display_name(lead):
    """The lead's name on one line, else its company, email or title."""
    full = " ".join(filter(None, (lead.first_name, lead.last_name)))
    name = full or lead.company_name or lead.email or lead.title or "Lead"
    return _one_line(name) or "Lead"


def contact_name(contact):
    """The contact's first and last name on one line, as the contact list
    shows it, else its email."""
    full = " ".join(filter(None, (contact.first_name, contact.last_name)))
    return _one_line(full or contact.email or "") or "Contact"


def source_label(lead):
    if not lead.source:
        return ""
    return source_labels(lead.org).get(lead.source, lead.source)


def push_body(verb, name, source):
    return PUSH_TEXT[verb].format(name=name, source=f" ({source})" if source else "")


def _members(profiles):
    return profiles.filter(is_active=True, user__is_active=True).select_related(
        "user", "org"
    )


def _admins(org_id):
    """`common.permissions.is_org_admin` as a query."""
    admin = Q(role="ADMIN") | Q(user__is_superuser=True)
    return list(_members(Profile.objects.filter(admin, org_id=org_id)))


def _assignees(lead):
    return list(_members(lead.assigned_to.filter(org_id=lead.org_id)))


def _lead_alert(recipients, verb, lead, actor=None, pushing=None):
    link = f"/leads/{lead.pk}"
    _alert(recipients, verb, lead, lead, link, display_name(lead), actor, pushing)


def _alert(recipients, verb, lead, record, link, name, actor=None, pushing=None):
    """Tell `recipients` about `record`, named `name` and opened at `link`,
    with `lead`'s source."""
    if _quiet.get() or not recipients:
        return
    source = source_label(lead)
    if pushing is None:
        pushing = push_configured()
    for profile in recipients:
        notifications.create(
            profile,
            verb,
            actor=actor,
            entity=record,
            entity_name=name,
            link=link,
            data={"source": source},
        )
        if pushing and profile.lead_alerts_push and _has_phone(profile):
            transaction.on_commit(
                partial(
                    send_push.delay,
                    str(profile.pk),
                    PUSH_TITLE,
                    push_body(verb, name, source),
                    link,
                    verb=verb,
                ),
                robust=True,
            )


def _has_phone(profile):
    return DeviceToken.objects.filter(
        org_id=profile.org_id, user_id=profile.user_id
    ).exists()


def _current_assigner():
    if current_actor() is not None:
        return None
    named = _assigner.get()
    if named is not None:
        return named
    return getattr(get_current_request(), "profile", None)


@receiver(m2m_changed, sender=Lead.assigned_to.through)
def lead_assigned(sender, instance, action, reverse, pk_set, **kwargs):
    if action != "post_add" or reverse or not pk_set or _quiet.get():
        return
    assigner = _current_assigner()
    if not isinstance(assigner, Profile) or assigner.org_id != instance.org_id:
        assigner = None
    added = _members(Profile.objects.filter(pk__in=pk_set, org_id=instance.org_id))
    recipients = [p for p in added if assigner is None or p.user_id != assigner.user_id]
    _lead_alert(recipients, ASSIGNED, instance, actor=assigner)


def queue_created(lead):
    """Queue `notify_created` for a new intake lead once it commits."""
    if _quiet.get():
        return
    transaction.on_commit(
        partial(
            notify_created.apply_async,
            (str(lead.pk), str(lead.org_id)),
            countdown=CREATED_DELAY_SECONDS,
        ),
        robust=True,
    )


@shared_task
def notify_created(lead_id, org_id):
    """Tell the admins about an intake lead that still has no active member
    on it, unless it has gone."""
    set_rls_context(org_id)
    try:
        lead = (
            Lead.objects.filter(pk=lead_id, org_id=org_id, is_active=True)
            .select_related("org")
            .first()
        )
        if lead is None or _assignees(lead):
            return
        _lead_alert(_admins(org_id), CREATED, lead)
    finally:
        clear_rls_context()


def notify_enquired(lead, contact=None):
    """A repeat enquiry. With `contact`, the contact a converted lead became,
    only that contact's active owners are told, and nobody when it has none."""
    if contact is None:
        _lead_alert(_assignees(lead), ENQUIRED, lead)
        return
    owners = list(_members(contact.assigned_to.filter(org_id=lead.org_id)))
    link = f"/contacts/{contact.pk}"
    _alert(owners, ENQUIRED, lead, contact, link, contact_name(contact))


def notify_waiting(lead, pushing=None):
    """The waiting reminder: the lead's members, else the org's admins.
    `pushing` is `push_configured()`, read once by a caller alerting many."""
    recipients = _assignees(lead) or _admins(lead.org_id)
    _lead_alert(recipients, WAITING, lead, pushing=pushing)
