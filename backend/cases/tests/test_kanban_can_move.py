"""Each ticket board card says whether the viewer may move it (`can_move`).

The board reads with the ticket read rule, which admits watchers, while the
move asserts the write rule, which does not. So a watcher was offered a drag
on both clients that the server then refused with a 403. `can_move` is the
write rule itself (`has_case_write_access`), computed per card, so the clients
offer the drag only where the move is accepted. The move's own contract is
unchanged: a watcher still gets 403, and a ticket the caller cannot open is a
404.
"""

from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from cases.models import Case, CasePipeline, CaseStage, CaseWatcher
from cases.serializer import CaseKanbanCardSerializer


def _case(org, creator, name, **kw):
    kw.setdefault("status", "New")
    return Case.objects.create(
        name=name, priority="Normal", org=org, created_by=creator, **kw
    )


def _cards(payload):
    return {card["id"]: card for col in payload["columns"] for card in col["cases"]}


def _board(client, **params):
    response = client.get("/api/cases/kanban/", params)
    assert response.status_code == 200, response.content
    return response.json()


def _move(client, case):
    return client.patch(
        f"/api/cases/{case.id}/move/", {"status": "Assigned"}, format="json"
    )


@pytest.fixture
def watched(org_a, admin_user, user_profile):
    """Someone else's ticket that the `user_client` caller only watches."""
    case = _case(org_a, admin_user, "Watched")
    CaseWatcher.objects.create(case=case, profile=user_profile, org=org_a)
    return case


@pytest.fixture
def held(org_a, admin_user, user_profile):
    """Someone else's ticket handed to the `user_client` caller."""
    case = _case(org_a, admin_user, "Held")
    case.assigned_to.add(user_profile)
    return case


class TestTicketCardCanMove:
    def test_a_watcher_sees_the_card_read_only_and_the_move_is_refused(
        self, user_client, watched
    ):
        card = _cards(_board(user_client))[str(watched.id)]
        assert card["can_move"] is False

        response = _move(user_client, watched)
        assert response.status_code == 403
        watched.refresh_from_db()
        assert watched.status == "New"

    def test_an_assignee_may_move_and_the_card_says_so(self, user_client, held):
        assert _cards(_board(user_client))[str(held.id)]["can_move"] is True

        response = _move(user_client, held)
        assert response.status_code == 200, response.content
        assert response.json()["case"]["can_move"] is True

    def test_the_creator_may_move(self, user_client, regular_user, org_a):
        mine = _case(org_a, regular_user, "Mine")
        assert _cards(_board(user_client))[str(mine.id)]["can_move"] is True
        assert _move(user_client, mine).status_code == 200

    def test_one_board_mixes_both(self, user_client, watched, held):
        cards = _cards(_board(user_client))
        assert cards[str(watched.id)]["can_move"] is False
        assert cards[str(held.id)]["can_move"] is True

    def test_an_admin_may_move_every_card(self, admin_client, org_a, regular_user):
        other = _case(org_a, regular_user, "A member's")
        cards = _cards(_board(admin_client))
        assert cards[str(other.id)]["can_move"] is True
        assert _move(admin_client, other).status_code == 200

    def test_a_superuser_on_a_member_profile_may_move(
        self, user_client, regular_user, watched
    ):
        regular_user.is_superuser = True
        regular_user.save(update_fields=["is_superuser"])
        assert _cards(_board(user_client))[str(watched.id)]["can_move"] is True
        assert _move(user_client, watched).status_code == 200

    def test_a_ticket_the_caller_cannot_open_is_absent_and_404s(
        self, user_client, org_a, admin_user
    ):
        hidden = _case(org_a, admin_user, "Hidden")
        assert str(hidden.id) not in _cards(_board(user_client))
        assert _move(user_client, hidden).status_code == 404

    def test_pipeline_mode_carries_the_flag(self, user_client, org_a, watched, held):
        pipeline = CasePipeline.objects.create(name="Support", org=org_a)
        stage = CaseStage.objects.create(
            pipeline=pipeline, name="Triage", order=1, org=org_a
        )
        Case.objects.filter(pk__in=[watched.pk, held.pk]).update(stage=stage)

        payload = _board(user_client, pipeline_id=str(pipeline.id))
        cards = _cards(payload)
        assert cards[str(watched.id)]["can_move"] is False
        assert cards[str(held.id)]["can_move"] is True

    def test_without_a_request_the_flag_is_false(self, held):
        assert CaseKanbanCardSerializer(held).data["can_move"] is False

    def test_the_flag_costs_no_query_per_card(
        self, org_a, admin_user, regular_user, user_profile
    ):
        """Serializing with the flag computed costs what serializing without
        it does, on cards prefetched the way the board prefetches them."""
        for n in range(4):
            case = _case(org_a, admin_user, f"Watched {n}")
            CaseWatcher.objects.create(case=case, profile=user_profile, org=org_a)
        cases = list(
            Case.objects.filter(org=org_a)
            .select_related("created_by", "stage", "account")
            .prefetch_related("assigned_to", "tags", "contacts")
        )
        viewer = SimpleNamespace(profile=user_profile, user=regular_user)
        # The SLA fields cache what they look up on each case, so one pass
        # first leaves both measured passes starting from the same state.
        CaseKanbanCardSerializer(cases, many=True).data

        with CaptureQueriesContext(connection) as without_flag:
            CaseKanbanCardSerializer(cases, many=True).data
        with CaptureQueriesContext(connection) as with_flag:
            data = CaseKanbanCardSerializer(
                cases, many=True, context={"request": viewer}
            ).data
        assert [card["can_move"] for card in data] == [False] * 4
        assert len(with_flag) == len(without_flag)
