"""`LeadStage.win_probability` is bounded to 0-100 at the database (D11).

The board move copies it onto `Lead.probability`, whose `lead_probability_range`
check made an out-of-range stage a 500 on every move into it. The serializer
bound is pinned in `test_leads_kanban.py::TestLeadStageWinProbabilityBounds`;
this file pins the check constraint and `leads/0017`'s clamp.
"""

import importlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.apps import apps as global_apps
from django.db import IntegrityError, connection, transaction

from leads.models import Lead, LeadPipeline, LeadStage

CONSTRAINT = "lead_stage_win_probability_range"


def _set_rls(org):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_org', %s, false)", [str(org.id)])


def _stage(org, created_by, name, win_probability=0):
    pipeline, _ = LeadPipeline.objects.get_or_create(
        name="Sales", org=org, defaults={"created_by": created_by}
    )
    return LeadStage.objects.create(
        pipeline=pipeline,
        name=name,
        org=org,
        win_probability=win_probability,
    )


@contextmanager
def _check_suspended():
    """Let a test store a row as production may hold one from before the check.

    SQLite cannot drop a constraint inside the test transaction, but it can be
    told to skip CHECKs. Postgres drops it; the test transaction's rollback
    restores it, and re-adding it here also proves the rows now satisfy it.
    """
    with connection.cursor() as cursor:
        if connection.vendor == "sqlite":
            cursor.execute("PRAGMA ignore_check_constraints = ON")
            try:
                yield
            finally:
                cursor.execute("PRAGMA ignore_check_constraints = OFF")
        else:
            cursor.execute(f"ALTER TABLE lead_stage DROP CONSTRAINT {CONSTRAINT}")
            yield
            # Rows inserted and updated in this transaction leave deferred FK
            # checks queued, and Postgres refuses ALTER TABLE while they are.
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(
                f"ALTER TABLE lead_stage ADD CONSTRAINT {CONSTRAINT} "
                "CHECK (win_probability >= 0 AND win_probability <= 100)"
            )


@pytest.mark.django_db
class TestConstraint:
    @pytest.mark.parametrize("value", [-1, 101, 150])
    def test_out_of_range_is_refused_by_the_database(self, admin_user, org_a, value):
        _set_rls(org_a)
        stage = _stage(org_a, admin_user, "New")

        # .update() skips every Python-side check, so only the DB can refuse it.
        with pytest.raises(IntegrityError), transaction.atomic():
            LeadStage.objects.filter(pk=stage.pk).update(win_probability=value)

        stage.refresh_from_db()
        assert stage.win_probability == 0

    @pytest.mark.parametrize("value", [0, 100])
    def test_the_bounds_save(self, admin_user, org_a, value):
        _set_rls(org_a)
        stage = _stage(org_a, admin_user, "Edge", win_probability=value)

        stage.refresh_from_db()
        assert stage.win_probability == value


@pytest.mark.django_db
class TestClampMigration:
    """`leads/0017`'s data step, called directly as `common/tests/test_tag_slugs.py`
    calls `common/0042`. The live models stand in for the historical ones,
    which is sound while `LeadStage` has the same fields as at 0017."""

    def _clamp(self):
        module = importlib.import_module(
            "leads.migrations.0017_leadstage_win_probability_range"
        )
        module.clamp_win_probability(
            global_apps, SimpleNamespace(connection=connection)
        )

    def test_out_of_range_rows_are_clamped_in_every_org(self, admin_user, org_a, org_b):
        with _check_suspended():
            _set_rls(org_a)
            low = _stage(org_a, admin_user, "Low", -5)
            high = _stage(org_a, admin_user, "High", 150)
            zero = _stage(org_a, admin_user, "Zero", 0)
            mid = _stage(org_a, admin_user, "Mid", 50)
            hundred = _stage(org_a, admin_user, "Hundred", 100)
            _set_rls(org_b)
            other = _stage(org_b, admin_user, "Other org", 150)

            self._clamp()

        got = {}
        for org, stage in [
            (org_a, low),
            (org_a, high),
            (org_a, zero),
            (org_a, mid),
            (org_a, hundred),
            (org_b, other),
        ]:
            _set_rls(org)
            got[stage.name] = LeadStage.objects.get(pk=stage.pk).win_probability
        assert got == {
            "Low": 0,
            "High": 100,
            "Zero": 0,
            "Mid": 50,
            "Hundred": 100,
            "Other org": 100,
        }

    def test_a_move_into_a_clamped_stage_succeeds(
        self, admin_client, admin_user, org_a
    ):
        with _check_suspended():
            _set_rls(org_a)
            stage = _stage(org_a, admin_user, "Was 150", 150)
            self._clamp()
        _set_rls(org_a)
        lead = Lead.objects.create(
            first_name="Board",
            last_name="Lead",
            email="clamped@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
        )

        response = admin_client.patch(
            f"/api/leads/{lead.id}/move/", {"stage_id": str(stage.id)}, format="json"
        )

        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.stage_id == stage.id
        assert lead.probability == 100


@pytest.mark.django_db
class TestMoveAtTheBounds:
    @pytest.mark.parametrize("value", [0, 1, 99, 100])
    def test_a_move_into_any_storable_stage_succeeds(
        self, admin_client, admin_user, org_a, value
    ):
        _set_rls(org_a)
        stage = _stage(org_a, admin_user, "Target", value)
        lead = Lead.objects.create(
            first_name="Board",
            last_name="Lead",
            email="bounds@example.com",
            status="assigned",
            created_by=admin_user,
            org=org_a,
        )

        response = admin_client.patch(
            f"/api/leads/{lead.id}/move/", {"stage_id": str(stage.id)}, format="json"
        )

        assert response.status_code == 200
        lead.refresh_from_db()
        assert lead.stage_id == stage.id
        assert lead.probability == value
