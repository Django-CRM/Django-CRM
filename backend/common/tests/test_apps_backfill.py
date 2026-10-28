"""The data migration that records orgs which applied a pack before apps
existed as having installed it (``common/migrations/0062``).

Reports written then carry names, not natural keys or ids, so these fixtures
apply a pack with today's applier and strip what it adds now.
"""

import importlib
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.apps import apps as live_apps
from django.db import connection
from django.utils import timezone

from common.models import InstalledApp, PackApplication, Tags
from common.packs.applier import apply_pack
from common.packs.loader import get_pack
from common.packs.planner import plan_app
from common.testing import restore_rls_context, rls_org, set_modules_off
from invoices.models import Product
from leads.models import LeadPipeline, LeadSource

migration = importlib.import_module("common.migrations.0062_backfill_installed_apps")
APP = "real-estate"


def _old_run(org, actor, pack_id=APP, version=None, when=None):
    """Apply a pack and leave its run as the applier wrote it before apps."""
    pack = dict(get_pack(pack_id))
    if version is not None:
        pack["version"] = version
    apply_pack(org, pack, actor)
    run = PackApplication.objects.filter(org=org, pack_id=pack_id).first()
    run.result.pop("offered")
    run.save(update_fields=["result"])
    if when is not None:
        PackApplication.objects.filter(pk=run.pk).update(created_at=when)
        run.refresh_from_db()
    return run


def _backfill():
    migration.backfill_installed_apps(live_apps, SimpleNamespace(connection=connection))
    restore_rls_context()


def _keys(entries, type_):
    return {e["key"] for e in entries if e["type"] == type_}


def test_an_org_without_runs_gets_no_row(org_a, org_b):
    _backfill()

    assert not InstalledApp.objects.exists()


def test_one_run_becomes_an_installed_app(org_a, admin_profile):
    run = _old_run(org_a, admin_profile)
    pack = get_pack(APP)

    _backfill()

    row = InstalledApp.objects.get(org=org_a)
    assert (row.app_id, row.status, row.installed_version) == (
        APP,
        "installed",
        run.pack_version,
    )
    assert row.installed_by == admin_profile
    assert row.installed_at == run.created_at
    assert row.removed_at is None
    assert _keys(row.owned, "pipeline") == {
        "Sales",
        "Service requests",
        "Agent workflow",
    }
    assert _keys(row.owned, "field") == {f["key"] for f in pack["custom_fields"]}
    assert _keys(row.owned, "wording") == set(pack["terminology"])
    assert _keys(row.owned, "product") == {p["sku"] for p in pack["products"]}
    assert _keys(row.owned, "source") == {"property portal"}
    assert "Sales/Booked" in _keys(row.owned, "stage")
    pipeline = LeadPipeline.objects.get(org=org_a, name="Sales")
    assert {
        "type": "pipeline",
        "target": "lead",
        "key": "Sales",
        "id": str(pipeline.id),
    } in row.owned
    assert all(e["skipped_module"] is None for e in row.offered)
    assert "Sales/Site visit" in _keys(row.offered, "stage")

    # It now behaves as an install: nothing new to update, and removal works.
    assert InstalledApp.objects.filter(org=org_a).count() == 1
    assert plan_app(org_a, APP, "remove")["items"]


def test_two_runs_of_one_pack_make_one_row_at_the_latest(
    org_a, admin_profile, user_profile
):
    first = _old_run(
        org_a, admin_profile, version=1, when=timezone.now() - timedelta(days=3)
    )
    Tags.objects.get(org=org_a, slug="hot").delete()
    latest = _old_run(org_a, user_profile)
    assert latest.created_at > first.created_at
    assert latest.result["created"] == [{"type": "tag", "name": "Hot"}]

    _backfill()

    row = InstalledApp.objects.get(org=org_a)
    assert row.installed_version == latest.pack_version
    assert row.installed_by == user_profile
    assert row.installed_at == latest.created_at
    hot = Tags.objects.get(org=org_a, slug="hot")
    assert {"type": "tag", "target": None, "key": "hot", "id": str(hot.id)} in row.owned


def test_items_the_org_deleted_are_not_owned_but_stay_offered(org_a, admin_profile):
    _old_run(org_a, admin_profile)
    Tags.objects.get(org=org_a, slug="investor").delete()
    Product.objects.get(org=org_a, sku="RE-STAGING").delete()
    LeadPipeline.objects.get(org=org_a, name="Sales").delete()

    _backfill()

    row = InstalledApp.objects.get(org=org_a)
    assert "investor" not in _keys(row.owned, "tag")
    assert "RE-STAGING" not in _keys(row.owned, "product")
    assert "Sales" not in _keys(row.owned, "pipeline")
    assert not _keys(row.owned, "stage") & {"Sales/Booked"}
    assert {"investor", "hot"} <= _keys(row.offered, "tag")
    assert "RE-STAGING" in _keys(row.offered, "product")
    assert "Sales" in _keys(row.offered, "pipeline")


def test_two_packs_make_two_rows_and_shared_wording_belongs_to_the_first(
    org_a, admin_profile
):
    _old_run(org_a, admin_profile, when=timezone.now() - timedelta(days=1))
    _old_run(org_a, admin_profile, pack_id="events-weddings")

    _backfill()

    rows = {r.app_id: r for r in InstalledApp.objects.filter(org=org_a)}
    assert set(rows) == {APP, "events-weddings"}
    assert "lead.plural" in _keys(rows[APP].owned, "wording")
    assert "lead.plural" not in _keys(rows["events-weddings"].owned, "wording")
    assert "lead.plural" in _keys(rows["events-weddings"].offered, "wording")
    assert "account.plural" in _keys(rows["events-weddings"].owned, "wording")
    assert "hot" not in _keys(rows["events-weddings"].offered, "tag")


def test_a_run_behind_the_catalogue_sees_an_update(admin_client, org_a, admin_profile):
    """Version 1 lacked a tag that later versions have."""
    run = _old_run(org_a, admin_profile, version=1)
    run.result["created"] = [e for e in run.result["created"] if e["name"] != "Hot"]
    run.save(update_fields=["result"])
    Tags.objects.filter(org=org_a, slug="hot").delete()

    _backfill()

    app = next(
        a for a in admin_client.get("/api/apps/").json()["apps"] if a["id"] == APP
    )
    assert (app["status"], app["installed_version"], app["update_available"]) == (
        "installed",
        1,
        True,
    )
    assert admin_client.post(f"/api/apps/{APP}/upgrade/").status_code == 200
    row = InstalledApp.objects.get(org=org_a)
    assert row.installed_version == get_pack(APP)["version"]
    assert Tags.objects.filter(org=org_a, slug="hot").exists()


def test_a_part_never_offered_then_is_offered_by_an_update(org_a, admin_profile):
    """The lead source joined the manifest after some orgs applied it."""
    run = _old_run(org_a, admin_profile)
    run.result["created"] = [
        e for e in run.result["created"] if e["type"] != "lead_source"
    ]
    run.save(update_fields=["result"])
    LeadSource.objects.filter(org=org_a, key="property portal").delete()

    _backfill()

    items = plan_app(org_a, APP, "upgrade")["items"]
    # Reports from before apps name no deal pipeline, as no pack had one then.
    assert [i for i in items if i["target"] != "deal"] == [
        {"type": "source", "target": None, "label": "Property portal", "effect": "add"}
    ]


def test_a_part_the_org_made_again_after_the_run_is_not_owned(org_a, admin_profile):
    """Only a row that existed when the run that created it ended can be the
    app's: the org's own product, tag or pipeline under the same key is not."""
    from common.apps_catalogue import remove_app

    run = _old_run(org_a, admin_profile)
    Product.objects.filter(org=org_a, sku="RE-STAGING").delete()
    Tags.objects.filter(org=org_a, slug="investor").delete()
    LeadPipeline.objects.filter(org=org_a, name="Sales").delete()
    product = Product.objects.create(org=org_a, name="Our staging", sku="RE-STAGING")
    tag = Tags.objects.create(org=org_a, name="Investor")
    pipeline = LeadPipeline.objects.create(org=org_a, name="Sales")
    assert min(product.created_at, tag.created_at, pipeline.created_at) > (
        run.created_at
    )

    _backfill()

    row = InstalledApp.objects.get(org=org_a)
    owned_ids = {e["id"] for e in row.owned}
    assert not owned_ids & {str(product.id), str(tag.id), str(pipeline.id)}
    assert "RE-VALUATION" in _keys(row.owned, "product")
    assert "Sales" not in _keys(row.owned, "pipeline")

    remove_app(org_a, APP, admin_profile)

    product.refresh_from_db()
    assert product.is_active
    assert not Product.objects.get(org=org_a, sku="RE-VALUATION").is_active


def test_parts_skipped_for_an_off_module_are_marked(org_a, admin_profile):
    set_modules_off(org_a, "tickets", "deals", "invoices")
    _old_run(org_a, admin_profile)

    _backfill()

    row = InstalledApp.objects.get(org=org_a)
    skipped = {(e["type"], e["key"]): e["skipped_module"] for e in row.offered}
    assert skipped[("pipeline", "Service requests")] == "tickets"
    assert skipped[("stage", "Service requests/With agent")] == "tickets"
    assert skipped[("product", "RE-VALUATION")] == "invoices"
    assert skipped[("pipeline", "Sales")] is None


def test_product_names_from_before_a_rename_still_resolve(org_a, admin_profile):
    run = _old_run(org_a, admin_profile, pack_id="education-admissions")
    for entry in run.result["created"]:
        if entry["name"] == "SAT Prep, 12 week":
            entry["name"] = f"SAT Prep {chr(0x2014)} 12 week"
    run.save(update_fields=["result"])

    _backfill()

    row = InstalledApp.objects.get(org=org_a)
    assert "ED-SAT-12W" in _keys(row.owned, "product")


def test_an_existing_row_and_an_unknown_pack_are_left_alone(org_a, admin_profile):
    _old_run(org_a, admin_profile)
    PackApplication.objects.create(
        org=org_a, pack_id="gone", pack_version=1, result={"created": []}
    )
    _backfill()
    row = InstalledApp.objects.get(org=org_a)
    InstalledApp.objects.filter(pk=row.pk).update(status="removed")

    _backfill()

    assert list(InstalledApp.objects.values_list("app_id", "status")) == [
        (APP, "removed")
    ]


def test_reverse_deletes_only_rows_nothing_has_touched(
    org_a, org_b, admin_profile, profile_b
):
    _old_run(org_a, admin_profile)
    _old_run(org_a, admin_profile, pack_id="events-weddings")
    with rls_org(org_b):
        _old_run(org_b, profile_b)
    _backfill()
    from common.apps_catalogue import remove_app

    remove_app(org_a, "events-weddings", admin_profile)

    migration.remove_backfilled_apps(live_apps, SimpleNamespace(connection=connection))
    restore_rls_context()

    assert list(InstalledApp.objects.values_list("app_id", flat=True)) == [
        "events-weddings"
    ]
    with rls_org(org_b):
        assert not InstalledApp.objects.filter(org=org_b).exists()


@pytest.mark.postgres_only
def test_the_backfill_reads_each_org_under_its_own_context(
    org_a, org_b, admin_profile, profile_b
):
    if connection.vendor != "postgresql":
        pytest.skip("Row-level security is a PostgreSQL feature")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        if cursor.fetchone()[0]:
            pytest.skip("Needs a role row-level security binds, as in CI")
    _old_run(org_a, admin_profile)
    with rls_org(org_b):
        _old_run(org_b, profile_b, pack_id="events-weddings")

    _backfill()

    assert list(InstalledApp.objects.values_list("app_id", flat=True)) == [APP]
    assert _keys(InstalledApp.objects.get().owned, "pipeline")
    with rls_org(org_b):
        row = InstalledApp.objects.get()
        assert row.app_id == "events-weddings"
        assert _keys(row.owned, "pipeline")
