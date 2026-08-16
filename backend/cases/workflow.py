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
