"""G31: each deal's next open task, on the deal list and the deal board.

`next_activity` is the earliest open task linked to the deal, among the tasks
the CALLER can open. A member who can open a deal cannot necessarily open
every task on it, and a hidden task counted here would put its title and due
date on their card. Both endpoints are checked because they build their
querysets separately.
"""

from datetime import date, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from opportunity.models import Opportunity
from tasks.models import Task

LIST = "/api/opportunities/"
BOARD = "/api/opportunities/kanban/"


def _deal(org, profile, name="Deal"):
    deal = Opportunity.objects.create(org=org, name=name, stage="PROSPECTING")
    deal.assigned_to.add(profile)
    return deal


def _task(deal, title, *, visible_to=None, status="New", due=None):
    task = Task.objects.create(
        title=title,
        status=status,
        priority="Low",
        org=deal.org,
        opportunity=deal,
        due_date=due,
    )
    if visible_to is not None:
        task.assigned_to.add(visible_to)
    return task


def _from_list(client, deal):
    rows = client.get(LIST).data["opportunities"]
    return next(r for r in rows if r["id"] == str(deal.id))["next_activity"]


def _from_board(client, deal):
    columns = client.get(BOARD).data["columns"]
    cards = [card for column in columns for card in column["items"]]
    return next(c for c in cards if c["id"] == str(deal.id))["next_activity"]


READERS = [_from_list, _from_board]


@pytest.mark.django_db
@pytest.mark.parametrize("read", READERS)
class TestNextActivity:
    def test_visible_open_task_is_shown(self, read, user_client, user_profile, org_a):
        deal = _deal(org_a, user_profile)
        due = date.today() + timedelta(days=3)
        task = _task(deal, "Send proposal", visible_to=user_profile, due=due)

        assert read(user_client, deal) == {
            "id": str(task.id),
            "title": "Send proposal",
            "due_date": due.isoformat(),
        }

    def test_hidden_task_is_never_shown(self, read, user_client, user_profile, org_a):
        """The hidden task is due sooner, so a leak would pick it first."""
        deal = _deal(org_a, user_profile)
        _task(deal, "Secret call", due=date.today())
        _task(
            deal,
            "My follow-up",
            visible_to=user_profile,
            due=date.today() + timedelta(days=5),
        )

        assert read(user_client, deal)["title"] == "My follow-up"

    def test_only_a_hidden_task_reads_as_none(
        self, read, user_client, user_profile, org_a
    ):
        deal = _deal(org_a, user_profile)
        _task(deal, "Secret call", due=date.today())

        assert read(user_client, deal) is None

    def test_admin_sees_every_task(self, read, admin_client, user_profile, org_a):
        deal = _deal(org_a, user_profile)
        _task(deal, "Secret call", due=date.today())

        assert read(admin_client, deal)["title"] == "Secret call"

    def test_deal_with_no_open_task_is_flagged(
        self, read, user_client, user_profile, org_a
    ):
        deal = _deal(org_a, user_profile)

        assert read(user_client, deal) is None

    def test_completed_tasks_are_ignored(self, read, user_client, user_profile, org_a):
        deal = _deal(org_a, user_profile)
        _task(
            deal,
            "Done already",
            visible_to=user_profile,
            status="Completed",
            due=date.today() - timedelta(days=1),
        )
        assert read(user_client, deal) is None

        _task(deal, "Still to do", visible_to=user_profile, status="In Progress")
        assert read(user_client, deal)["title"] == "Still to do"

    def test_earliest_due_wins_and_undated_comes_last(
        self, read, user_client, user_profile, org_a
    ):
        deal = _deal(org_a, user_profile)
        _task(deal, "Undated", visible_to=user_profile)
        _task(
            deal,
            "Later",
            visible_to=user_profile,
            due=date.today() + timedelta(days=9),
        )
        _task(
            deal,
            "Sooner",
            visible_to=user_profile,
            due=date.today() + timedelta(days=1),
        )

        assert read(user_client, deal)["title"] == "Sooner"

    def test_undated_task_still_counts(self, read, user_client, user_profile, org_a):
        deal = _deal(org_a, user_profile)
        _task(deal, "Undated", visible_to=user_profile)

        assert read(user_client, deal) == {
            "id": str(Task.objects.get(title="Undated").id),
            "title": "Undated",
            "due_date": None,
        }


@pytest.mark.django_db
def test_tasks_do_not_multiply_deal_rows(user_client, user_profile, org_a):
    """Several tasks on several deals: one row per deal, and the totals agree."""
    deals = [_deal(org_a, user_profile, name=f"Deal {i}") for i in range(3)]
    for deal in deals:
        for n in range(3):
            _task(deal, f"{deal.name} task {n}", visible_to=user_profile)

    data = user_client.get(LIST).data
    assert sorted(r["id"] for r in data["opportunities"]) == sorted(
        str(d.id) for d in deals
    )
    assert data["totals"]["count"] == 3
    assert data["opportunities_count"] == 3


@pytest.mark.django_db
def test_move_response_carries_next_activity(user_client, user_profile, org_a):
    deal = _deal(org_a, user_profile)
    _task(deal, "Call back", visible_to=user_profile)

    response = user_client.patch(
        f"/api/opportunities/{deal.id}/move/",
        {"column_id": "NEGOTIATION"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["opportunity"]["next_activity"]["title"] == "Call back"


@pytest.mark.django_db
def test_next_activity_is_read_only(user_client, user_profile, org_a):
    """A client cannot write it: PATCH ignores the key and creates no task."""
    deal = _deal(org_a, user_profile)
    response = user_client.patch(
        f"/api/opportunities/{deal.id}/",
        {"next_activity": {"title": "Forged"}},
        format="json",
    )
    assert response.status_code == 200
    assert not Task.objects.filter(title="Forged").exists()
    assert _from_list(user_client, deal) is None


@pytest.mark.django_db
@pytest.mark.parametrize("read", READERS)
@pytest.mark.parametrize("code,kind", [("CLOSED_WON", "won"), ("CLOSED_LOST", "lost")])
def test_closed_deal_carries_its_kind_beside_next_activity(
    read, code, kind, user_client, user_profile, org_a
):
    """The "none" flag is for open deals only, and the clients tell them apart
    by `stage_kind` in the same row, so both payloads must carry it."""
    deal = _deal(org_a, user_profile)
    Opportunity.objects.filter(pk=deal.pk).update(stage=code, amount=100)

    rows = (
        user_client.get(LIST).data["opportunities"]
        if read is _from_list
        else [
            card
            for column in user_client.get(BOARD).data["columns"]
            for card in column["items"]
        ]
    )
    row = next(r for r in rows if r["id"] == str(deal.id))

    assert row["stage_kind"] == kind
    assert row["next_activity"] is None


def _task_queries(client, url):
    with CaptureQueriesContext(connection) as ctx:
        assert client.get(url).status_code == 200
    return [q["sql"] for q in ctx.captured_queries if '"task"' in q["sql"]]


@pytest.mark.django_db
@pytest.mark.parametrize("url", [LIST, BOARD])
def test_counts_never_carry_the_task_subqueries(url, user_client, user_profile, org_a):
    """The paginator's and each column's COUNT(*) ran the three correlated
    task subqueries over every visible deal. They are now computed once, for
    the rows actually returned, and never inside a count."""
    for i in range(4):
        deal = _deal(org_a, user_profile, name=f"Deal {i}")
        _task(deal, f"Task {i}", visible_to=user_profile)

    queries = _task_queries(user_client, url)

    assert not [q for q in queries if "COUNT(" in q.upper()]
    # One query for the whole page or board, not one per deal or column.
    assert len(queries) == 1
