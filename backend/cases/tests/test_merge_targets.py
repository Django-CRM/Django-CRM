"""The merge facts and picker the web and mobile ticket pages read (G9).

`CaseDetailView.get` reports `can_merge` (this ticket's half of the merge
rule) and `can_unmerge` per merged-from source; `merge-targets/` lists only
tickets the merge endpoint would accept. Each fact is pinned both ways, and
the picker is pinned never to list a ticket the caller cannot open.
"""

from __future__ import annotations

import pytest

from cases.models import Case, CaseWatcher
from conftest import rls_org


def _case(org, creator, name, **fields):
    fields.setdefault("status", "New")
    return Case.objects.create(
        name=name, priority="Normal", org=org, created_by=creator, **fields
    )


def _targets(client, case, **params):
    return client.get(f"/api/cases/{case.id}/merge-targets/", params)


def _names(response):
    return sorted(row["name"] for row in response.json()["results"])


class TestCanMergeFact:
    def test_creator_may_merge_their_ticket(self, user_client, regular_user, org_a):
        mine = _case(org_a, regular_user, "Mine")
        body = user_client.get(f"/api/cases/{mine.id}/").json()
        assert body["can_merge"] is True

    def test_assignee_who_did_not_raise_it_may_not(
        self, user_client, user_profile, admin_user, org_a
    ):
        theirs = _case(org_a, admin_user, "Handed to me")
        theirs.assigned_to.add(user_profile)
        body = user_client.get(f"/api/cases/{theirs.id}/").json()
        assert body["can_merge"] is False

    def test_admin_may_merge_any_ticket(self, admin_client, regular_user, org_a):
        member_ticket = _case(org_a, regular_user, "A member's")
        body = admin_client.get(f"/api/cases/{member_ticket.id}/").json()
        assert body["can_merge"] is True


class TestCanUnmergeFact:
    def _merged(self, client, source, target):
        response = client.post(f"/api/cases/{source.id}/merge/{target.id}/")
        assert response.status_code == 200, response.content

    def test_true_for_the_creator_of_both(self, user_client, regular_user, org_a):
        target = _case(org_a, regular_user, "Target")
        source = _case(org_a, regular_user, "Source")
        self._merged(user_client, source, target)
        entry = user_client.get(f"/api/cases/{target.id}/").json()["merged_from_cases"][
            0
        ]
        assert entry["can_unmerge"] is True
        assert user_client.post(f"/api/cases/{source.id}/unmerge/").status_code == 200

    def test_false_when_the_source_is_someone_elses(
        self, admin_client, user_client, user_profile, regular_user, admin_user, org_a
    ):
        target = _case(org_a, regular_user, "Mine")
        source = _case(org_a, admin_user, "The admin's")
        source.assigned_to.add(user_profile)  # readable, not mergeable
        self._merged(admin_client, source, target)
        entry = user_client.get(f"/api/cases/{target.id}/").json()["merged_from_cases"][
            0
        ]
        assert entry["restricted"] is False
        assert entry["can_unmerge"] is False
        # The fact agrees with the endpoint.
        assert user_client.post(f"/api/cases/{source.id}/unmerge/").status_code == 403

    def test_restricted_source_keeps_no_name_and_no_button(
        self, admin_client, user_client, regular_user, admin_user, org_a
    ):
        target = _case(org_a, regular_user, "Mine")
        source = _case(org_a, admin_user, "Hidden from me")
        self._merged(admin_client, source, target)
        entry = user_client.get(f"/api/cases/{target.id}/").json()["merged_from_cases"][
            0
        ]
        assert entry["restricted"] is True
        assert entry["name"] is None
        assert entry["can_unmerge"] is False


class TestMergeTargets:
    def test_member_is_offered_only_tickets_they_raised(
        self, user_client, user_profile, regular_user, admin_user, org_a
    ):
        source = _case(org_a, regular_user, "Source")
        _case(org_a, regular_user, "Also mine")
        assigned = _case(org_a, admin_user, "Assigned to me")
        assigned.assigned_to.add(user_profile)
        watched = _case(org_a, admin_user, "Watched by me")
        CaseWatcher.objects.create(case=watched, profile=user_profile, org=org_a)
        _case(org_a, admin_user, "Hidden from me")
        response = _targets(user_client, source)
        assert response.status_code == 200, response.content
        # Readable but not raised by them: the merge would refuse, so the
        # picker never offers them. The hidden one is never listed at all.
        assert _names(response) == ["Also mine"]

    def test_admin_is_offered_every_live_ticket_but_the_source(
        self, admin_client, regular_user, admin_user, org_a
    ):
        source = _case(org_a, admin_user, "Source")
        _case(org_a, regular_user, "A member's")
        _case(org_a, admin_user, "Admin's")
        _case(org_a, admin_user, "Soft-deleted", is_active=False)
        _case(org_a, admin_user, "Already a duplicate", status="Duplicate")
        merged_away = _case(org_a, admin_user, "Merged away")
        survivor = _case(org_a, admin_user, "Survivor")
        admin_client.post(f"/api/cases/{merged_away.id}/merge/{survivor.id}/")
        assert _names(_targets(admin_client, source)) == [
            "A member's",
            "Admin's",
            "Survivor",
        ]

    def test_search_narrows_by_name(self, admin_client, admin_user, org_a):
        source = _case(org_a, admin_user, "Source")
        _case(org_a, admin_user, "Printer on fire")
        _case(org_a, admin_user, "VPN down")
        assert _names(_targets(admin_client, source, search="printer")) == [
            "Printer on fire"
        ]

    def test_another_orgs_tickets_are_never_listed(
        self, admin_client, admin_user, user_b, org_a, org_b
    ):
        source = _case(org_a, admin_user, "Source")
        with rls_org(org_b):
            _case(org_b, user_b, "Foreign")
        assert _names(_targets(admin_client, source)) == []

    def test_refused_when_the_caller_may_not_merge_the_source(
        self, user_client, user_profile, admin_user, org_a
    ):
        assigned = _case(org_a, admin_user, "Assigned to me")
        assigned.assigned_to.add(user_profile)
        response = _targets(user_client, assigned)
        assert response.status_code == 403
        assert "results" not in response.json()

    def test_a_hidden_source_answers_as_its_detail_page_does(
        self, user_client, admin_user, org_a
    ):
        hidden = _case(org_a, admin_user, "Hidden")
        detail = user_client.get(f"/api/cases/{hidden.id}/").status_code
        assert _targets(user_client, hidden).status_code == detail == 403

    def test_another_orgs_source_is_404(self, admin_client, user_b, org_b):
        with rls_org(org_b):
            foreign = _case(org_b, user_b, "Foreign")
        assert _targets(admin_client, foreign).status_code == 404

    def test_unauthenticated_is_refused(
        self, unauthenticated_client, admin_user, org_a
    ):
        source = _case(org_a, admin_user, "Source")
        assert _targets(unauthenticated_client, source).status_code in (401, 403)


@pytest.mark.parametrize("who", ["member", "watcher"])
def test_board_move_of_an_unwritable_ticket_answers_like_its_detail(
    who, user_client, user_profile, admin_user, org_a
):
    """A hidden ticket's move answers what its detail GET answers (403 inside
    the org, `test_kanban_move_write_rule` pins 404 across orgs), and a
    watcher, who may read it, is still refused the move. Nothing is written."""
    case = _case(org_a, admin_user, "Not mine")
    if who == "watcher":
        CaseWatcher.objects.create(case=case, profile=user_profile, org=org_a)
    detail = user_client.get(f"/api/cases/{case.id}/").status_code
    move = user_client.patch(
        f"/api/cases/{case.id}/move/", {"status": "Closed"}, format="json"
    )
    assert move.status_code == 403
    assert detail == (200 if who == "watcher" else 403)
    case.refresh_from_db()
    assert case.status == "New"
