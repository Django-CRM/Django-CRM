"""Writing interactions, and the lead fields that are derived from them.

Every write that can touch a lead ends in `refresh_lead_response`, which
recomputes the lead's first response and last outbound time from the stored
interactions rather than adjusting them, so a delete or a backdated edit can
never leave them stale. Deleting the record an interaction is home to recomputes
its lead too (`interactions.signals`).

Each write locks the lead row before it touches an interaction, the order a
lead merge and a conversion already take, so two writers on one lead queue
instead of deadlocking.
"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Q, Window
from django.utils import timezone

from common.modules import org_of
from common.org_time import resolve_timezone_name
from interactions.models import HOME_CHOICES, OUTBOUND_KINDS, Interaction
from leads.models import Lead

HOME_KEYS = tuple(key for key, _ in HOME_CHOICES)

# A response: an outbound call, meeting, email, WhatsApp or SMS, whoever wrote
# it. A connector logs with no member, and a removed member's logs lose theirs;
# both still answered the lead.
OUTBOUND = Q(direction="outbound", kind__in=OUTBOUND_KINDS)


@transaction.atomic
def log_interaction(
    org,
    *,
    home,
    record,
    kind,
    direction="outbound",
    occurred_at=None,
    logged_by=None,
    origin="manual",
    **fields,
) -> Interaction:
    """Create an interaction homed on `record` (of kind `home`) in `org`.

    `fields` may carry further links (`lead=`, `account=`, ...) and any other
    column. Every record and the logger must belong to `org`.
    """
    if home not in HOME_KEYS:
        raise ValueError(f"Unknown home {home!r}.")
    links = {name: fields.pop(name) for name in HOME_KEYS if name in fields}
    links[home] = record
    for linked in (*links.values(), logged_by):
        if linked is not None and linked.org_id != org.id:
            raise ValueError("An interaction and its records share one org.")
    if links.get("lead") is not None:
        _lock_lead(links["lead"].pk)
    interaction = Interaction.objects.create(
        org=org,
        home=home,
        kind=kind,
        direction=direction,
        occurred_at=occurred_at or timezone.now(),
        logged_by=logged_by,
        origin=origin,
        **links,
        **fields,
    )
    if interaction.lead_id:
        refresh_lead_response(interaction.lead)
    return interaction


@transaction.atomic
def change_interaction(interaction, values) -> Interaction:
    """Save `values` onto `interaction`; its home and links never change here."""
    _lock_lead(interaction.lead_id)
    for name, value in values.items():
        setattr(interaction, name, value)
    interaction.save()
    if interaction.lead_id:
        refresh_lead_response(interaction.lead)
    return interaction


@transaction.atomic
def delete_interaction(interaction) -> None:
    _lock_lead(interaction.lead_id)
    lead = interaction.lead if interaction.lead_id else None
    interaction.delete()
    if lead is not None:
        refresh_lead_response(lead)


@transaction.atomic
def refresh_lead_response(lead) -> list[str]:
    """Recompute `lead`'s response fields from its outbound interactions and
    return the names of those that changed.

    Written with a queryset update, never `save()`, so a recompute sends no
    `lead.updated` webhook and runs no automation rule: integrations hear
    `interaction.created` instead. The lead row is locked before the read, so
    two writes on one lead recompute one after the other and the later one
    sees both.
    """
    stored = (
        Lead.objects.select_for_update()
        .filter(pk=lead.pk)
        .values(
            "created_at",
            "first_response_at",
            "first_response_by_id",
            "last_outbound_at",
            "last_contacted",
        )
        .first()
    )
    if stored is None:
        return []
    first = (
        Interaction.objects.filter(OUTBOUND, org_id=lead.org_id, lead_id=lead.pk)
        .annotate(latest=Window(Max("occurred_at")))
        .order_by("occurred_at", "created_at", "pk")
        .values("occurred_at", "logged_by_id", "latest")
        .first()
    )

    wanted = {
        "first_response_at": None,
        "first_response_by_id": None,
        "last_outbound_at": None,
    }
    if first is not None:
        # Never before the lead existed: a call logged with an earlier time is
        # a response of zero, and the lead does not read as still waiting.
        created = stored["created_at"]
        wanted = {
            "first_response_at": max(first["occurred_at"], created),
            "first_response_by_id": first["logged_by_id"],
            "last_outbound_at": max(first["latest"], created),
        }
        day = timezone.localtime(
            wanted["last_outbound_at"], _org_zone(org_of(lead))
        ).date()
        if stored["last_contacted"] is None or day > stored["last_contacted"]:
            wanted["last_contacted"] = day

    changed = {name: v for name, v in wanted.items() if stored[name] != v}
    if changed:
        Lead.objects.filter(pk=lead.pk).update(updated_at=timezone.now(), **changed)
    for name, value in wanted.items():
        setattr(lead, name, value)
    return [name.removesuffix("_id") for name in changed]


def _lock_lead(lead_id):
    if lead_id is not None:
        list(Lead.objects.select_for_update().filter(pk=lead_id).values_list("pk"))


def _org_zone(org):
    try:
        return ZoneInfo(resolve_timezone_name(org))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(settings.TIME_ZONE)
