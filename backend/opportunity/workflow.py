"""
Opportunity workflow configuration.

Rules about deal stages that do not depend on any one org's configuration.
Which stages a pipeline has, what each is called and how long a deal may sit in
it live on `DealStage`; this module holds the defaults a new pipeline is seeded
with and the arithmetic every reader of those rows shares.
"""

import math

from common.utils import STAGES

# What a stage means, as opposed to what it is called. Won and lost are the
# two closed kinds; every report, goal and dashboard figure reads the kind, so
# renaming "Closed Won" to "Signed" changes a label and nothing else.
OPEN = "open"
WON = "won"
LOST = "lost"
STAGE_KINDS = ((OPEN, "Open"), (WON, "Won"), (LOST, "Lost"))
CLOSED_KINDS = (WON, LOST)

# Default probability for the seeded open stages (applied when a deal enters a
# stage with probability 0/None). Won and lost are decided by kind instead, so
# a custom-named won stage forecasts at 100 like the seeded one.
STAGE_PROBABILITIES = {
    "PROSPECTING": 10,
    "QUALIFICATION": 25,
    "PROPOSAL": 50,
    "NEGOTIATION": 75,
}


def stage_probability(code, kind):
    """The probability a deal takes on entering the stage `code` of `kind`."""
    if kind == WON:
        return 100
    if kind == LOST:
        return 0
    return STAGE_PROBABILITIES.get(code, 0)


# Default expected days per seeded open stage for deal aging.
DEFAULT_STAGE_EXPECTED_DAYS = {
    "PROSPECTING": 14,
    "QUALIFICATION": 14,
    "PROPOSAL": 10,
    "NEGOTIATION": 10,
}

# Red threshold multiplier: deal is "rotten" when days >= expected_days * ROTTEN_MULTIPLIER
ROTTEN_MULTIPLIER = 1.5

_SEEDED_KINDS = {"CLOSED_WON": WON, "CLOSED_LOST": LOST}

DEFAULT_PIPELINE_NAME = "Sales"

# (code, label, kind, expected_days) for every stage a new pipeline starts
# with. The codes are the ones `Opportunity.stage` has always stored, so a
# client that predates configurable pipelines keeps sending valid values.
DEFAULT_STAGES = tuple(
    (
        code,
        label,
        _SEEDED_KINDS.get(code, OPEN),
        DEFAULT_STAGE_EXPECTED_DAYS.get(code),
    )
    for code, label in STAGES
)


def aging_thresholds(kind, expected_days, warning_days):
    """`(yellow_days, red_days)` for a stage, or None when it never ages.

    A deal is yellow once it has sat `yellow_days` whole days in the stage and
    red (rotting) at `red_days`. Closed stages and stages with no expected
    duration never age. Whole days on both sides, so the per-row status
    (`Opportunity.get_aging_status`) and the queryset filters built from
    `stage_changed_at` cutoffs (`opportunity.stages.aging_q`) always agree.
    """
    if kind != OPEN or not expected_days:
        return None
    yellow = min(warning_days, expected_days) if warning_days else expected_days
    return yellow, math.ceil(expected_days * ROTTEN_MULTIPLIER)
