"""Won and lost: when each lead closed either way, and each person's outcomes.

A lead is won when its status is converted or its stage is a won stage, lost
when its status is closed or its stage is a lost stage, and open otherwise.
`Lead.save()` calls `stamp`, so every path that changes a lead's status or
stage records the moment. The rules are spelled out in `docs/api/leads.md`
("Outcomes").
"""

from django.db.models import Count, Q
from django.utils import timezone

from leads.models import Lead, LeadStage
from leads.response import _median_in_python, window

#: Where an instance keeps the (status, stage_id) its row holds, or None when
#: it does not know: an unsaved lead, or one loaded without either field.
_SAVED = "_outcome_saved"


def outcome_of(status, stage_id):
    """``"won"``, ``"lost"`` or None (open). Won wins when both apply."""
    if status == "converted":
        return "won"
    stage_type = None
    if stage_id:
        stage_type = (
            LeadStage.objects.filter(pk=stage_id)
            .values_list("stage_type", flat=True)
            .first()
        )
    if stage_type == "won":
        return "won"
    if status == "closed" or stage_type == "lost":
        return "lost"
    return None


def _row_pair(lead, fields=None):
    """The (status, stage_id) the row holds once ``fields`` (None: every
    loaded field) have been written or read."""
    if "status" not in lead.__dict__ or "stage_id" not in lead.__dict__:
        return None
    pair = (lead.status, lead.stage_id)
    previous = getattr(lead, _SAVED, None)
    if fields is None or previous is None:
        return pair
    names = set(fields)
    return (
        pair[0] if "status" in names else previous[0],
        pair[1] if names & {"stage", "stage_id"} else previous[1],
    )


def remember(lead, fields=None):
    """Note what the row holds after a load, a refresh or a save of ``fields``."""
    setattr(lead, _SAVED, _row_pair(lead, fields))


def stamp(lead, save_kwargs):
    """Before a save: when it changes the lead's outcome, stamp the one that
    now applies and clear the other, or clear both when the lead reopens."""
    before = getattr(lead, _SAVED, None)
    fields = save_kwargs.get("update_fields")
    after = _row_pair(lead, fields)
    if before is None or after is None or before == after:
        return
    now = outcome_of(*after)
    if now == outcome_of(*before):
        return
    moment = timezone.now()
    lead.won_at = moment if now == "won" else None
    lead.lost_at = moment if now == "lost" else None
    if fields is not None:
        save_kwargs["update_fields"] = {*fields, "won_at", "lost_at"}


def _numbers(won, lost, new_leads, middle):
    return {
        "won": won,
        "lost": lost,
        "win_rate": round(100 * won / (won + lost)) if won + lost else None,
        "median_seconds_to_win": (
            None if middle is None else int(middle.total_seconds())
        ),
        "new_leads": new_leads,
    }


def lead_outcomes(org, profiles, days, *, totals):
    """One row per profile in ``profiles`` (all of them members of ``org``)
    over the last ``days`` days of the org's calendar, today included, and the
    org's totals when ``totals`` is true, else None.

    A row counts the leads assigned to its person now, from the M2M's own
    table, so a lead with several assignees counts once for each of them. The
    totals count each of the org's leads once, assigned or not.
    """
    since, start = window(days)
    won = Q(lead__won_at__gte=start)
    lost = Q(lead__lost_at__gte=start)
    new = Q(lead__created_at__gte=start)
    assigned = Lead.assigned_to.through.objects.filter(
        profile_id__in=[profile.id for profile in profiles], lead__org=org
    )
    counts = {
        row["profile_id"]: row
        for row in assigned.filter(won | lost | new)
        .order_by()
        .values("profile_id")
        .annotate(
            won=Count("lead_id", filter=won),
            lost=Count("lead_id", filter=lost),
            new_leads=Count("lead_id", filter=new),
        )
    }
    medians = _median_in_python(
        assigned.filter(won), "profile_id", "lead__created_at", "lead__won_at"
    )

    rows = []
    for profile in profiles:
        count = counts.get(profile.id, {})
        rows.append(
            {
                "profile": str(profile.id),
                "name": profile.user.name or profile.user.email,
                **_numbers(
                    count.get("won", 0),
                    count.get("lost", 0),
                    count.get("new_leads", 0),
                    medians.get(profile.id),
                ),
            }
        )
    rows.sort(key=lambda row: (row["name"].casefold(), row["profile"]))

    summary = None
    if totals:
        leads = Lead.objects.filter(org=org)
        count = leads.order_by().aggregate(
            won=Count("pk", filter=Q(won_at__gte=start)),
            lost=Count("pk", filter=Q(lost_at__gte=start)),
            new_leads=Count("pk", filter=Q(created_at__gte=start)),
        )
        middle = _median_in_python(
            leads.filter(won_at__gte=start), "org", "created_at", "won_at"
        ).get(org.id)
        summary = _numbers(count["won"], count["lost"], count["new_leads"], middle)
    return {"since": since, "rows": rows, "totals": summary}
