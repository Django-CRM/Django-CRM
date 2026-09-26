"""A same-org ticket the caller cannot open answers like a missing one.

Owner decision (1.11.0): on every endpoint that takes a ticket id, a ticket in
the caller's org that they may not read answers 404 with a body identical to
the one a missing id gets. A 403 confirmed the ticket existed. The responses
are compared whole, status and body, never by status alone, and the hidden
ticket is checked untouched afterwards.

The caller is a plain member who did not create the hidden ticket, is not
assigned to it and does not watch it. `test_case_access_and_sla` pins the
other direction: a watcher, who may read, still gets 403 on a write.
"""

import pytest

from cases.models import Case, CaseWatcher, TimeEntry
from common.models import Comment

MISSING = "00000000-0000-0000-0000-00000000abcd"
OTHER = "00000000-0000-0000-0000-00000000ef01"

# (method, path under /api/cases/<id>/, body)
ENDPOINTS = [
    ("get", "", None),
    ("put", "", {"name": "Renamed", "status": "New", "priority": "Normal"}),
    ("patch", "", {"priority": "Low"}),
    ("post", "", {"comment": "hello"}),
    ("delete", "", None),
    ("get", "activities/", None),
    ("post", "solutions/", {"solution_id": OTHER}),
    ("delete", f"solutions/{OTHER}/", None),
    ("get", "solution-suggestions/", None),
    ("post", "watch/", None),
    ("delete", "watch/", None),
    ("get", "watchers/", None),
    ("get", "merge-targets/", None),
    ("post", "unmerge/", None),
    ("get", "tree/", None),
    ("post", "link/", {"parent_id": None}),
    ("post", "close-with-children/", {"cascade": False}),
    ("get", "time-entries/", None),
    ("post", "time-entries/", {"duration_minutes": 30, "description": "x"}),
    ("post", "time-entries/start/", None),
    ("get", "time-summary/", None),
    ("post", "request-approval/", {}),
    ("patch", "move/", {"status": "Assigned"}),
]


@pytest.fixture
def hidden(admin_user, org_a):
    return Case.objects.create(
        name="Payroll dispute",
        status="New",
        priority="Normal",
        org=org_a,
        created_by=admin_user,
    )


@pytest.fixture
def mine(regular_user, org_a):
    return Case.objects.create(
        name="My ticket",
        status="New",
        priority="Normal",
        org=org_a,
        created_by=regular_user,
    )


def _call(client, method, url, body):
    return getattr(client, method)(url, body, format="json")


def _whole(response):
    return response.status_code, response.content


def _untouched(case):
    case.refresh_from_db()
    assert case.status == "New"
    assert case.name == "Payroll dispute"
    assert case.priority == "Normal"
    assert case.is_active is True
    assert case.parent_id is None
    assert not CaseWatcher.objects.filter(case=case).exists()
    assert not TimeEntry.objects.filter(case=case).exists()
    assert not Comment.objects.filter(object_id=case.id).exists()


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_hidden_answers_exactly_like_missing(method, path, body, user_client, hidden):
    got = _call(user_client, method, f"/api/cases/{hidden.id}/{path}", body)
    missing = _call(user_client, method, f"/api/cases/{MISSING}/{path}", body)
    assert got.status_code == 404, got.content
    assert _whole(got) == _whole(missing)
    assert b"Payroll dispute" not in got.content
    _untouched(hidden)


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_a_reader_is_not_told_404(
    method, path, body, user_client, user_profile, hidden, org_a
):
    """The other side of the rule: once the member may read the ticket (here,
    as its assignee) no endpoint above answers what a missing ticket gets.
    (Two of them still 404, about the solution id in the path or body.)"""
    hidden.assigned_to.add(user_profile)
    got = _call(user_client, method, f"/api/cases/{hidden.id}/{path}", body)
    missing = _call(user_client, method, f"/api/cases/{MISSING}/{path}", body)
    assert _whole(got) != _whole(missing), (path, got.content)


def test_merging_a_hidden_source_answers_like_a_missing_one(user_client, hidden, mine):
    got = user_client.post(f"/api/cases/{hidden.id}/merge/{mine.id}/")
    missing = user_client.post(f"/api/cases/{MISSING}/merge/{mine.id}/")
    assert got.status_code == 404
    assert _whole(got) == _whole(missing)
    _untouched(hidden)


def test_merging_into_a_hidden_target_answers_like_a_missing_one(
    user_client, hidden, mine
):
    got = user_client.post(f"/api/cases/{mine.id}/merge/{hidden.id}/")
    missing = user_client.post(f"/api/cases/{mine.id}/merge/{MISSING}/")
    assert got.status_code == 404
    assert _whole(got) == _whole(missing)
    mine.refresh_from_db()
    assert mine.merged_into_id is None


class TestBulk:
    """A batch leaves a hidden id out of `results`, exactly as a missing one."""

    def test_update_reports_nothing_for_a_hidden_id(self, user_client, hidden, mine):
        response = user_client.post(
            "/api/cases/bulk/update/",
            {
                "ids": [str(hidden.id), MISSING, str(mine.id)],
                "fields": {"priority": "Low"},
            },
            format="json",
        )
        assert response.json()["results"] == [{"id": str(mine.id), "status": "updated"}]
        _untouched(hidden)

    def test_delete_reports_nothing_for_a_hidden_id(self, user_client, hidden, mine):
        response = user_client.post(
            "/api/cases/bulk/delete/",
            {"ids": [str(hidden.id), MISSING, str(mine.id)]},
            format="json",
        )
        assert response.json()["results"] == [{"id": str(mine.id), "status": "deleted"}]
        _untouched(hidden)

    def test_a_readable_but_unwritable_ticket_still_reports_no_access(
        self, user_client, user_profile, hidden, org_a
    ):
        CaseWatcher.objects.create(case=hidden, profile=user_profile, org=org_a)
        response = user_client.post(
            "/api/cases/bulk/update/",
            {"ids": [str(hidden.id)], "fields": {"priority": "Low"}},
            format="json",
        )
        assert response.json()["results"] == [
            {"id": str(hidden.id), "status": "no_access"}
        ]
