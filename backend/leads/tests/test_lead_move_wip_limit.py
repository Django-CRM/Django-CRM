"""D9: the WIP limit on a lead board move.

The limit is a capacity of the stage, shared by everyone who works it, so it
counts every lead on the stage's lane, the same basis the ticket and task
board moves use, and not only the leads the mover can open. Counting per
caller would let each member fill the stage to the limit on their own.

What it must not do is tell a member anything about the leads they cannot
see: the refusal names the limit and never a count, so its body is the same
however many hidden leads fill the stage. Leads that sit on no lane for
anyone (inactive or converted) do not count against it.
"""

import pytest

from leads.models import Lead, LeadPipeline, LeadStage


@pytest.fixture
def stage(org_a):
    pipeline = LeadPipeline.objects.create(name="WIP", org=org_a)
    return LeadStage.objects.create(
        pipeline=pipeline, name="Limited", order=1, org=org_a, wip_limit=2
    )


def _lead(org, name, **kw):
    kw.setdefault("status", "assigned")
    return Lead.objects.create(
        first_name=name,
        last_name="Lead",
        email=f"{name.lower()}@example.com",
        org=org,
        **kw,
    )


def _mine(org, profile, name):
    lead = _lead(org, name)
    lead.assigned_to.add(profile)
    return lead


def _move(client, lead, stage):
    return client.patch(
        f"/api/leads/{lead.id}/move/", {"stage_id": str(stage.id)}, format="json"
    )


@pytest.mark.django_db
class TestWipLimit:
    def test_room_under_the_limit_allows_the_move(
        self, user_client, user_profile, org_a, stage
    ):
        _lead(org_a, "Hidden", stage=stage)  # one of two slots, not the caller's
        lead = _mine(org_a, user_profile, "Mover")

        response = _move(user_client, lead, stage)

        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.stage_id == stage.id

    def test_hidden_leads_count_toward_the_limit(
        self, user_client, user_profile, org_a, stage
    ):
        _lead(org_a, "HiddenOne", stage=stage)
        _lead(org_a, "HiddenTwo", stage=stage)
        lead = _mine(org_a, user_profile, "Mover")

        response = _move(user_client, lead, stage)

        assert response.status_code == 400
        lead.refresh_from_db()
        assert lead.stage_id is None

    def test_refusal_does_not_reveal_how_many_are_hidden(
        self, user_client, user_profile, org_a, stage
    ):
        """Two hidden leads or five, the member gets the same bytes."""
        for n in range(2):
            _lead(org_a, f"First{n}", stage=stage)
        first = _move(user_client, _mine(org_a, user_profile, "MoverA"), stage)

        for n in range(3):
            _lead(org_a, f"Second{n}", stage=stage)
        second = _move(user_client, _mine(org_a, user_profile, "MoverB"), stage)

        assert first.status_code == second.status_code == 400
        assert first.json() == second.json()
        assert first.json() == {
            "error": True,
            "errors": "Stage 'Limited' has reached its WIP limit of 2",
        }

    def test_off_board_leads_do_not_count(self, admin_client, org_a, stage):
        """Inactive and converted leads are on no lane, even for an admin."""
        _lead(org_a, "Gone", stage=stage, is_active=False)
        _lead(org_a, "Won", stage=stage, status="converted")
        _lead(org_a, "Present", stage=stage)
        lead = _lead(org_a, "Mover")

        assert _move(admin_client, lead, stage).status_code == 200

    def test_the_moving_lead_is_not_counted_against_itself(
        self, admin_client, org_a, stage
    ):
        _lead(org_a, "Present", stage=stage)
        lead = _lead(org_a, "Mover", stage=stage)

        assert _move(admin_client, lead, stage).status_code == 200
