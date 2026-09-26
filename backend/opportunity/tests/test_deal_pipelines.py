"""Configurable deal pipelines: the admin API, the deal contract, and kinds.

What these pin, in the order a reader meets the risk:

* every write is admin-only, and every read is open to members;
* another org's pipeline or stage is a 404 (or a 400 on a deal body), never
  its data and never a write;
* a pipeline keeps at least one open, one won and one lost stage, a stage or
  pipeline holding deals cannot be deleted, and the default cannot be;
* reorder takes the pipeline's exact stage set, once each;
* a deal's stage must be one of its pipeline's codes, and old clients that send
  a legacy code and no pipeline keep working;
* "won" is the stage's kind, not the string CLOSED_WON, everywhere money and
  goals are counted.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Account
from common.models import Org
from contacts.models import Contact
from opportunity.models import (
    DealPipeline,
    DealStage,
    Opportunity,
    OpportunityLineItem,
    SalesGoal,
)

PIPELINES = "/api/opportunities/pipelines/"
DEALS = "/api/opportunities/"


def _pipeline_url(pipeline):
    return f"{PIPELINES}{pipeline.id}/"


def _stage_url(stage):
    return f"/api/opportunities/stages/{stage.id}/"


def _reorder_url(pipeline):
    return f"{PIPELINES}{pipeline.id}/stages/reorder/"


def _stage(org, code, pipeline=None):
    pipeline = pipeline or DealPipeline.default_for(org)
    return DealStage.objects.get(pipeline=pipeline, code=code)


@pytest.fixture
def default_pipeline(org_a):
    return DealPipeline.default_for(org_a)


@pytest.fixture
def custom_pipeline(org_a):
    """A second pipeline whose won and lost stages are not called Closed Won/Lost."""
    pipeline = DealPipeline.objects.create(org=org_a, name="Renewals")
    for order, (code, label, kind, days) in enumerate(
        [
            ("TALKING", "Talking", "open", 5),
            ("SIGNED", "Signed", "won", None),
            ("CHURNED", "Churned", "lost", None),
        ],
        start=1,
    ):
        DealStage.objects.create(
            org=org_a,
            pipeline=pipeline,
            code=code,
            label=label,
            kind=kind,
            order=order,
            expected_days=days,
        )
    return pipeline


@pytest.fixture
def pipeline_b(org_b):
    """Org B's default pipeline, created under org B's RLS context."""
    from common.testing import set_rls_context

    set_rls_context(org_b)
    pipeline = DealPipeline.default_for(org_b)
    return pipeline


@pytest.mark.django_db
class TestDefaultPipeline:
    def test_a_new_org_gets_the_seeded_default_on_first_read(self, user_client, org_a):
        assert not DealPipeline.objects.filter(org=org_a).exists()

        response = user_client.get(PIPELINES)

        assert response.status_code == 200
        (pipeline,) = response.data["pipelines"]
        assert pipeline["is_default"] is True
        assert [
            (s["code"], s["label"], s["kind"], s["expected_days"])
            for s in pipeline["stages"]
        ] == [
            ("PROSPECTING", "Prospecting", "open", 14),
            ("QUALIFICATION", "Qualification", "open", 14),
            ("PROPOSAL", "Proposal", "open", 10),
            ("NEGOTIATION", "Negotiation", "open", 10),
            ("CLOSED_WON", "Closed Won", "won", None),
            ("CLOSED_LOST", "Closed Lost", "lost", None),
        ]

    def test_default_for_is_idempotent(self, org_a):
        first = DealPipeline.default_for(org_a)
        assert DealPipeline.default_for(org_a) == first
        assert DealPipeline.objects.filter(org=org_a, is_default=True).count() == 1

    def test_a_deal_saved_without_a_pipeline_lands_in_the_default(self, org_a):
        deal = Opportunity.objects.create(org=org_a, name="No pipeline")
        assert deal.pipeline == DealPipeline.default_for(org_a)
        assert deal.stage == "PROSPECTING"

    def test_an_unknown_code_from_an_internal_caller_settles_on_first_open(self, org_a):
        deal = Opportunity.objects.create(org=org_a, name="Stray", stage="GONE")
        assert deal.stage == "PROSPECTING"


@pytest.mark.django_db
class TestPipelineWritesAreAdminOnly:
    """Each write, refused for a member and allowed for an admin."""

    def test_create(self, admin_client, user_client, org_a):
        assert (
            user_client.post(PIPELINES, {"name": "X"}, format="json").status_code == 403
        )
        response = admin_client.post(PIPELINES, {"name": "Enterprise"}, format="json")
        assert response.status_code == 201
        assert response.data["is_default"] is False
        kinds = {s["kind"] for s in response.data["stages"]}
        assert kinds == {"open", "won", "lost"}
        assert DealPipeline.objects.filter(org=org_a, name="Enterprise").exists()

    def test_rename(self, admin_client, user_client, custom_pipeline):
        url = _pipeline_url(custom_pipeline)
        assert user_client.patch(url, {"name": "Y"}, format="json").status_code == 403
        response = admin_client.patch(url, {"name": "Upsells"}, format="json")
        assert response.status_code == 200
        custom_pipeline.refresh_from_db()
        assert custom_pipeline.name == "Upsells"

    def test_is_default_is_not_writable(self, admin_client, custom_pipeline):
        admin_client.patch(
            _pipeline_url(custom_pipeline), {"is_default": True}, format="json"
        )
        custom_pipeline.refresh_from_db()
        assert custom_pipeline.is_default is False

    def test_delete(self, admin_client, user_client, custom_pipeline):
        url = _pipeline_url(custom_pipeline)
        assert user_client.delete(url).status_code == 403
        assert admin_client.delete(url).status_code == 204
        assert not DealPipeline.objects.filter(pk=custom_pipeline.pk).exists()

    def test_add_stage(self, admin_client, user_client, custom_pipeline):
        url = f"{_pipeline_url(custom_pipeline)}stages/"
        body = {"label": "Verbal yes", "kind": "open", "expected_days": 3}
        assert user_client.post(url, body, format="json").status_code == 403
        response = admin_client.post(url, body, format="json")
        assert response.status_code == 201
        added = response.data["stages"][-1]
        assert (added["code"], added["label"], added["order"]) == (
            "VERBAL_YES",
            "Verbal yes",
            4,
        )

    def test_edit_stage(self, admin_client, user_client, org_a, default_pipeline):
        stage = _stage(org_a, "PROPOSAL")
        body = {"label": "Quote sent", "expected_days": 4, "warning_days": 2}
        assert (
            user_client.patch(_stage_url(stage), body, format="json").status_code == 403
        )
        assert (
            admin_client.patch(_stage_url(stage), body, format="json").status_code
            == 200
        )
        stage.refresh_from_db()
        # The label moves; the code every deal stores does not.
        assert (stage.code, stage.label, stage.expected_days, stage.warning_days) == (
            "PROPOSAL",
            "Quote sent",
            4,
            2,
        )

    def test_code_is_not_writable(self, admin_client, org_a, default_pipeline):
        stage = _stage(org_a, "PROPOSAL")
        admin_client.patch(_stage_url(stage), {"code": "HACKED"}, format="json")
        stage.refresh_from_db()
        assert stage.code == "PROPOSAL"

    def test_delete_stage(self, admin_client, user_client, org_a, default_pipeline):
        stage = _stage(org_a, "PROPOSAL")
        assert user_client.delete(_stage_url(stage)).status_code == 403
        assert admin_client.delete(_stage_url(stage)).status_code == 204
        assert not DealStage.objects.filter(pk=stage.pk).exists()

    def test_reorder(self, admin_client, user_client, custom_pipeline):
        ids = [str(s.id) for s in custom_pipeline.stages.all()][::-1]
        url = _reorder_url(custom_pipeline)
        assert (
            user_client.post(url, {"stage_ids": ids}, format="json").status_code == 403
        )
        response = admin_client.post(url, {"stage_ids": ids}, format="json")
        assert response.status_code == 200
        assert [s["id"] for s in response.data["stages"]] == ids

    def test_members_read(self, user_client, custom_pipeline):
        response = user_client.get(_pipeline_url(custom_pipeline))
        assert response.status_code == 200
        assert [s["code"] for s in response.data["stages"]] == [
            "TALKING",
            "SIGNED",
            "CHURNED",
        ]

    def test_aging_config_put(self, admin_client, user_client, org_a):
        body = [{"stage": "PROSPECTING", "expected_days": 3}]
        url = "/api/opportunities/aging-config/"
        assert user_client.put(url, body, format="json").status_code == 403
        assert admin_client.put(url, body, format="json").status_code == 200
        assert _stage(org_a, "PROSPECTING").expected_days == 3


@pytest.mark.django_db
class TestCrossOrg:
    """Another org's pipeline or stage never resolves, whoever asks."""

    def test_pipeline_detail_and_writes_are_404(self, org_b_client, custom_pipeline):
        url = _pipeline_url(custom_pipeline)
        assert org_b_client.get(url).status_code == 404
        assert (
            org_b_client.patch(url, {"name": "Mine"}, format="json").status_code == 404
        )
        assert org_b_client.delete(url).status_code == 404
        stages_url = f"{url}stages/"
        body = {"label": "Planted", "kind": "open"}
        assert org_b_client.post(stages_url, body, format="json").status_code == 404
        custom_pipeline.refresh_from_db()
        assert custom_pipeline.name == "Renewals"
        assert custom_pipeline.stages.count() == 3

    def test_list_holds_only_own_pipelines(self, org_b_client, custom_pipeline):
        names = {p["name"] for p in org_b_client.get(PIPELINES).data["pipelines"]}
        assert "Renewals" not in names

    def test_stage_writes_are_404(self, org_b_client, custom_pipeline):
        stage = custom_pipeline.stages.get(code="TALKING")
        response = org_b_client.patch(
            _stage_url(stage), {"label": "Owned"}, format="json"
        )
        assert response.status_code == 404
        assert org_b_client.delete(_stage_url(stage)).status_code == 404
        stage.refresh_from_db()
        assert stage.label == "Talking"

    def test_reorder_into_another_orgs_pipeline_is_404(
        self, org_b_client, custom_pipeline
    ):
        ids = [str(s.id) for s in custom_pipeline.stages.all()]
        response = org_b_client.post(
            _reorder_url(custom_pipeline), {"stage_ids": ids}, format="json"
        )
        assert response.status_code == 404

    def test_reorder_cannot_pull_in_another_orgs_stage(
        self, admin_client, custom_pipeline, pipeline_b
    ):
        ids = [str(s.id) for s in custom_pipeline.stages.all()]
        ids[0] = str(pipeline_b.stages.first().id)
        response = admin_client.post(
            _reorder_url(custom_pipeline), {"stage_ids": ids}, format="json"
        )
        assert response.status_code == 400

    def test_a_deal_cannot_name_another_orgs_pipeline(
        self, admin_client, org_a, pipeline_b
    ):
        from common.testing import set_rls_context

        set_rls_context(org_a)
        response = admin_client.post(
            DEALS,
            {
                "name": "Smuggled",
                "pipeline": str(pipeline_b.id),
                "stage": "PROSPECTING",
            },
            format="json",
        )
        assert response.status_code == 400
        assert "pipeline" in response.data["errors"]
        assert not Opportunity.objects.filter(name="Smuggled").exists()

    def test_kanban_for_another_orgs_pipeline_is_404(self, admin_client, pipeline_b):
        response = admin_client.get(
            f"/api/opportunities/kanban/?pipeline={pipeline_b.id}"
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestPipelineRules:
    def test_the_default_pipeline_cannot_be_deleted(
        self, admin_client, default_pipeline
    ):
        response = admin_client.delete(_pipeline_url(default_pipeline))
        assert response.status_code == 400
        assert DealPipeline.objects.filter(pk=default_pipeline.pk).exists()

    def test_a_pipeline_holding_deals_cannot_be_deleted(
        self, admin_client, org_a, custom_pipeline
    ):
        Opportunity.objects.create(
            org=org_a, name="Held", pipeline=custom_pipeline, stage="TALKING"
        )
        response = admin_client.delete(_pipeline_url(custom_pipeline))
        assert response.status_code == 400
        assert DealPipeline.objects.filter(pk=custom_pipeline.pk).exists()

    def test_a_stage_holding_deals_cannot_be_deleted(
        self, admin_client, org_a, default_pipeline
    ):
        Opportunity.objects.create(org=org_a, name="Held", stage="PROPOSAL")
        stage = _stage(org_a, "PROPOSAL")
        assert admin_client.delete(_stage_url(stage)).status_code == 400
        assert DealStage.objects.filter(pk=stage.pk).exists()

    @pytest.mark.parametrize("code", ["SIGNED", "CHURNED", "TALKING"])
    def test_the_last_stage_of_a_kind_cannot_be_deleted(
        self, admin_client, custom_pipeline, code
    ):
        stage = custom_pipeline.stages.get(code=code)
        assert admin_client.delete(_stage_url(stage)).status_code == 400
        assert DealStage.objects.filter(pk=stage.pk).exists()

    def test_the_last_stage_of_a_kind_cannot_change_kind(
        self, admin_client, custom_pipeline
    ):
        stage = custom_pipeline.stages.get(code="SIGNED")
        response = admin_client.patch(
            _stage_url(stage), {"kind": "open"}, format="json"
        )
        assert response.status_code == 400
        stage.refresh_from_db()
        assert stage.kind == "won"

    def test_a_stage_holding_deals_cannot_change_kind(
        self, admin_client, org_a, default_pipeline
    ):
        Opportunity.objects.create(org=org_a, name="Held", stage="PROPOSAL")
        stage = _stage(org_a, "PROPOSAL")
        response = admin_client.patch(_stage_url(stage), {"kind": "won"}, format="json")
        assert response.status_code == 400
        stage.refresh_from_db()
        assert stage.kind == "open"

    def test_a_free_stage_may_change_kind_and_loses_its_rotting_days(
        self, admin_client, org_a, default_pipeline
    ):
        stage = _stage(org_a, "PROPOSAL")
        response = admin_client.patch(_stage_url(stage), {"kind": "won"}, format="json")
        assert response.status_code == 200
        stage.refresh_from_db()
        assert (stage.kind, stage.expected_days) == ("won", None)

    def test_duplicate_pipeline_name_is_refused(self, admin_client, custom_pipeline):
        response = admin_client.post(PIPELINES, {"name": "renewals"}, format="json")
        assert response.status_code == 400

    @pytest.mark.parametrize(
        "body",
        [
            {"label": "", "kind": "open"},
            {"label": "X", "kind": "closed"},
            {"label": "X", "kind": "open", "expected_days": 0},
            {"label": "X", "kind": "open", "expected_days": "soon"},
            {"label": "X" * 101, "kind": "open"},
        ],
    )
    def test_bad_stage_input_is_a_400(self, admin_client, custom_pipeline, body):
        url = f"{_pipeline_url(custom_pipeline)}stages/"
        assert admin_client.post(url, body, format="json").status_code == 400
        assert custom_pipeline.stages.count() == 3

    def test_new_stage_codes_never_collide(self, admin_client, custom_pipeline):
        url = f"{_pipeline_url(custom_pipeline)}stages/"
        response = admin_client.post(
            url, {"label": "Talking!", "kind": "open"}, format="json"
        )
        assert response.status_code == 201
        codes = list(custom_pipeline.stages.values_list("code", flat=True))
        assert "TALKING_2" in codes and len(codes) == len(set(codes))

    def test_duplicate_stage_label_in_a_pipeline_is_refused(
        self, admin_client, custom_pipeline
    ):
        url = f"{_pipeline_url(custom_pipeline)}stages/"
        response = admin_client.post(
            url, {"label": "talking", "kind": "open"}, format="json"
        )
        assert response.status_code == 400
        signed = custom_pipeline.stages.get(code="SIGNED")
        response = admin_client.patch(
            _stage_url(signed), {"label": "Churned"}, format="json"
        )
        assert response.status_code == 400
        # Its own name back is not a clash.
        response = admin_client.patch(
            _stage_url(signed), {"label": "Signed"}, format="json"
        )
        assert response.status_code == 200


@pytest.mark.django_db
class TestReorder:
    def _ids(self, pipeline):
        return [str(s.id) for s in pipeline.stages.all()]

    def test_full_set_in_new_order_is_saved(self, admin_client, custom_pipeline):
        ids = self._ids(custom_pipeline)
        new = [ids[2], ids[0], ids[1]]
        response = admin_client.post(
            _reorder_url(custom_pipeline), {"stage_ids": new}, format="json"
        )
        assert response.status_code == 200
        assert self._ids(custom_pipeline) == new

    @pytest.mark.parametrize(
        "mangle",
        [
            lambda ids: ids[:-1],  # partial
            lambda ids: ids + [ids[0]],  # duplicate
            lambda ids: [ids[0], ids[0], ids[1]],  # duplicate hiding a missing one
            lambda ids: ["not-a-uuid"],  # malformed
            lambda ids: [],  # empty
        ],
    )
    def test_anything_but_the_exact_set_is_a_400(
        self, admin_client, custom_pipeline, mangle
    ):
        before = self._ids(custom_pipeline)
        response = admin_client.post(
            _reorder_url(custom_pipeline),
            {"stage_ids": mangle(before)},
            format="json",
        )
        assert response.status_code == 400
        assert self._ids(custom_pipeline) == before

    def test_a_stage_of_another_pipeline_is_a_400(
        self, admin_client, custom_pipeline, default_pipeline
    ):
        ids = self._ids(custom_pipeline)
        ids[0] = str(default_pipeline.stages.first().id)
        response = admin_client.post(
            _reorder_url(custom_pipeline), {"stage_ids": ids}, format="json"
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestDealContract:
    def test_old_client_body_lands_in_default_pipeline(self, admin_client, org_a):
        response = admin_client.post(
            DEALS, {"name": "Legacy", "stage": "NEGOTIATION"}, format="json"
        )
        assert response.status_code == 200, response.data
        deal = Opportunity.objects.get(name="Legacy")
        assert deal.pipeline == DealPipeline.default_for(org_a)
        assert deal.stage == "NEGOTIATION"

    def test_no_stage_starts_in_first_open_stage(self, admin_client, custom_pipeline):
        response = admin_client.post(
            DEALS,
            {"name": "Fresh", "pipeline": str(custom_pipeline.id)},
            format="json",
        )
        assert response.status_code == 200, response.data
        assert Opportunity.objects.get(name="Fresh").stage == "TALKING"

    def test_a_stage_of_another_pipeline_is_a_400(self, admin_client, custom_pipeline):
        response = admin_client.post(
            DEALS,
            {
                "name": "Mismatch",
                "pipeline": str(custom_pipeline.id),
                "stage": "PROSPECTING",
            },
            format="json",
        )
        assert response.status_code == 400
        assert "stage" in response.data["errors"]

    def test_moving_pipelines_needs_a_stage_there(
        self, admin_client, org_a, custom_pipeline
    ):
        deal = Opportunity.objects.create(org=org_a, name="Mover", stage="PROPOSAL")
        url = f"{DEALS}{deal.id}/"

        response = admin_client.patch(
            url, {"pipeline": str(custom_pipeline.id)}, format="json"
        )
        assert response.status_code == 400
        deal.refresh_from_db()
        assert deal.pipeline.is_default

        response = admin_client.patch(
            url,
            {"pipeline": str(custom_pipeline.id), "stage": "TALKING"},
            format="json",
        )
        assert response.status_code == 200, response.data
        deal.refresh_from_db()
        assert (deal.pipeline, deal.stage) == (custom_pipeline, "TALKING")

    def test_serializer_exposes_pipeline_label_and_kind(
        self, admin_client, org_a, custom_pipeline
    ):
        deal = Opportunity.objects.create(
            org=org_a,
            name="Won renewal",
            pipeline=custom_pipeline,
            stage="SIGNED",
            amount=Decimal("10"),
            closed_on=timezone.localdate(),
        )
        data = admin_client.get(f"{DEALS}{deal.id}/").data
        obj = data["opportunity_obj"]
        assert (str(obj["pipeline"]), obj["stage_label"], obj["stage_kind"]) == (
            str(custom_pipeline.id),
            "Signed",
            "won",
        )
        assert data["stage"] == [
            ("TALKING", "Talking"),
            ("SIGNED", "Signed"),
            ("CHURNED", "Churned"),
        ]

    def test_custom_won_stage_needs_amount_and_close_date(
        self, admin_client, org_a, custom_pipeline
    ):
        deal = Opportunity.objects.create(
            org=org_a, name="Nil win", pipeline=custom_pipeline, stage="TALKING"
        )
        response = admin_client.patch(
            f"{DEALS}{deal.id}/", {"stage": "SIGNED"}, format="json"
        )
        assert response.status_code == 400
        assert {"amount", "closed_on"} <= set(response.data["errors"])

    def test_closing_into_a_custom_won_stage_records_the_closer(
        self, admin_client, admin_profile, org_a, custom_pipeline
    ):
        deal = Opportunity.objects.create(
            org=org_a, name="Closer", pipeline=custom_pipeline, stage="TALKING"
        )
        response = admin_client.patch(
            f"{DEALS}{deal.id}/",
            {"stage": "SIGNED", "amount": 50, "closed_on": "2026-09-01"},
            format="json",
        )
        assert response.status_code == 200, response.data
        deal.refresh_from_db()
        assert deal.closed_by == admin_profile
        assert deal.probability == 100


@pytest.mark.django_db
class TestKanban:
    def test_board_is_the_chosen_pipelines_stages(
        self, admin_client, org_a, custom_pipeline
    ):
        Opportunity.objects.create(org=org_a, name="In default", stage="PROPOSAL")
        Opportunity.objects.create(
            org=org_a, name="In custom", pipeline=custom_pipeline, stage="TALKING"
        )

        default = admin_client.get("/api/opportunities/kanban/").data
        assert default["pipeline"]["is_default"] is True
        assert [c["id"] for c in default["columns"]][-2:] == [
            "CLOSED_WON",
            "CLOSED_LOST",
        ]
        assert default["total_items"] == 1

        custom = admin_client.get(
            f"/api/opportunities/kanban/?pipeline={custom_pipeline.id}"
        ).data
        assert [(c["id"], c["name"], c["kind"]) for c in custom["columns"]] == [
            ("TALKING", "Talking", "open"),
            ("SIGNED", "Signed", "won"),
            ("CHURNED", "Churned", "lost"),
        ]
        (card,) = custom["columns"][0]["items"]
        assert (card["name"], card["stage_label"]) == ("In custom", "Talking")

    def test_move_to_a_column_of_another_pipeline_is_a_400(
        self, admin_client, org_a, custom_pipeline
    ):
        deal = Opportunity.objects.create(
            org=org_a, name="Stay", pipeline=custom_pipeline, stage="TALKING"
        )
        response = admin_client.patch(
            f"/api/opportunities/{deal.id}/move/",
            {"column_id": "NEGOTIATION"},
            format="json",
        )
        assert response.status_code == 400
        deal.refresh_from_db()
        assert deal.stage == "TALKING"

    def test_dragging_into_a_custom_lost_stage_closes_and_back_reopens(
        self, admin_client, admin_profile, org_a, custom_pipeline
    ):
        deal = Opportunity.objects.create(
            org=org_a, name="Churn", pipeline=custom_pipeline, stage="TALKING"
        )
        url = f"/api/opportunities/{deal.id}/move/"
        assert (
            admin_client.patch(url, {"column_id": "CHURNED"}, format="json").status_code
            == 200
        )
        deal.refresh_from_db()
        assert deal.closed_by == admin_profile and deal.closed_on is not None
        assert deal.probability == 0

        admin_client.patch(url, {"column_id": "TALKING"}, format="json")
        deal.refresh_from_db()
        assert deal.closed_by is None


@pytest.mark.django_db
class TestWonIsAKind:
    """A custom-named won stage counts as won in every figure that reads wins."""

    @pytest.fixture
    def signed(self, org_a, custom_pipeline):
        return Opportunity.objects.create(
            org=org_a,
            name="Signed deal",
            pipeline=custom_pipeline,
            stage="SIGNED",
            amount=Decimal("700"),
            currency=org_a.default_currency or "USD",
            closed_on=timezone.localdate(),
        )

    @pytest.fixture
    def churned(self, org_a, custom_pipeline):
        return Opportunity.objects.create(
            org=org_a,
            name="Churned deal",
            pipeline=custom_pipeline,
            stage="CHURNED",
            amount=Decimal("300"),
            closed_on=timezone.localdate(),
        )

    def test_dashboard(self, admin_client, signed, churned, org_a):
        Opportunity.objects.create(
            org=org_a,
            name="Talking deal",
            pipeline=signed.pipeline,
            stage="TALKING",
            amount=Decimal("50"),
        )
        metrics = admin_client.get("/api/dashboard/").data["revenue_metrics"]
        assert metrics["won_this_month"] == 700
        assert metrics["open_opportunities_count"] == 1
        assert metrics["pipeline_value"] == 50

    def test_open_filter_excludes_custom_closed_stages(
        self, admin_client, signed, churned
    ):
        names = {
            o["name"]
            for o in admin_client.get(f"{DEALS}?open=true").data["opportunities"]
        }
        assert not names & {"Signed deal", "Churned deal"}

    def test_goals(self, org_a, signed, churned):
        today = timezone.localdate()
        goal = SalesGoal.objects.create(
            org=org_a,
            name="Deals",
            goal_type="DEALS_CLOSED",
            target_value=Decimal("5"),
            period_type="CUSTOM",
            period_start=today - timedelta(days=1),
            period_end=today + timedelta(days=1),
        )
        assert goal.compute_progress() == Decimal("1")
        (batched,) = SalesGoal.attach_progress([SalesGoal.objects.get(pk=goal.pk)])
        assert batched._cached_progress == Decimal("1")

    def test_account_rollups(self, admin_client, org_a, signed, churned):
        account = Account.objects.create(name="Acme", org=org_a)
        Opportunity.objects.filter(pk__in=[signed.pk, churned.pk]).update(
            account=account
        )
        rollups = admin_client.get(f"/api/accounts/{account.id}/").json()[
            "account_obj"
        ]["rollups"]
        assert rollups["won_count"] == 1
        assert rollups["open_deal_count"] == 0

    def test_invoice_from_a_custom_won_stage(
        self, admin_client, org_a, signed, custom_pipeline
    ):
        account = Account.objects.create(name="Billed", org=org_a)
        contact = Contact.objects.create(first_name="Bo", last_name="B", org=org_a)
        signed.account = account
        signed.save()
        signed.contacts.add(contact)
        OpportunityLineItem.objects.create(
            opportunity=signed, org=org_a, name="W", unit_price=Decimal("1")
        )
        talking = Opportunity.objects.create(
            org=org_a, name="Not yet", pipeline=custom_pipeline, stage="TALKING"
        )

        refused = admin_client.post(f"/api/invoices/from-opportunity/{talking.id}/")
        assert refused.status_code == 400

        response = admin_client.post(f"/api/invoices/from-opportunity/{signed.id}/")
        assert response.status_code == 201, response.content


@pytest.mark.django_db
class TestRotting:
    def test_per_stage_days_drive_status_and_filters(
        self, admin_client, org_a, custom_pipeline
    ):
        # TALKING expects 5 days, so red (rotting) at ceil(7.5) = 8.
        for name, days in (("Fresh", 2), ("Aging", 6), ("Rotting", 8)):
            deal = Opportunity.objects.create(
                org=org_a, name=name, pipeline=custom_pipeline, stage="TALKING"
            )
            Opportunity.objects.filter(pk=deal.pk).update(
                stage_changed_at=timezone.now() - timedelta(days=days)
            )

        rows = {
            o["name"]: o["aging_status"]
            for o in admin_client.get(DEALS).data["opportunities"]
        }
        assert rows == {"Fresh": "green", "Aging": "yellow", "Rotting": "red"}

        rotten = admin_client.get(f"{DEALS}?rotten=true").data
        assert [o["name"] for o in rotten["opportunities"]] == ["Rotting"]
        assert rotten["totals"]["stalled_count"] == 1

        board = admin_client.get(
            f"/api/opportunities/kanban/?pipeline={custom_pipeline.id}"
        ).data
        statuses = {c["name"]: c["aging_status"] for c in board["columns"][0]["items"]}
        assert statuses == rows

    def test_list_stage_queries_do_not_grow_with_rows(self, admin_client, org_a):
        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                assert admin_client.get(DEALS).status_code == 200
            return len(
                [
                    q
                    for q in ctx.captured_queries
                    if "opportunity_pipeline_stage" in q["sql"]
                ]
            )

        Opportunity.objects.create(org=org_a, name="One")
        few = count_queries()
        for i in range(6):
            Opportunity.objects.create(org=org_a, name=f"More {i}")
        assert count_queries() == few


@pytest.mark.django_db(transaction=True)
class TestMigration:
    """opportunity/0020 against a database as 0019 left it."""

    def _migrate(self, target):
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("opportunity", target)])
        return executor.loader.project_state(("opportunity", target)).apps

    def test_default_pipeline_created_deals_assigned_aging_carried(self):
        old = self._migrate("0019_enable_rls_deal_pipelines")
        OldOpp = old.get_model("opportunity", "Opportunity")
        OldAging = old.get_model("opportunity", "StageAgingConfig")
        # The live Org, not the historical one: only `opportunity` is rolled
        # back, so `organization` has every column the current model has.
        org = Org.objects.create(name="Legacy Co")
        OldAging.objects.create(
            org_id=org.pk, stage="PROPOSAL", expected_days=4, warning_days=2
        )
        OldOpp.objects.create(org_id=org.pk, name="Old win", stage="CLOSED_WON")
        OldOpp.objects.create(org_id=org.pk, name="Old open", stage="PROPOSAL")

        try:
            self._migrate("0021_pipeline_required_drop_stage_aging")

            org = Org.objects.get(pk=org.pk)
            pipeline = DealPipeline.objects.get(org=org)
            assert pipeline.is_default
            stages = {s.code: s for s in pipeline.stages.all()}
            assert list(stages) == [
                "PROSPECTING",
                "QUALIFICATION",
                "PROPOSAL",
                "NEGOTIATION",
                "CLOSED_WON",
                "CLOSED_LOST",
            ]
            assert stages["CLOSED_WON"].kind == "won"
            assert stages["CLOSED_LOST"].kind == "lost"
            assert stages["PROPOSAL"].kind == "open"
            # The org's own config wins; unconfigured stages keep the defaults.
            assert (
                stages["PROPOSAL"].expected_days,
                stages["PROPOSAL"].warning_days,
            ) == (
                4,
                2,
            )
            assert stages["PROSPECTING"].expected_days == 14
            assert stages["NEGOTIATION"].expected_days == 10
            assert set(
                Opportunity.objects.filter(org=org).values_list("pipeline", flat=True)
            ) == {pipeline.id}
        finally:
            # Leave the schema at the head for whatever runs next.
            from django.core.management import call_command

            call_command("migrate", verbosity=0)
            Org.objects.filter(name="Legacy Co").delete()

    def test_deals_created_after_0020_do_not_block_0021(self):
        """The old code keeps serving between 0020 and 0021, so it can add a
        deal with no pipeline, in an org 0020 covered or in a new one."""
        from django.core.management import call_command

        old = self._migrate("0019_enable_rls_deal_pipelines")
        covered = Org.objects.create(name="Covered Co")
        old.get_model("opportunity", "Opportunity").objects.create(
            org_id=covered.pk, name="Before", stage="PROPOSAL"
        )
        try:
            mid = self._migrate("0020_default_deal_pipelines")
            MidOpp = mid.get_model("opportunity", "Opportunity")
            late = MidOpp.objects.create(
                org_id=covered.pk, name="Late", stage="PROSPECTING"
            )
            newcomer = Org.objects.create(name="Newcomer Co")
            fresh = MidOpp.objects.create(
                org_id=newcomer.pk, name="Fresh", stage="PROSPECTING"
            )
            assert late.pipeline_id is None and fresh.pipeline_id is None

            self._migrate("0021_pipeline_required_drop_stage_aging")

            covered_default = DealPipeline.objects.get(org=covered, is_default=True)
            assert Opportunity.objects.get(pk=late.pk).pipeline == covered_default
            assert DealPipeline.objects.filter(org=covered).count() == 1
            newcomer_default = DealPipeline.objects.get(org=newcomer, is_default=True)
            assert Opportunity.objects.get(pk=fresh.pk).pipeline == newcomer_default
        finally:
            call_command("migrate", verbosity=0)
            Org.objects.filter(name__in=["Covered Co", "Newcomer Co"]).delete()

    def test_an_old_zero_expected_days_still_ages(self):
        """Old 0 meant red at once; on DealStage 0 would mean never ages."""
        from django.core.management import call_command

        old = self._migrate("0019_enable_rls_deal_pipelines")
        org = Org.objects.create(name="Zero Days")
        old.get_model("opportunity", "StageAgingConfig").objects.create(
            org_id=org.pk, stage="QUALIFICATION", expected_days=0
        )
        try:
            self._migrate("0021_pipeline_required_drop_stage_aging")

            stage = DealStage.objects.get(org=org, code="QUALIFICATION")
            assert stage.expected_days == 1
        finally:
            call_command("migrate", verbosity=0)
            Org.objects.filter(pk=org.pk).delete()

    def test_reverse_writes_the_aging_back(self):
        self._migrate("0021_pipeline_required_drop_stage_aging")
        org = Org.objects.create(name="Round Trip")
        from common.testing import set_rls_context

        set_rls_context(org)
        DealPipeline.default_for(org)
        DealStage.objects.filter(org=org, code="QUALIFICATION").update(
            expected_days=9, warning_days=None
        )
        try:
            old = self._migrate("0019_enable_rls_deal_pipelines")
            OldAging = old.get_model("opportunity", "StageAgingConfig")
            assert (
                OldAging.objects.get(org_id=org.pk, stage="QUALIFICATION").expected_days
                == 9
            )
        finally:
            from django.core.management import call_command

            call_command("migrate", verbosity=0)
            Org.objects.filter(pk=org.pk).delete()


@pytest.mark.django_db
def test_deleting_the_org_takes_its_pipelines_and_deals(org_a, custom_pipeline):
    """RESTRICT on `Opportunity.pipeline` stops a lone pipeline delete, never
    the org's own cascade."""
    Opportunity.objects.create(
        org=org_a, name="Goes too", pipeline=custom_pipeline, stage="TALKING"
    )
    org_a.delete()
    assert not DealPipeline.objects.filter(org_id=org_a.pk).exists()
    assert not Opportunity.objects.filter(org_id=org_a.pk).exists()


def test_pipeline_tables_are_registered_as_org_scoped():
    from common.rls import ORG_SCOPED_TABLES

    assert {"opportunity_pipeline", "opportunity_pipeline_stage"} <= set(
        ORG_SCOPED_TABLES
    )
    assert "stage_aging_config" not in ORG_SCOPED_TABLES


@pytest.mark.postgres_only
@pytest.mark.django_db
def test_pipeline_tables_have_rls_policies():
    if connection.vendor != "postgresql":
        pytest.skip("RLS requires PostgreSQL")
    with connection.cursor() as cursor:
        for table in ("opportunity_pipeline", "opportunity_pipeline_stage"):
            cursor.execute(
                """
                SELECT c.relrowsecurity,
                       (SELECT COUNT(*) FROM pg_policy p WHERE p.polrelid = c.oid)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname = %s
                """,
                [table],
            )
            enabled, policies = cursor.fetchone()
            assert enabled and policies >= 2, table
