"""Deal pipelines as a pack part, and the Real Estate app's version 3.

A pack's ``deal_pipelines`` are created beside the org's own, with their
stages, when no deal pipeline of that name exists; they never become the
default, are skipped while the Deals module is off, and stay on removal.
"""

import copy
from unittest import mock

import pytest

from common.apps_catalogue import install_app, remove_app, upgrade_app
from common.models import InstalledApp
from common.packs.loader import get_pack, get_registry
from common.packs.planner import has_update, plan_app
from common.packs.schema import PackValidationError, validate_manifest
from common.testing import set_modules_off
from opportunity.models import DealPipeline, DealStage, Opportunity

APP = "real-estate"
DEALS = "/api/opportunities/"

# (code, label, kind, expected_days), in board order.
EXPECTED = {
    "Sale": [
        ("viewing", "Viewing", "open", 7),
        ("offer_made", "Offer made", "open", 7),
        ("offer_accepted", "Offer accepted", "open", 14),
        ("agreement", "Agreement", "open", 30),
        ("completed", "Completed", "won", None),
        ("fell_through", "Fell through", "lost", None),
    ],
    "Listing": [
        ("valuation", "Valuation", "open", 7),
        ("listing_agreed", "Listing agreed", "open", 7),
        ("on_market", "On the market", "open", 60),
        ("under_offer", "Under offer", "open", 30),
        ("sold", "Sold", "won", None),
        ("withdrawn", "Withdrawn", "lost", None),
    ],
    "Rental": [
        ("viewing", "Viewing", "open", 7),
        ("application", "Application", "open", 7),
        ("referencing", "Referencing", "open", 7),
        ("let_agreed", "Let agreed", "won", None),
        ("fell_through", "Fell through", "lost", None),
    ],
}


@pytest.fixture
def catalogue():
    """The live manifest registry, put back after the test."""
    registry = get_registry()
    with mock.patch.dict(registry):
        yield registry


def _stages(pipeline):
    return [
        (s.code, s.label, s.order, s.kind, s.expected_days)
        for s in pipeline.stages.order_by("order")
    ]


def _expected(name):
    return [
        (code, label, order, kind, days)
        for order, (code, label, kind, days) in enumerate(EXPECTED[name], start=1)
    ]


def _entries(entries, type_):
    return {
        e["key"]: e for e in entries if e["type"] == type_ and e["target"] == "deal"
    }


def _preview(org, action):
    return {
        (i["type"], i["target"], i["label"]): i["effect"]
        for i in plan_app(org, APP, action)["items"]
    }


def _version_2():
    """real-estate as it shipped before deal pipelines."""
    pack = copy.deepcopy(get_pack(APP))
    pack["version"] = 2
    del pack["deal_pipelines"]
    return pack


def test_deal_pipelines_created_with_stages(org_a, admin_profile, admin_client):
    preview = _preview(org_a, "install")
    for name in EXPECTED:
        assert preview[("pipeline", "deal", name)] == "add"

    row = install_app(org_a, APP, admin_profile, sample_data=False)

    owned_pipelines = _entries(row.owned, "pipeline")
    owned_stages = _entries(row.owned, "stage")
    offered_stages = _entries(row.offered, "stage")
    for name in EXPECTED:
        pipeline = DealPipeline.objects.get(org=org_a, name=name)
        assert pipeline.is_default is False
        assert _stages(pipeline) == _expected(name)
        assert owned_pipelines[name] == {
            "type": "pipeline",
            "target": "deal",
            "key": name,
            "id": str(pipeline.id),
        }
        for stage in pipeline.stages.all():
            key = f"{name}/{stage.code}"
            assert stage.org_id == org_a.id
            assert owned_stages[key] == {
                "type": "stage",
                "target": "deal",
                "key": key,
                "id": str(stage.id),
            }
            assert offered_stages[key]["skipped_module"] is None
    assert len(owned_stages) == sum(len(s) for s in EXPECTED.values())

    res = admin_client.get(f"{DEALS}pipelines/")
    assert res.status_code == 200
    listed = {p["name"]: p for p in res.json()["pipelines"]}
    assert [s["code"] for s in listed["Rental"]["stages"]] == [
        code for code, *_rest in EXPECTED["Rental"]
    ]
    sale = DealPipeline.objects.get(org=org_a, name="Sale")
    res = admin_client.post(
        DEALS,
        {"name": "12 Elm Road", "pipeline": str(sale.id), "stage": "offer_made"},
        format="json",
    )
    assert res.status_code == 200, res.data
    deal = Opportunity.objects.get(org=org_a, name="12 Elm Road")
    assert (deal.pipeline_id, deal.stage) == (sale.id, "offer_made")


@pytest.mark.parametrize("theirs", ["Sale", "SALE"])
def test_existing_deal_pipeline_name_is_kept_untouched(org_a, admin_profile, theirs):
    mine = DealPipeline.objects.create(org=org_a, name=theirs)
    mine.seed_stages()
    before = _stages(mine)

    assert _preview(org_a, "install")[("pipeline", "deal", "Sale")] == "keep"
    row = install_app(org_a, APP, admin_profile, sample_data=False)

    mine.refresh_from_db()
    assert (mine.name, mine.is_default) == (theirs, False)
    assert _stages(mine) == before
    assert DealPipeline.objects.filter(org=org_a, name__iexact="sale").count() == 1
    assert "Sale" not in _entries(row.owned, "pipeline")
    assert not [k for k in _entries(row.owned, "stage") if k.startswith("Sale/")]
    assert _entries(row.offered, "pipeline")["Sale"]["skipped_module"] is None
    assert "Listing" in _entries(row.owned, "pipeline")
    assert has_update(org_a, get_pack(APP), row) is False

    remove_app(org_a, APP, admin_profile)

    assert _stages(DealPipeline.objects.get(pk=mine.pk)) == before


def test_deal_pipelines_skipped_while_deals_module_off_then_added_on_upgrade(
    org_a, admin_profile, admin_client
):
    set_modules_off(org_a, "deals")
    preview = _preview(org_a, "install")
    assert preview[("pipeline", "deal", "Rental")] == "skipped_module"

    row = install_app(org_a, APP, admin_profile, sample_data=False)

    assert not DealPipeline.objects.filter(org=org_a).exists()
    offered = {**_entries(row.offered, "pipeline"), **_entries(row.offered, "stage")}
    assert offered and all(e["skipped_module"] == "deals" for e in offered.values())
    assert not _entries(row.owned, "pipeline")
    app = next(
        a for a in admin_client.get("/api/apps/").json()["apps"] if a["id"] == APP
    )
    assert (app["skipped_modules"], app["update_available"]) == (["deals"], False)

    set_modules_off(org_a)
    assert has_update(org_a, get_pack(APP), row) is True
    preview = _preview(org_a, "upgrade")
    assert {k: v for k, v in preview.items() if k[0] == "pipeline"} == {
        ("pipeline", "deal", name): "add" for name in EXPECTED
    }

    row = upgrade_app(org_a, APP, admin_profile)

    for name in EXPECTED:
        pipeline = DealPipeline.objects.get(org=org_a, name=name)
        assert _stages(pipeline) == _expected(name)
        assert _entries(row.owned, "pipeline")[name]["id"] == str(pipeline.id)
    assert all(e["skipped_module"] is None for e in row.offered)
    assert has_update(org_a, get_pack(APP), row) is False


def test_deal_pipelines_never_become_default(org_a, admin_profile):
    install_app(org_a, APP, admin_profile, sample_data=False)

    assert not DealPipeline.objects.filter(org=org_a, is_default=True).exists()
    default = DealPipeline.default_for(org_a)
    assert default.name == "Sales"
    assert list(
        DealPipeline.objects.filter(org=org_a, is_default=True).values_list(
            "pk", flat=True
        )
    ) == [default.pk]
    deal = Opportunity.objects.create(org=org_a, name="No pipeline named")
    assert deal.pipeline_id == default.pk


def test_deal_pipelines_keep_an_existing_default(org_a, admin_profile):
    default = DealPipeline.default_for(org_a)

    install_app(org_a, APP, admin_profile, sample_data=False)

    assert list(
        DealPipeline.objects.filter(org=org_a, is_default=True).values_list(
            "pk", flat=True
        )
    ) == [default.pk]


def test_removal_keeps_deal_pipelines(org_a, admin_profile):
    install_app(org_a, APP, admin_profile, sample_data=False)
    stages = DealStage.objects.filter(org=org_a).count()

    preview = _preview(org_a, "remove")
    for name in EXPECTED:
        assert preview[("pipeline", "deal", name)] == "keep"
    row = remove_app(org_a, APP, admin_profile)

    assert set(
        DealPipeline.objects.filter(org=org_a).values_list("name", flat=True)
    ) == set(EXPECTED)
    assert DealStage.objects.filter(org=org_a).count() == stages
    assert set(_entries(row.owned, "pipeline")) == set(EXPECTED)

    row = install_app(org_a, APP, admin_profile, sample_data=False)

    assert DealPipeline.objects.filter(org=org_a).count() == len(EXPECTED)
    assert DealStage.objects.filter(org=org_a).count() == stages
    assert set(_entries(row.owned, "pipeline")) == set(EXPECTED)


def test_a_new_deal_stage_on_a_pipeline_the_org_has_is_named_not_added(
    org_a, admin_profile, catalogue
):
    install_app(org_a, APP, admin_profile, sample_data=False)
    pack = copy.deepcopy(get_pack(APP))
    pack["version"] += 1
    pack["deal_pipelines"][0]["stages"].append(
        {"code": "keys_handed_over", "label": "Keys handed over", "order": 7,
         "kind": "won"}
    )  # fmt: skip
    pack["deal_pipelines"].append(
        {
            "name": "Commercial lease",
            "stages": [
                {"code": "heads_of_terms", "label": "Heads of terms", "order": 1,
                 "kind": "open", "expected_days": 21},
                {"code": "signed", "label": "Signed", "order": 2, "kind": "won"},
                {"code": "lost", "label": "Lost", "order": 3, "kind": "lost"},
            ],
        }
    )  # fmt: skip
    catalogue[APP] = pack

    preview = _preview(org_a, "upgrade")

    assert preview == {
        ("pipeline", "deal", "Commercial lease"): "add",
        ("stage", "deal", "Keys handed over (Sale)"): "add_by_hand",
    }
    row = upgrade_app(org_a, APP, admin_profile)

    sale = DealPipeline.objects.get(org=org_a, name="Sale")
    assert not sale.stages.filter(code="keys_handed_over").exists()
    lease = DealPipeline.objects.get(org=org_a, name="Commercial lease")
    assert [s.code for s in lease.stages.order_by("order")] == [
        "heads_of_terms", "signed", "lost"
    ]  # fmt: skip
    assert "Sale/keys_handed_over" in _entries(row.offered, "stage")
    assert has_update(org_a, pack, row) is False


def test_real_estate_v2_org_is_offered_v3_with_three_deal_pipelines(
    org_a, admin_profile, admin_client, catalogue
):
    pack = get_pack(APP)
    assert pack["version"] == 3
    assert pack["description"] == (
        "Agencies and brokers selling and letting residential and commercial property."
    )
    catalogue[APP] = _version_2()
    install_app(org_a, APP, admin_profile, sample_data=False)
    catalogue[APP] = pack

    app = next(
        a for a in admin_client.get("/api/apps/").json()["apps"] if a["id"] == APP
    )
    assert (app["version"], app["installed_version"]) == (3, 2)
    assert app["update_available"] is True
    assert _preview(org_a, "upgrade") == {
        ("pipeline", "deal", name): "add" for name in EXPECTED
    }

    res = admin_client.post(f"/api/apps/{APP}/upgrade/", {}, format="json")

    assert res.status_code == 200
    assert res.json()["app"]["installed_version"] == 3
    assert res.json()["app"]["update_available"] is False
    for name in EXPECTED:
        assert _stages(DealPipeline.objects.get(org=org_a, name=name)) == _expected(
            name
        )
    row = InstalledApp.objects.get(org=org_a, app_id=APP)
    assert set(_entries(row.owned, "pipeline")) == set(EXPECTED)


# The manifest schema


def _manifest(*pipelines):
    return {
        "id": "demo",
        "version": 1,
        "name": "Demo",
        "deal_pipelines": list(pipelines),
    }


def _pipeline(name="Sale", **changes):
    stages = [
        {"code": "viewing", "label": "Viewing", "order": 1, "kind": "open",
         "expected_days": 7},
        {"code": "completed", "label": "Completed", "order": 2, "kind": "won"},
        {"code": "fell_through", "label": "Fell through", "order": 3, "kind": "lost"},
    ]  # fmt: skip
    return {"name": name, "stages": stages, **changes}


def _stage(pipeline, index, **changes):
    pipeline["stages"][index] = {**pipeline["stages"][index], **changes}
    return pipeline


def _drop(pipeline, index, key):
    del pipeline["stages"][index][key]
    return pipeline


def test_a_valid_deal_pipeline_validates():
    assert validate_manifest(_manifest(_pipeline(), _pipeline("Rental")))


_REJECTED = [
    ("duplicate code", _stage(_pipeline(), 1, code="viewing"), "code 'viewing' is listed twice"),
    ("no won stage", _stage(_pipeline(), 1, kind="open"), "no won stage"),
    ("no lost stage", _stage(_pipeline(), 2, kind="open"), "no lost stage"),
    ("bad kind", _stage(_pipeline(), 0, kind="closed"), "kind 'closed'"),
    ("is_default", _pipeline(is_default=True), "is_default"),
    ("unknown pipeline key", _pipeline(colour="red"), "unknown key"),
    ("unknown stage key", _stage(_pipeline(), 0, color="#fff"), "unknown key"),
    ("uppercase code", _stage(_pipeline(), 0, code="Viewing"), "code"),
    ("code starts with a digit", _stage(_pipeline(), 0, code="1st"), "code"),
    ("code too long", _stage(_pipeline(), 0, code="v" * 65), "code"),
    ("blank label", _stage(_pipeline(), 0, label=""), "label"),
    ("label too long", _stage(_pipeline(), 0, label="x" * 101), "label"),
    ("label with outer space", _stage(_pipeline(), 0, label=" Viewing"), "label"),
    ("duplicate label", _stage(_pipeline(), 1, label="viewing"), "label 'viewing' is listed twice"),
    ("duplicate order", _stage(_pipeline(), 1, order=1), "order 1 is listed twice"),
    ("order zero", _stage(_pipeline(), 0, order=0), "order"),
    ("order not a number", _stage(_pipeline(), 0, order=True), "order"),
    ("no order", _drop(_pipeline(), 0, "order"), "order"),
    ("expected days zero", _stage(_pipeline(), 0, expected_days=0), "expected_days"),
    ("expected days too many", _stage(_pipeline(), 0, expected_days=3651), "expected_days"),
    ("expected days text", _stage(_pipeline(), 0, expected_days="7"), "expected_days"),
    ("expected days on won", _stage(_pipeline(), 1, expected_days=7), "expected_days"),
    ("blank name", _pipeline(name=""), "name"),
    ("name too long", _pipeline(name="x" * 101), "name"),
    ("name with outer space", _pipeline(name="Sale "), "name"),
    ("default pipeline name", _pipeline(name="Sales"), "default deal pipeline"),
    ("no stages", _pipeline(stages=[]), "stages"),
]  # fmt: skip


@pytest.mark.parametrize(
    ("manifest", "message"),
    [pytest.param(_manifest(p), m, id=i) for i, p, m in _REJECTED]
    + [
        pytest.param(
            _manifest(_pipeline(), _pipeline("sale")),
            "name 'sale' is listed twice",
            id="duplicate name",
        ),
        pytest.param(
            {**_manifest(), "deal_pipelines": {"name": "Sale"}},
            "expected a list",
            id="not a list",
        ),
    ],
)
def test_deal_pipelines_schema_rejects(manifest, message):
    with pytest.raises(PackValidationError, match="manifest.deal_pipelines") as exc:
        validate_manifest(manifest)
    assert message in str(exc.value)
