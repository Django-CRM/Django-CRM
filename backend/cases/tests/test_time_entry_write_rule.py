"""Logging time and starting a timer take the ticket's write rule.

Listing a ticket's time and reading its summary need read access. Logging time
and starting a timer are work on the ticket, so they take the rule replying
takes (`comment_permission` on the detail payload): an assignee or creator may,
a watcher (who may read) gets 403, and a member who cannot open the ticket gets
the 404 a missing ticket gets. Stopping stays the entry owner's.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from cases.models import Case, CaseWatcher, TimeEntry


@pytest.fixture
def ticket(admin_user, org_a):
    return Case.objects.create(
        name="Printer on fire",
        status="New",
        priority="Normal",
        org=org_a,
        created_by=admin_user,
    )


def _log(client, case):
    end = timezone.now() - timedelta(minutes=5)
    return client.post(
        f"/api/cases/{case.id}/time-entries/",
        {
            "started_at": (end - timedelta(minutes=30)).isoformat(),
            "ended_at": end.isoformat(),
            "description": "Replaced the toner",
        },
        format="json",
    )


def _start(client, case):
    return client.post(f"/api/cases/{case.id}/time-entries/start/", {}, format="json")


WRITES = [_log, _start]


@pytest.mark.parametrize("write", WRITES)
def test_an_assignee_may(write, user_client, user_profile, ticket):
    ticket.assigned_to.add(user_profile)
    assert write(user_client, ticket).status_code == 201
    assert TimeEntry.objects.filter(case=ticket, profile=user_profile).count() == 1


@pytest.mark.parametrize("write", WRITES)
def test_a_watcher_reads_but_may_not_write(
    write, user_client, user_profile, ticket, org_a
):
    CaseWatcher.objects.create(case=ticket, profile=user_profile, org=org_a)
    assert user_client.get(f"/api/cases/{ticket.id}/time-entries/").status_code == 200
    assert user_client.get(f"/api/cases/{ticket.id}/time-summary/").status_code == 200
    # The detail payload tells the clients the same thing.
    detail = user_client.get(f"/api/cases/{ticket.id}/").json()
    assert detail["comment_permission"] is False

    assert write(user_client, ticket).status_code == 403
    assert not TimeEntry.objects.filter(case=ticket).exists()


@pytest.mark.parametrize("write", WRITES)
def test_a_member_who_cannot_open_it_gets_404(write, user_client, ticket):
    assert write(user_client, ticket).status_code == 404
    assert not TimeEntry.objects.filter(case=ticket).exists()


def test_the_owner_still_stops_their_timer_after_losing_the_ticket(
    user_client, user_profile, ticket
):
    """Stopping is not gated on the ticket: refusing it would strand the one
    running timer a person may have, org-wide."""
    ticket.assigned_to.add(user_profile)
    entry_id = _start(user_client, ticket).json()["id"]
    ticket.assigned_to.remove(user_profile)

    response = user_client.post(f"/api/time-entries/{entry_id}/stop/")
    assert response.status_code == 200, response.content
    assert TimeEntry.objects.get(id=entry_id).ended_at is not None
