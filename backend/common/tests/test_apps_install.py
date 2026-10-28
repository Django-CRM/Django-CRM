"""Installing, updating and removing an industry app (``common.apps_catalogue``).

What each action creates, keeps, owns and switches off, and that none of them
creates, changes or deletes a customer record beyond the sample rows an install
is asked for.
"""

import copy
from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import Account
from cases.models import Case, CasePipeline, CaseStage
from common import apps_catalogue
from common.apps_catalogue import (
    AppConflict,
    AppNotFound,
    install_app,
    remove_app,
    upgrade_app,
)
from common.audit_log import SecurityAuditLog
from common.models import (
    CustomFieldDefinition,
    InstalledApp,
    Org,
    PackApplication,
    Profile,
    Tags,
    User,
)
from common.packs.loader import get_pack, get_registry
from common.packs.planner import has_update, plan_app
from common.testing import set_modules_off
from contacts.models import Contact
from invoices.models import Product
from leads.models import Lead, LeadPipeline, LeadSource, LeadStage
from opportunity.models import Opportunity
from tasks.models import Task, TaskPipeline

APP = "real-estate"
RECORD_MODELS = (Lead, Contact, Account, Opportunity, Case, Task)


def _records(org):
    """Every customer record of ``org`` as stored, with its tag ids."""
    out = {}
    for model in RECORD_MODELS:
        rows = list(model.objects.filter(org=org).order_by("pk").values())
        for row in rows:
            row["tag_ids"] = sorted(
                str(t)
                for t in model.objects.get(pk=row["id"]).tags.values_list(
                    "pk", flat=True
                )
            )
        out[model.__name__] = rows
    return out


def _real_records(org):
    """The same, for records that are not sample rows."""
    return {
        name: [r for r in rows if not r["is_sample"]]
        for name, rows in _records(org).items()
    }


@pytest.fixture
def busy_org(org_a, admin_profile):
    """An org with real records in every module, one of them using a field
    key the app also defines."""
    account = Account.objects.create(org=org_a, name="Northwind Traders")
    Contact.objects.create(
        org=org_a, first_name="Ada", last_name="Lovelace", account=account
    )
    lead = Lead.objects.create(
        org=org_a, title="A real enquiry", first_name="Bo", custom_fields={"x": 1}
    )
    lead.tags.add(Tags.objects.create(org=org_a, name="Existing"))
    Opportunity.objects.create(org=org_a, name="A real deal", account=account)
    Case.objects.create(org=org_a, name="A real ticket", status="New", priority="Low")
    Task.objects.create(org=org_a, title="A real task", status="New", priority="Low")
    return org_a


@pytest.fixture
def catalogue():
    """The live manifest registry, put back after the test."""
    registry = get_registry()
    with mock.patch.dict(registry):
        yield registry


def _newer(**changes):
    """real-estate one version on, adding a tag, a field, a product and a
    stage to its existing lead pipeline, and relabelling an existing field."""
    pack = copy.deepcopy(get_pack(APP))
    pack["version"] += 1
    pack["tags"].append({"name": "Waterfront", "color": "sky"})
    pack["custom_fields"].append(
        {
            "target": "Lead",
            "key": "plot_size",
            "label": "Plot size",
            "field_type": "number",
        }
    )
    pack["custom_fields"][0]["label"] = "Maximum budget"
    pack["products"].append({"name": "Rental search", "sku": "RE-RENT", "price": 300})
    pack["lead_pipeline"]["stages"].append(
        {"name": "Keys handed over", "order": 8, "stage_type": "won"}
    )
    pack.update(changes)
    return pack


def _row(org):
    return InstalledApp.objects.get(org=org, app_id=APP)


def _owned(org, type_):
    return {e["key"] for e in _row(org).owned if e["type"] == type_}


# Install


def test_install_on_an_empty_org_creates_and_owns_every_part(org_a, admin_profile):
    pack = get_pack(APP)

    row = install_app(org_a, APP, admin_profile, sample_data=True)

    assert (row.status, row.installed_version) == ("installed", pack["version"])
    assert row.installed_by == admin_profile and row.installed_at is not None
    assert LeadPipeline.objects.filter(org=org_a, name="Sales").count() == 1
    assert CasePipeline.objects.filter(org=org_a).count() == 1
    assert TaskPipeline.objects.filter(org=org_a).count() == 1
    fields = CustomFieldDefinition.objects.filter(org=org_a, target_model="Lead")
    assert {f.key for f in fields} == {f["key"] for f in pack["custom_fields"]}
    assert Product.objects.filter(org=org_a).count() == len(pack["products"])
    assert LeadSource.objects.filter(org=org_a, key="property portal").exists()
    org_a.refresh_from_db()
    assert org_a.terminology == pack["terminology"]
    assert org_a.vertical == APP
    assert Lead.objects.filter(org=org_a, is_sample=True).exists()

    assert _owned(org_a, "pipeline") == {
        "Sales", "Service requests", "Agent workflow", "Sale", "Listing", "Rental"
    }  # fmt: skip
    assert _owned(org_a, "field") == {f["key"] for f in pack["custom_fields"]}
    assert _owned(org_a, "wording") == set(pack["terminology"])
    assert _owned(org_a, "product") == {p["sku"] for p in pack["products"]}
    assert _owned(org_a, "source") == {"property portal"}
    assert _owned(org_a, "tag") == {Tags.slug_for(t["name"]) for t in pack["tags"]}
    assert "Sales/Site visit" in _owned(org_a, "stage")
    field = CustomFieldDefinition.objects.get(org=org_a, key="budget_max")
    entry = next(e for e in row.owned if e["key"] == "budget_max")
    assert entry == {
        "type": "field",
        "target": "lead",
        "key": "budget_max",
        "id": str(field.id),
    }
    assert {(e["type"], e["key"]) for e in row.offered} >= {
        ("pipeline", "Sales"),
        ("stage", "Sales/Booked"),
        ("wording", "lead.plural"),
        ("product", "RE-VALUATION"),
    }
    assert all(e["skipped_module"] is None for e in row.offered)
    assert PackApplication.objects.filter(org=org_a, pack_id=APP).count() == 1
    audit = SecurityAuditLog.objects.get(event_type="APP_INSTALLED")
    assert (audit.org, audit.user) == (org_a, admin_profile.user)
    assert audit.metadata == {"app_id": APP, "version": pack["version"]}


def test_install_without_sample_data_creates_no_record(org_a, admin_profile):
    install_app(org_a, APP, admin_profile, sample_data=False)

    for model in RECORD_MODELS:
        assert not model.objects.filter(org=org_a).exists()
    assert not Tags.objects.filter(org=org_a, slug="sample-data").exists()


def test_install_on_a_busy_org_changes_no_record(busy_org, admin_profile):
    before = _records(busy_org)

    install_app(busy_org, APP, admin_profile, sample_data=False)

    assert _records(busy_org) == before


def test_sample_data_on_a_busy_org_only_adds_sample_rows(busy_org, admin_profile):
    before = _records(busy_org)

    install_app(busy_org, APP, admin_profile, sample_data=True)

    assert Lead.objects.filter(org=busy_org, is_sample=True).exists()
    assert _real_records(busy_org) == before


def test_install_keeps_what_the_org_has_and_does_not_own_it(org_a, admin_profile):
    """Review focus 2."""
    org_a.terminology = {"lead.plural": "Prospects"}
    org_a.save(update_fields=["terminology"])
    mine = LeadPipeline.objects.create(org=org_a, name="Sales", description="Ours")
    LeadStage.objects.create(org=org_a, pipeline=mine, name="Ours", order=1)
    tag = Tags.objects.create(org=org_a, name="Hot", color="gray")
    field = CustomFieldDefinition.objects.create(
        org=org_a, target_model="Lead", key="budget_max", label="Our budget",
        field_type="text",
    )  # fmt: skip
    preview = plan_app(org_a, APP, "install")["items"]
    effects = {(i["type"], i["label"]): i["effect"] for i in preview}
    assert effects[("wording", "Leads called Prospects")] == "keep"
    assert effects[("wording", "Lead called Enquiry")] == "add"
    assert effects[("pipeline", "Sales")] == "keep"
    assert effects[("field", "Budget (max)")] == "keep"

    row = install_app(org_a, APP, admin_profile, sample_data=False)

    assert LeadPipeline.objects.filter(org=org_a, name="Sales").count() == 1
    assert list(mine.stages.values_list("name", flat=True)) == ["Ours"]
    assert Tags.objects.filter(org=org_a, slug="hot").count() == 1
    tag.refresh_from_db()
    field.refresh_from_db()
    assert (tag.name, tag.color) == ("Hot", "gray")
    assert (field.label, field.field_type) == ("Our budget", "text")
    org_a.refresh_from_db()
    assert org_a.terminology["lead.plural"] == "Prospects"
    owned = {(e["type"], e["key"]) for e in row.owned}
    assert not owned & {
        ("pipeline", "Sales"),
        ("tag", "hot"),
        ("field", "budget_max"),
        ("wording", "lead.plural"),
    }
    assert ("wording", "lead.singular") in owned
    assert ("pipeline", "Service requests") in owned

    remove_app(org_a, APP, admin_profile)

    field.refresh_from_db()
    assert field.is_active
    assert LeadPipeline.objects.filter(pk=mine.pk).exists()
    assert Tags.objects.filter(pk=tag.pk).exists()
    org_a.refresh_from_db()
    assert org_a.terminology == {"lead.plural": "Prospects"}


def test_install_refuses_an_installed_app(org_a, admin_profile):
    install_app(org_a, APP, admin_profile, sample_data=False)

    with pytest.raises(AppConflict):
        install_app(org_a, APP, admin_profile, sample_data=False)
    assert PackApplication.objects.filter(org=org_a).count() == 1


def test_unknown_app_is_not_found(org_a, admin_profile):
    for call in (
        lambda: install_app(org_a, "nope", admin_profile, sample_data=False),
        lambda: upgrade_app(org_a, "nope", admin_profile),
        lambda: remove_app(org_a, "nope", admin_profile),
        lambda: plan_app(org_a, "nope", "install"),
    ):
        with pytest.raises(AppNotFound):
            call()


def test_an_actor_from_another_org_is_refused(org_a, org_b, profile_b):
    with pytest.raises(ValueError):
        install_app(org_a, APP, profile_b, sample_data=False)
    assert not InstalledApp.objects.filter(org=org_a).exists()


# Modules


def test_an_off_module_skips_its_parts_and_a_later_update_fills_them(
    org_a, admin_profile
):
    set_modules_off(org_a, "tickets", "deals", "invoices")

    row = install_app(org_a, APP, admin_profile, sample_data=False)

    assert not CasePipeline.objects.filter(org=org_a).exists()
    assert not Product.objects.filter(org=org_a).exists()
    skipped = {(e["type"], e["key"]): e["skipped_module"] for e in row.offered}
    assert skipped[("pipeline", "Service requests")] == "tickets"
    assert skipped[("stage", "Service requests/With agent")] == "tickets"
    assert skipped[("product", "RE-VALUATION")] == "invoices"
    assert skipped[("pipeline", "Sales")] is None
    with pytest.raises(AppConflict):
        upgrade_app(org_a, APP, admin_profile)

    set_modules_off(org_a, "deals")
    preview = plan_app(org_a, APP, "upgrade")
    assert {(i["type"], i["label"], i["effect"]) for i in preview["items"]} >= {
        ("pipeline", "Service requests", "add"),
        ("product", "Property valuation", "add"),
    }
    row = upgrade_app(org_a, APP, admin_profile)

    pipeline = CasePipeline.objects.get(org=org_a, name="Service requests")
    assert CaseStage.objects.filter(pipeline=pipeline).count() == 5
    assert Product.objects.filter(org=org_a).count() == len(get_pack(APP)["products"])
    for e in row.offered:
        deal_pipeline = e["type"] in ("pipeline", "stage") and e["target"] == "deal"
        assert e["skipped_module"] == ("deals" if deal_pipeline else None)
    assert ("pipeline", "Service requests") in {
        (e["type"], e["key"]) for e in row.owned
    }
    assert row.installed_version == get_pack(APP)["version"]


# Upgrade


def test_upgrade_adds_only_what_was_never_offered(org_a, admin_profile, catalogue):
    install_app(org_a, APP, admin_profile, sample_data=False)
    Tags.objects.get(org=org_a, slug="hot").delete()
    LeadPipeline.objects.get(org=org_a, name="Sales").stages.filter(
        name="Site visit"
    ).delete()
    catalogue[APP] = _newer()

    preview = plan_app(org_a, APP, "upgrade")
    effects = {(i["type"], i["label"]): i["effect"] for i in preview["items"]}
    assert effects == {
        ("tag", "Waterfront"): "add",
        ("field", "Plot size"): "add",
        ("product", "Rental search"): "add",
        ("stage", "Keys handed over (Sales)"): "add_by_hand",
    }
    assert preview["sample_data"] == {
        "offered": False,
        "default_on": False,
        "records": 0,
    }

    row = upgrade_app(org_a, APP, admin_profile)

    newer = catalogue[APP]["version"]
    assert row.installed_version == newer
    assert Tags.objects.filter(org=org_a, slug="waterfront").exists()
    assert not Tags.objects.filter(org=org_a, slug="hot").exists()
    sales = LeadPipeline.objects.get(org=org_a, name="Sales")
    assert not sales.stages.filter(name__in=["Keys handed over", "Site visit"]).exists()
    assert CustomFieldDefinition.objects.get(org=org_a, key="budget_max").label == (
        "Budget (max)"
    )
    assert Product.objects.filter(org=org_a, sku="RE-RENT").exists()
    assert "waterfront" in _owned(org_a, "tag")
    assert ("stage", "Sales/Keys handed over") in {
        (e["type"], e["key"]) for e in row.offered
    }
    assert not Lead.objects.filter(org=org_a).exists()
    assert SecurityAuditLog.objects.filter(event_type="APP_UPGRADED").count() == 1
    assert PackApplication.objects.filter(org=org_a, pack_version=newer).count() == 1

    with pytest.raises(AppConflict):
        upgrade_app(org_a, APP, admin_profile)


def test_upgrade_never_brings_back_a_field_the_org_deleted(
    org_a, admin_profile, catalogue
):
    """Review focus 4, for the soft delete fields have and a hard delete."""
    install_app(org_a, APP, admin_profile, sample_data=False)
    field = CustomFieldDefinition.objects.get(org=org_a, key="possession_by")
    field.is_active = False
    field.save(update_fields=["is_active"])
    CustomFieldDefinition.objects.filter(org=org_a, key="configuration").delete()
    catalogue[APP] = _newer()

    upgrade_app(org_a, APP, admin_profile)

    field.refresh_from_db()
    assert not field.is_active
    assert not CustomFieldDefinition.objects.filter(
        org=org_a, key="configuration"
    ).exists()


def test_upgrade_with_nothing_new_and_on_an_app_not_installed_is_refused(
    org_a, admin_profile
):
    with pytest.raises(AppConflict):
        upgrade_app(org_a, APP, admin_profile)
    install_app(org_a, APP, admin_profile, sample_data=False)
    with pytest.raises(AppConflict):
        upgrade_app(org_a, APP, admin_profile)
    remove_app(org_a, APP, admin_profile)
    with pytest.raises(AppConflict):
        upgrade_app(org_a, APP, admin_profile)


def test_upgrade_changes_no_record(busy_org, admin_profile, catalogue):
    install_app(busy_org, APP, admin_profile, sample_data=True)
    before = _records(busy_org)
    catalogue[APP] = _newer()

    upgrade_app(busy_org, APP, admin_profile)

    assert _records(busy_org) == before


# Removal


def test_remove_turns_off_what_the_app_owns_and_keeps_every_record(
    busy_org, admin_profile
):
    install_app(busy_org, APP, admin_profile, sample_data=True)
    lead = Lead.objects.filter(org=busy_org, is_sample=True).first()
    lead.custom_fields = {"budget_max": 100}
    lead.save(update_fields=["custom_fields"])
    before = _records(busy_org)
    pipelines = LeadPipeline.objects.filter(org=busy_org).count()
    tags = Tags.objects.filter(org=busy_org).count()

    row = remove_app(busy_org, APP, admin_profile)

    assert (row.status, row.removed_at is not None) == ("removed", True)
    assert not CustomFieldDefinition.objects.filter(
        org=busy_org, target_model="Lead", is_active=True
    ).exists()
    assert not Product.objects.filter(org=busy_org, is_active=True).exists()
    assert not LeadSource.objects.get(org=busy_org, key="property portal").is_active
    assert LeadSource.objects.get(org=busy_org, key="call").is_active
    busy_org.refresh_from_db()
    assert busy_org.terminology == {}
    assert LeadPipeline.objects.filter(org=busy_org).count() == pipelines
    assert Tags.objects.filter(org=busy_org).count() == tags
    assert _records(busy_org) == before
    assert SecurityAuditLog.objects.filter(event_type="APP_REMOVED").count() == 1
    assert PackApplication.objects.filter(org=busy_org).count() == 1


def test_remove_leaves_wording_the_org_no_longer_holds(org_a, admin_profile):
    install_app(org_a, APP, admin_profile, sample_data=False)
    org_a.terminology = {**org_a.terminology, "lead.plural": "Buyers"}
    org_a.save(update_fields=["terminology"])

    remove_app(org_a, APP, admin_profile)

    org_a.refresh_from_db()
    assert org_a.terminology == {"lead.plural": "Buyers"}


def test_remove_refuses_an_app_that_is_not_installed(org_a, admin_profile):
    with pytest.raises(AppConflict):
        remove_app(org_a, APP, admin_profile)
    install_app(org_a, APP, admin_profile, sample_data=False)
    remove_app(org_a, APP, admin_profile)
    with pytest.raises(AppConflict):
        remove_app(org_a, APP, admin_profile)


def test_remove_then_install_turns_owned_parts_back_on(busy_org, admin_profile):
    """Review focus 3."""
    install_app(busy_org, APP, admin_profile, sample_data=True)
    deleted = CustomFieldDefinition.objects.get(org=busy_org, key="possession_by")
    deleted.is_active = False
    deleted.save(update_fields=["is_active"])
    Tags.objects.get(org=busy_org, slug="investor").delete()
    remove_app(busy_org, APP, admin_profile)
    before = _records(busy_org)
    counts = {
        model: model.objects.filter(org=busy_org).count()
        for model in (LeadPipeline, LeadStage, CasePipeline, Tags, Product, LeadSource)
    }
    fields = CustomFieldDefinition.objects.filter(org=busy_org).count()

    preview = plan_app(busy_org, APP, "install")
    effects = {(i["type"], i["label"]): i["effect"] for i in preview["items"]}
    assert effects[("field", "Budget (max)")] == "turn_on"
    assert effects[("product", "Property valuation")] == "turn_on"
    assert effects[("source", "Property portal")] == "turn_on"
    assert effects[("wording", "Leads called Enquiries")] == "add"
    assert ("field", "Possession by") not in effects
    assert preview["sample_data"]["offered"] is False

    row = install_app(busy_org, APP, admin_profile, sample_data=True)

    assert row.status == "installed" and row.removed_at is None
    assert {
        model: model.objects.filter(org=busy_org).count() for model in counts
    } == counts
    assert CustomFieldDefinition.objects.filter(org=busy_org).count() == fields
    active = set(
        CustomFieldDefinition.objects.filter(
            org=busy_org, is_active=True, target_model="Lead"
        ).values_list("key", flat=True)
    )
    assert active == {"budget_max", "configuration", "preferred_location",
                      "finance_required"}  # fmt: skip
    assert not Product.objects.filter(org=busy_org, is_active=False).exists()
    assert LeadSource.objects.get(org=busy_org, key="property portal").is_active
    assert not Tags.objects.filter(org=busy_org, slug="investor").exists()
    busy_org.refresh_from_db()
    assert busy_org.terminology == get_pack(APP)["terminology"]
    assert _records(busy_org) == before
    assert not any(e.get("off") for e in row.owned)
    assert _owned(busy_org, "wording") == set(get_pack(APP)["terminology"])

    remove_app(busy_org, APP, admin_profile)
    assert _records(busy_org) == before


def test_two_apps_share_wording_and_removing_the_first_hands_it_over(
    org_a, admin_profile
):
    install_app(org_a, APP, admin_profile, sample_data=False)
    install_app(org_a, "events-weddings", admin_profile, sample_data=False)
    org_a.refresh_from_db()
    assert org_a.terminology["lead.plural"] == "Enquiries"
    assert org_a.terminology["opportunity.plural"] == "Deals"

    remove_app(org_a, APP, admin_profile)

    org_a.refresh_from_db()
    assert org_a.terminology["lead.plural"] == "Enquiries"
    assert "opportunity.plural" not in org_a.terminology
    other = InstalledApp.objects.get(org=org_a, app_id="events-weddings")
    assert ("wording", "lead.plural") in {(e["type"], e["key"]) for e in other.owned}

    remove_app(org_a, "events-weddings", admin_profile)

    org_a.refresh_from_db()
    assert org_a.terminology == {}


# Preview


def _table_counts():
    from django.apps import apps

    return {
        model._meta.label: model.objects.count()
        for model in apps.get_models()
        if model._meta.managed and not model._meta.proxy
    }


@pytest.mark.parametrize("state", ["new", "installed", "removed"])
def test_preview_writes_nothing(busy_org, admin_profile, catalogue, state):
    if state != "new":
        install_app(busy_org, APP, admin_profile, sample_data=False)
    if state == "removed":
        remove_app(busy_org, APP, admin_profile)
    catalogue[APP] = _newer()
    busy_org.refresh_from_db()
    before = (_table_counts(), _records(busy_org), dict(busy_org.terminology))
    actions = {
        "new": ["install"],
        "installed": ["upgrade", "remove"],
        "removed": ["install"],
    }[state]

    with CaptureQueriesContext(connection) as queries:
        for action in actions:
            plan_app(busy_org, APP, action)

    busy_org.refresh_from_db()
    assert (_table_counts(), _records(busy_org), dict(busy_org.terminology)) == before
    writes = [
        q["sql"]
        for q in queries.captured_queries
        if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    assert writes == []


def test_install_preview_lists_adds_and_keeps(org_a, admin_profile):
    Tags.objects.create(org=org_a, name="Hot")

    preview = plan_app(org_a, APP, "install")

    assert preview["action"] == "install"
    items = {
        (i["type"], i["target"], i["label"]): i["effect"] for i in preview["items"]
    }
    assert items[("wording", "deal", "Deals called Deals")] == "add"
    assert items[("wording", "lead", "Leads called Enquiries")] == "add"
    assert items[("pipeline", "lead", "Sales")] == "add"
    assert items[("pipeline", "ticket", "Service requests")] == "add"
    assert items[("field", "lead", "Budget (max)")] == "add"
    assert items[("tag", None, "Hot")] == "keep"
    assert items[("tag", None, "Investor")] == "add"
    assert items[("source", None, "Property portal")] == "add"
    assert items[("product", None, "Property valuation")] == "add"
    assert not [i for i in preview["items"] if i["type"] == "stage"]
    sample = get_pack(APP)["sample_data"]
    assert preview["sample_data"] == {
        "offered": True,
        "default_on": True,
        "records": sum(len(rows) for rows in sample.values()),
    }


@pytest.mark.parametrize(
    "model",
    [
        lambda org: Lead.objects.create(org=org, title="Real"),
        lambda org: Contact.objects.create(org=org, first_name="A", last_name="B"),
        lambda org: Account.objects.create(org=org, name="Real Co"),
    ],
)
def test_sample_data_is_on_by_default_only_without_real_records(
    org_a, admin_profile, model
):
    Opportunity.objects.create(org=org_a, name="A deal does not count")
    assert plan_app(org_a, APP, "install")["sample_data"]["default_on"] is True

    model(org_a)

    sample = plan_app(org_a, APP, "install")["sample_data"]
    assert (sample["offered"], sample["default_on"]) == (True, False)


def test_sample_data_is_not_offered_once_the_org_has_sample_rows(org_a, admin_profile):
    install_app(org_a, "events-weddings", admin_profile, sample_data=True)

    sample = plan_app(org_a, APP, "install")["sample_data"]

    assert sample == {"offered": False, "default_on": False, "records": 0}


def test_sample_data_counts_leave_out_off_modules(org_a, admin_profile):
    set_modules_off(org_a, "leads")
    sample = get_pack(APP)["sample_data"]

    records = plan_app(org_a, APP, "install")["sample_data"]["records"]

    lead_tasks = sum(1 for t in sample["tasks"] if t.get("lead"))
    assert (
        records
        == sum(len(rows) for rows in sample.values())
        - len(sample["leads"])
        - lead_tasks
    )


def test_remove_preview_names_what_it_turns_off_and_keeps(org_a, admin_profile):
    install_app(org_a, APP, admin_profile, sample_data=False)
    CustomFieldDefinition.objects.filter(org=org_a, key="possession_by").update(
        is_active=False
    )

    preview = plan_app(org_a, APP, "remove")

    items = {(i["type"], i["label"]): i["effect"] for i in preview["items"]}
    assert items[("field", "Budget (max)")] == "turn_off"
    assert ("field", "Possession by") not in items
    assert items[("source", "Property portal")] == "turn_off"
    assert items[("product", "Property valuation")] == "turn_off"
    assert items[("wording", "Leads called Enquiries")] == "restore_wording"
    assert items[("pipeline", "Sales")] == "keep"
    assert items[("tag", "Hot")] == "keep"


def test_preview_refuses_what_the_action_would_refuse(org_a, admin_profile):
    for action in ("upgrade", "remove"):
        with pytest.raises(AppConflict):
            plan_app(org_a, APP, action)
    install_app(org_a, APP, admin_profile, sample_data=False)
    for action in ("install", "upgrade"):
        with pytest.raises(AppConflict):
            plan_app(org_a, APP, action)


def test_an_install_by_a_profile_whose_user_is_gone_keeps_the_row(org_a):
    user = User.objects.create_user(email="gone@test.com", password="x")
    profile = Profile.objects.create(user=user, org=org_a, role="ADMIN")
    install_app(org_a, APP, profile, sample_data=False)

    profile.delete()

    assert InstalledApp.objects.get(org=org_a, app_id=APP).installed_by is None


# Review fixes


def test_remove_keeps_a_word_renamed_after_it_planned(org_a, admin_profile):
    install_app(org_a, APP, admin_profile, sample_data=False)
    real = apps_catalogue.build_plan

    def plan_then_rename(org, *args, **kwargs):
        plan = real(org, *args, **kwargs)
        Org.objects.filter(pk=org.pk).update(
            terminology={**org.terminology, "lead.plural": "Buyers"}
        )
        return plan

    with mock.patch("common.apps_catalogue.build_plan", side_effect=plan_then_rename):
        row = remove_app(org_a, APP, admin_profile)

    org_a.refresh_from_db()
    assert org_a.terminology == {"lead.plural": "Buyers"}
    entries = {e["key"]: e for e in row.owned if e["type"] == "wording"}
    assert "off" not in entries["lead.plural"]
    assert entries["lead.singular"]["off"] is True


def test_install_reads_modules_from_the_org_row_not_the_callers_copy(
    org_a, admin_profile
):
    stale = Org.objects.get(pk=org_a.pk)
    set_modules_off(org_a, "tickets")

    row = install_app(stale, APP, admin_profile, sample_data=False)

    assert not CasePipeline.objects.filter(org=org_a).exists()
    skipped = {(e["type"], e["key"]): e["skipped_module"] for e in row.offered}
    assert skipped[("pipeline", "Service requests")] == "tickets"
    assert stale.modules_off == ["tickets"]


@pytest.mark.postgres_only
def test_each_action_locks_the_org_row_before_it_writes(org_a, admin_profile):
    if connection.vendor != "postgresql":
        pytest.skip("SELECT ... FOR UPDATE is a PostgreSQL statement here")
    actions = (
        lambda: install_app(org_a, APP, admin_profile, sample_data=False),
        lambda: remove_app(org_a, APP, admin_profile),
        lambda: install_app(org_a, APP, admin_profile, sample_data=False),
    )
    for action in actions:
        with CaptureQueriesContext(connection) as queries:
            action()
        sqls = [q["sql"] for q in queries.captured_queries]
        locked = next(
            i
            for i, sql in enumerate(sqls)
            if 'FROM "organization"' in sql and "FOR UPDATE" in sql
        )
        first_write = next(
            i
            for i, sql in enumerate(sqls)
            if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        )
        assert locked < first_write


def test_installing_again_at_a_newer_version_names_new_stages(
    org_a, admin_profile, catalogue
):
    install_app(org_a, APP, admin_profile, sample_data=False)
    remove_app(org_a, APP, admin_profile)
    catalogue[APP] = _newer()

    preview = plan_app(org_a, APP, "install")

    effects = {(i["type"], i["label"]): i["effect"] for i in preview["items"]}
    assert effects[("stage", "Keys handed over (Sales)")] == "add_by_hand"
    assert effects[("tag", "Waterfront")] == "add"

    row = install_app(org_a, APP, admin_profile, sample_data=False)

    assert row.installed_version == catalogue[APP]["version"]
    assert ("stage", "Sales/Keys handed over") in {
        (e["type"], e["key"]) for e in row.offered
    }
    sales = LeadPipeline.objects.get(org=org_a, name="Sales")
    assert not sales.stages.filter(name="Keys handed over").exists()
    assert has_update(org_a, catalogue[APP], row) is False


def test_a_module_back_on_whose_part_the_org_has_is_no_update(org_a, admin_profile):
    set_modules_off(org_a, "tickets")
    install_app(org_a, APP, admin_profile, sample_data=False)
    CasePipeline.objects.create(org=org_a, name="Service requests")
    set_modules_off(org_a)
    row = InstalledApp.objects.get(org=org_a, app_id=APP)

    assert has_update(org_a, get_pack(APP), row) is False
    with pytest.raises(AppConflict):
        upgrade_app(org_a, APP, admin_profile)

    CasePipeline.objects.filter(org=org_a).delete()
    assert has_update(org_a, get_pack(APP), row) is True


def test_a_new_stage_the_org_already_added_is_no_update(
    org_a, admin_profile, catalogue
):
    install_app(org_a, APP, admin_profile, sample_data=False)
    pack = copy.deepcopy(get_pack(APP))
    shipped = pack["version"]
    pack["version"] += 1
    pack["lead_pipeline"]["stages"].append(
        {"name": "Keys handed over", "order": 8, "stage_type": "won"}
    )
    catalogue[APP] = pack
    sales = LeadPipeline.objects.get(org=org_a, name="Sales")
    LeadStage.objects.create(org=org_a, pipeline=sales, name="Keys handed over")
    row = InstalledApp.objects.get(org=org_a, app_id=APP)

    assert has_update(org_a, pack, row) is False
    with pytest.raises(AppConflict):
        upgrade_app(org_a, APP, admin_profile)
    assert InstalledApp.objects.get(pk=row.pk).installed_version == shipped


def test_removal_logs_the_version_it_removes(org_a, admin_profile, catalogue):
    shipped = get_pack(APP)["version"]
    install_app(org_a, APP, admin_profile, sample_data=False)
    catalogue[APP] = _newer()

    remove_app(org_a, APP, admin_profile)

    audit = SecurityAuditLog.objects.get(event_type="APP_REMOVED")
    assert audit.metadata == {"app_id": APP, "version": shipped}
