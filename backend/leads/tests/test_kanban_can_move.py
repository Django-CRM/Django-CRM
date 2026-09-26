"""Each lead board card says whether the viewer may move it (`can_move`).

The flag is `has_lead_access`, the one-lead form of the `visible_leads_qs`
rule that `LeadMoveView` looks the lead up through. The lead board reads with
that same rule, so every card a caller is shown is one they may move, and the
flag is True on each of them; a lead outside the rule is not on the board and
its move answers 404. The flag is there so that both clients gate the drag on
the one field every kanban card carries, and so that it follows the move rule
if the two ever part.
"""

from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from leads.models import Lead, LeadPipeline, LeadStage
from leads.serializer import LeadKanbanCardSerializer


def _lead(org, name, **kw):
    kw.setdefault("status", "assigned")
    return Lead.objects.create(
        first_name=name,
        last_name="Lead",
        email=f"{name.lower()}@example.com",
        org=org,
        **kw,
    )


@pytest.fixture
def stage(org_a):
    pipeline = LeadPipeline.objects.create(name="Sales", org=org_a)
    return LeadStage.objects.create(pipeline=pipeline, name="New", order=1, org=org_a)


def _cards(client, stage=None):
    params = {"pipeline_id": str(stage.pipeline_id)} if stage else {}
    response = client.get("/api/leads/kanban/", params)
    assert response.status_code == 200, response.content
    payload = response.json()
    cards = [card for col in payload["columns"] for card in col["leads"]]
    cards += (payload.get("unstaged") or {}).get("leads", [])
    return {card["id"]: card for card in cards}


def _move(client, lead, stage):
    return client.patch(
        f"/api/leads/{lead.id}/move/", {"stage_id": str(stage.id)}, format="json"
    )


class TestLeadCardCanMove:
    def test_an_assignee_may_move_and_the_card_says_so(
        self, user_client, user_profile, admin_user, org_a, stage
    ):
        lead = _lead(org_a, "Held", created_by=admin_user)
        lead.assigned_to.add(user_profile)

        assert _cards(user_client, stage)[str(lead.id)]["can_move"] is True
        response = _move(user_client, lead, stage)
        assert response.status_code == 200, response.content
        assert response.json()["lead"]["can_move"] is True

    def test_the_creator_may_move(self, user_client, regular_user, org_a, stage):
        lead = _lead(org_a, "Mine", created_by=regular_user, stage=stage)
        assert _cards(user_client, stage)[str(lead.id)]["can_move"] is True
        assert _move(user_client, lead, stage).status_code == 200

    def test_status_mode_carries_the_flag(self, user_client, regular_user, org_a):
        lead = _lead(org_a, "Mine", created_by=regular_user)
        assert _cards(user_client)[str(lead.id)]["can_move"] is True

    def test_an_admin_may_move_every_card(
        self, admin_client, regular_user, org_a, stage
    ):
        lead = _lead(org_a, "Theirs", created_by=regular_user)
        assert _cards(admin_client, stage)[str(lead.id)]["can_move"] is True
        assert _move(admin_client, lead, stage).status_code == 200

    def test_a_superuser_on_a_member_profile_may_move(
        self, user_client, regular_user, admin_user, org_a, stage
    ):
        regular_user.is_superuser = True
        regular_user.save(update_fields=["is_superuser"])
        lead = _lead(org_a, "Theirs", created_by=admin_user)
        assert _cards(user_client, stage)[str(lead.id)]["can_move"] is True
        assert _move(user_client, lead, stage).status_code == 200

    def test_a_lead_the_caller_cannot_open_is_absent_and_404s(
        self, user_client, admin_user, org_a, stage
    ):
        hidden = _lead(org_a, "Hidden", created_by=admin_user)
        assert str(hidden.id) not in _cards(user_client, stage)
        assert _move(user_client, hidden, stage).status_code == 404

    def test_the_rule_refuses_a_lead_outside_it(
        self, user_profile, regular_user, admin_user, org_a
    ):
        """The same serializer on a lead the viewer may not open says False,
        so the flag is the rule and not a constant."""
        hidden = _lead(org_a, "Hidden", created_by=admin_user)
        viewer = SimpleNamespace(profile=user_profile, user=regular_user)
        data = LeadKanbanCardSerializer(hidden, context={"request": viewer}).data
        assert data["can_move"] is False

    def test_without_a_request_the_flag_is_false(self, regular_user, org_a):
        lead = _lead(org_a, "Mine", created_by=regular_user)
        assert LeadKanbanCardSerializer(lead).data["can_move"] is False

    def test_the_flag_costs_no_query_per_card(
        self, user_profile, regular_user, admin_user, org_a
    ):
        for n in range(4):
            _lead(org_a, f"Held{n}", created_by=admin_user).assigned_to.add(
                user_profile
            )
        leads = list(
            Lead.objects.filter(org=org_a)
            .select_related("created_by", "stage")
            .prefetch_related("assigned_to", "tags")
        )
        viewer = SimpleNamespace(profile=user_profile, user=regular_user)
        LeadKanbanCardSerializer(leads, many=True).data

        with CaptureQueriesContext(connection) as without_flag:
            LeadKanbanCardSerializer(leads, many=True).data
        with CaptureQueriesContext(connection) as with_flag:
            data = LeadKanbanCardSerializer(
                leads, many=True, context={"request": viewer}
            ).data
        assert all(card["can_move"] for card in data)
        assert len(with_flag) == len(without_flag)
