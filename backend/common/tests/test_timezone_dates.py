"""Whose day it is, when UTC and an org's timezone disagree.

``USE_TZ`` is on, so ``timezone.now()`` is UTC and ``.date()`` on it yields the
**UTC** calendar day. That is a different day from the org's for every hour its
timezone runs ahead of UTC, and the mirror image for every zone behind. All 132
sites in this app used to compute dates that way, so a task due today dropped
out of the Today queue and an invoice was not overdue on the day it came due.

They now go through ``timezone.localdate()``, which answers in whichever
timezone is active, and ``GetProfileAndOrg`` activates the org's. That makes the
day a per-tenant fact: at one instant a Kolkata org is on the 7th while a New
York org is still on the 6th, and each one's Today queue agrees with its own
wall calendar.

These tests freeze the clock inside the gap so they hold at any wall-clock time
rather than passing for 18.5 hours a day, and each one is paired with its mirror
so that a globally-computed day fails at least one of the pair.
"""

import datetime
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection
from django.utils import timezone
from rest_framework import status

from common.org_time import activate_org_timezone
from invoices.models import Invoice
from tasks.models import Task

# 19:00 UTC on 6 August is 00:30 on 7 August in Asia/Kolkata. Every assertion
# below turns on the two dates being different.
INSIDE_THE_GAP = datetime.datetime(2026, 8, 6, 19, 0, tzinfo=datetime.timezone.utc)
_DAY = datetime.timedelta(days=1)


def _sent_invoice(org, user, number, due_date):
    return Invoice.objects.create(
        invoice_number=number,
        org=org,
        created_by=user,
        status="Sent",
        issue_date=due_date - datetime.timedelta(days=10),
        due_date=due_date,
        total_amount=100,
    )


@contextmanager
def _as_org(tz_name):
    """Run a block with an org's timezone active, the way the middleware does."""
    activate_org_timezone(SimpleNamespace(timezone=tz_name))
    try:
        yield
    finally:
        timezone.deactivate()


def _set_rls(org):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as c:
        c.execute("SELECT set_config('app.current_org', %s, false)", [str(org.id)])


@pytest.fixture
def frozen():
    """Freeze the clock at an instant where the UTC day and the local day differ.

    Patching ``django.utils.timezone.now`` is enough to move both: Django's
    ``localdate()`` reaches for the module-level ``now`` when it is given no
    argument.
    """
    with patch("django.utils.timezone.now", return_value=INSIDE_THE_GAP):
        yield


@pytest.mark.django_db
class TestTheGapItself:
    """Guards the premise. If these stop holding, everything below is passing
    for the wrong reason and needs a new frozen instant."""

    def test_the_utc_day_and_an_eastern_orgs_day_differ(self, frozen):
        activate_org_timezone(SimpleNamespace(timezone="Asia/Kolkata"))
        try:
            assert timezone.now().date() == datetime.date(2026, 8, 6)
            assert timezone.localdate() == datetime.date(2026, 8, 7)
        finally:
            timezone.deactivate()

    def test_two_orgs_are_on_different_days_at_the_same_instant(self, frozen):
        days = {}
        for name in ("Asia/Kolkata", "America/New_York"):
            activate_org_timezone(SimpleNamespace(timezone=name))
            try:
                days[name] = timezone.localdate()
            finally:
                timezone.deactivate()

        assert days["Asia/Kolkata"] == datetime.date(2026, 8, 7)
        assert days["America/New_York"] == datetime.date(2026, 8, 6)

    def test_an_unknown_zone_falls_back_rather_than_raising(self, frozen):
        """A zone name that was legal when stored but has since left the tz
        database must not turn that org's every request into a 500."""
        activate_org_timezone(SimpleNamespace(timezone="Mars/Olympus_Mons"))
        try:
            assert timezone.localdate() == datetime.date(2026, 8, 6)
        finally:
            timezone.deactivate()


@pytest.mark.django_db
class TestTodayQueueUsesTheOrgDay:
    """Two orgs, one instant, two different days.

    This is the whole feature: at 19:00 UTC a Kolkata org is already on the 7th
    while a New York org is still on the 6th, and each one's Today queue has to
    agree with the calendar on its own wall.
    """

    url = "/api/dashboard/today/"

    def _task(self, org, user, due_date, title="Call the bakery"):
        _set_rls(org)
        return Task.objects.create(
            title=title,
            status="New",
            priority="Medium",
            org=org,
            created_by=user,
            due_date=due_date,
        )

    def test_a_task_due_on_the_orgs_today_is_in_the_queue(
        self, admin_client, org_a, admin_user, frozen
    ):
        org_a.timezone = "Asia/Kolkata"
        org_a.save(update_fields=["timezone"])
        # 7 August in Kolkata, still 6 August in UTC.
        task = self._task(org_a, admin_user, datetime.date(2026, 8, 7))

        response = admin_client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        assert f"task-{task.id}" in {row["id"] for row in response.data["queue"]}

    def test_the_same_date_is_still_tomorrow_for_an_org_behind_utc(
        self, admin_client, org_a, admin_user, frozen
    ):
        """The discriminating half. If the day were computed globally rather
        than per org, this task would land in the queue too."""
        org_a.timezone = "America/New_York"
        org_a.save(update_fields=["timezone"])
        # 19:00 UTC is 15:00 on the 6th in New York, so the 7th has not arrived.
        task = self._task(org_a, admin_user, datetime.date(2026, 8, 7))

        response = admin_client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        assert f"task-{task.id}" not in {row["id"] for row in response.data["queue"]}

    def test_yesterdays_task_reads_as_overdue_not_due(
        self, admin_client, org_a, admin_user, frozen
    ):
        org_a.timezone = "Asia/Kolkata"
        org_a.save(update_fields=["timezone"])
        task = self._task(org_a, admin_user, datetime.date(2026, 8, 6), "Chase it")

        response = admin_client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        row = next(
            (r for r in response.data["queue"] if r["id"] == f"task-{task.id}"), None
        )
        assert row is not None, "an overdue task belongs in the queue"
        assert row["due"] == "Overdue"


@pytest.mark.django_db
class TestOverdueUsesTheOrgDay:
    """`Invoice.is_overdue` is a model property with no request around it, so
    these activate the org's timezone by hand exactly as `GetProfileAndOrg`
    does. The pair is the point: east of UTC an invoice is late a few hours
    early, west of UTC it is late a few hours before it is actually due, and
    only one of those two directions can catch each mistake."""

    def test_east_of_utc_yesterdays_invoice_is_overdue(self, org_a, admin_user, frozen):
        _set_rls(org_a)
        with _as_org("Asia/Kolkata"):
            # The org is on 7 August; UTC is still on the 6th.
            invoice = _sent_invoice(
                org_a, admin_user, "INV-TZ-1", datetime.date(2026, 8, 6)
            )

            assert invoice.is_overdue is True

    def test_west_of_utc_todays_invoice_is_not_overdue_yet(self, org_a, admin_user):
        """The discriminating direction, and the one a US tenant lives in.

        A zone behind UTC crosses midnight after UTC does, so at 22:00 in New
        York it is already tomorrow in UTC and a UTC comparison calls an invoice
        late on the day it came due. Freezing east of UTC and asserting "due
        today is not overdue" would pass with the bug still in place, because
        there the buggy date is behind rather than ahead.
        """
        _set_rls(org_a)
        # 02:00 UTC on 7 August is 22:00 on 6 August in New York.
        west_of_the_gap = datetime.datetime(
            2026, 8, 7, 2, 0, tzinfo=datetime.timezone.utc
        )
        with patch("django.utils.timezone.now", return_value=west_of_the_gap):
            with _as_org("America/New_York"):
                assert timezone.localdate() == datetime.date(2026, 8, 6)

                invoice = _sent_invoice(
                    org_a, admin_user, "INV-TZ-2", datetime.date(2026, 8, 6)
                )

                assert invoice.is_overdue is False


@pytest.mark.django_db
class TestJoiningDate:
    """An invited member's `date_of_joining` is the org's day.

    It was set from `timezone.now()`, which a `DateField` stores as the UTC
    day, so a member invited in the Kolkata morning joined "yesterday".
    """

    def test_invited_member_joins_on_the_org_day(self, admin_client, org_a, frozen):
        from common.models import Profile

        org_a.timezone = "Asia/Kolkata"
        org_a.save(update_fields=["timezone"])
        response = admin_client.post(
            "/api/users/", {"email": "joiner@test.com", "role": "USER"}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        joined = Profile.objects.get(org=org_a, user__email="joiner@test.com")
        assert joined.date_of_joining == datetime.date(2026, 8, 7)
