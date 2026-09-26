"""The ticket board reads the SLA calendar once, not once per card.

Every card's breached and at-risk fields walk the org's business calendar. Each
case memoized the calendar for itself, so a board still read the calendar row
once per card, and `add_business_hours` then read the calendar's holidays again
on every walk (up to four per card), ignoring the prefetch. The board now loads
the calendar once and hands it to each card, and the walk reads the prefetched
holidays.
"""

from datetime import date, time, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from business_hours.models import BusinessCalendar, BusinessHoliday
from cases.models import Case


@pytest.fixture
def calendar(org_a):
    workday = {"open": time(9, 0), "close": time(17, 0)}
    fields = {}
    for day in ("monday", "tuesday", "wednesday", "thursday", "friday"):
        fields[f"{day}_open"] = workday["open"]
        fields[f"{day}_close"] = workday["close"]
    cal = BusinessCalendar.objects.create(
        org=org_a, name="Default", timezone="UTC", is_default=True, **fields
    )
    BusinessHoliday.objects.create(
        calendar=cal, org=org_a, date=date(2020, 1, 1), name="New year"
    )
    return cal


def _case(org, creator, name, created_at):
    case = Case.objects.create(
        name=name,
        status="New",
        priority="Normal",
        org=org,
        created_by=creator,
        sla_first_response_hours=4,
        sla_resolution_hours=24,
    )
    Case.objects.filter(pk=case.pk).update(created_at=created_at)
    return case


def _board(client):
    with CaptureQueriesContext(connection) as queries:
        response = client.get("/api/cases/kanban/")
    assert response.status_code == 200, response.content
    cards = {c["id"]: c for col in response.json()["columns"] for c in col["cases"]}
    return cards, len(queries)


def test_the_board_costs_the_same_for_one_card_or_five(
    admin_client, admin_user, org_a, calendar
):
    old = timezone.now() - timedelta(days=30)
    first = _case(org_a, admin_user, "First", old)
    _, one = _board(admin_client)

    for n in range(4):
        _case(org_a, admin_user, f"More {n}", old)
    cards, five = _board(admin_client)

    assert len(cards) == 5
    assert five == one
    # Still walked against the calendar: a month-old ticket has breached.
    assert cards[str(first.id)]["is_sla_breached"] is True


def test_a_shared_calendar_gives_the_answer_the_case_reaches_alone(
    admin_client, admin_user, org_a, calendar
):
    """A ticket two business hours old, read by the board and on its own."""
    now = timezone.now()
    case = _case(org_a, admin_user, "Recent", now - timedelta(hours=2))
    cards, _ = _board(admin_client)

    alone = Case.objects.get(pk=case.pk)
    card = cards[str(case.id)]
    assert card["is_sla_breached"] == (
        alone.is_sla_first_response_breached or alone.is_sla_resolution_breached
    )
    assert card["is_sla_at_risk"] == (
        alone.is_sla_first_response_at_risk or alone.is_sla_resolution_at_risk
    )
