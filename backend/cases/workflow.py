"""
Case workflow configuration.

Defines SLA defaults by priority.
"""

# Terminal statuses
TERMINAL_STATUSES = {"Closed", "Rejected", "Duplicate"}

# Statuses that require closed_on date
CLOSED_DATE_REQUIRED_STATUSES = {"Closed"}

# Default SLA by priority (in hours)
DEFAULT_FIRST_RESPONSE_SLA = {
    "Low": 24,
    "Normal": 8,
    "High": 4,
    "Urgent": 1,
}

DEFAULT_RESOLUTION_SLA = {
    "Low": 72,
    "Normal": 48,
    "High": 24,
    "Urgent": 4,
}

# How long a customer who writes back after the first reply should wait for the
# next one. Same promise as the first reply by default: a follow-up question is
# not less urgent than the opening one.
DEFAULT_NEXT_RESPONSE_SLA = {
    "Low": 24,
    "Normal": 8,
    "High": 4,
    "Urgent": 1,
}

# Upper bound on a configured SLA target. `business_hours.calendar` walks
# forward a day at a time and gives up after 5 years, so a target it cannot
# reach would come back as a junk deadline rather than an error. 8760 business
# hours is 4.2 years against a stock 40-hour week, which stays inside that cap
# while being far longer than any support promise worth writing down.
MAX_SLA_HOURS = 8760

# How much of a target has to be left for a case to still count as on track.
# Below this fraction it is "at risk": the amber band between on-track and
# breached, which is what makes the indicator actionable instead of a postmortem.
SLA_AT_RISK_FRACTION = 0.25


def resolve_sla_targets(org_id, priority):
    """Return ``(first_response_hours, resolution_hours)`` for an org+priority.

    An active ``EscalationPolicy`` for that priority supplies either target.
    The two fall back to the tables above independently, so an org that
    configured only a first-response promise keeps the stock resolution one.

    Returns the plain defaults when ``org_id`` is missing or no active policy
    matches, which is also what an empty RLS context produces: the filter finds
    no rows, and falling back is the fail-safe answer.
    """
    first = DEFAULT_FIRST_RESPONSE_SLA.get(priority, 4)
    resolution = DEFAULT_RESOLUTION_SLA.get(priority, 24)
    if not org_id:
        return first, resolution

    from cases.models import EscalationPolicy

    policy = (
        EscalationPolicy.objects.filter(
            org_id=org_id, priority=priority, is_active=True
        )
        .only("first_response_hours", "resolution_hours")
        .first()
    )
    if policy is None:
        return first, resolution
    return (
        first if policy.first_response_hours is None else policy.first_response_hours,
        resolution if policy.resolution_hours is None else policy.resolution_hours,
    )


def resolve_next_response_targets(org_id):
    """Return ``{priority: next_response_hours}`` for every priority of an org.

    Unlike the first-response and resolution targets, this one is not stamped
    on the case: a ticket can wait on a next reply many times, so there is no
    single deadline to store at creation. The analytics read it from the org's
    active ``EscalationPolicy`` rows when they score the waits, falling back to
    ``DEFAULT_NEXT_RESPONSE_SLA`` per priority, exactly as
    ``resolve_sla_targets`` falls back. One query for all priorities.
    """
    targets = dict(DEFAULT_NEXT_RESPONSE_SLA)
    if not org_id:
        return targets

    from cases.models import EscalationPolicy

    for priority, hours in EscalationPolicy.objects.filter(
        org_id=org_id, is_active=True, next_response_hours__isnull=False
    ).values_list("priority", "next_response_hours"):
        targets[priority] = hours
    return targets


DUPLICATE_BY_MERGE_ONLY = (
    "A ticket becomes Duplicate only by merging it into another ticket."
)


def duplicate_refusal(old_status, new_status):
    """Refuse moving a ticket into Duplicate by anything but a merge.

    Duplicate hides a ticket from the list and the board, and a merge is what
    records where it went (`merged_into`) and moves its comments, attachments
    and email thread over. Set by an edit, a board move or a stage mapped to
    it, the ticket just vanished. The merge endpoint writes the status itself
    and never comes through here. A ticket already Duplicate may still be
    edited.

    Returns ``{"status": message}`` or None, the shape ``close_refusal`` uses.
    """
    if new_status == "Duplicate" and old_status != "Duplicate":
        return {"status": DUPLICATE_BY_MERGE_ONLY}
    return None
