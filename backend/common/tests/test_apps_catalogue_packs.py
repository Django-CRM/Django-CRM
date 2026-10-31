"""The catalogue's entry points that take a manifest and an extension.

A caller that holds its own pack, one the shipped registry does not know, hands
in the pack and the extension instead of an app id.
"""

import json

import pytest

from common.apps_catalogue import (
    AppConflict,
    install_app,
    install_pack,
    remove_app,
    remove_pack,
    upgrade_pack,
)
from common.models import InstalledApp, Tags
from common.packs import loader
from common.packs.planner import (
    plan_pack,
    register_pack_source,
    unregister_pack_source,
)
from common.packs.schema import PackValidationError

PACK = {
    "id": "x-test-app",
    "version": 1,
    "name": "Test app",
    "tags": [{"name": "Solar", "color": "red"}],
    "custom_fields": [
        {
            "target": "Lead",
            "key": "roof_area",
            "label": "Roof area",
            "field_type": "number",
        }
    ],
}
OBJECT = {"type": "object", "target": "", "label": "Panel", "effect": "add"}


class RecordingExtension:
    def __init__(self, pending=False):
        self.calls = []
        self.is_pending = pending

    def preview(self, org, action):
        return [dict(OBJECT)]

    def pending(self, org):
        return self.is_pending

    def install(self, org, actor):
        self.calls.append("install")

    def upgrade(self, org, actor):
        self.calls.append("upgrade")

    def remove(self, org, actor):
        self.calls.append("remove")


@pytest.fixture(autouse=True)
def _no_pack_source():
    yield
    unregister_pack_source()


def test_install_pack_installs_an_unregistered_manifest(org_a, admin_profile):
    ext = RecordingExtension()
    row = install_pack(org_a, PACK, ext, admin_profile, sample_data=False)
    assert row.app_id == "x-test-app" and row.status == InstalledApp.INSTALLED
    assert Tags.objects.filter(org=org_a, name="Solar").exists()
    assert ext.calls == ["install"]


def test_a_private_app_never_becomes_the_org_vertical(org_a, admin_profile):
    install_pack(org_a, PACK, RecordingExtension(), admin_profile, sample_data=False)
    org_a.refresh_from_db()
    assert org_a.vertical == ""
    install_app(org_a, "real-estate", admin_profile, sample_data=False)
    org_a.refresh_from_db()
    assert org_a.vertical == "real-estate"


def test_upgrade_pack_uses_the_extension_it_is_given(org_a, admin_profile):
    install_pack(org_a, PACK, RecordingExtension(), admin_profile, sample_data=False)
    ext = RecordingExtension(pending=True)
    row = upgrade_pack(org_a, {**PACK, "version": 2}, ext, admin_profile)
    assert row.installed_version == 2 and ext.calls == ["upgrade"]


def test_upgrade_pack_with_nothing_pending_is_a_conflict(org_a, admin_profile):
    install_pack(org_a, PACK, RecordingExtension(), admin_profile, sample_data=False)
    ext = RecordingExtension(pending=False)
    with pytest.raises(AppConflict, match="This app has no update."):
        upgrade_pack(org_a, {**PACK, "version": 2}, ext, admin_profile)
    assert ext.calls == []


def test_remove_pack_keeps_records_and_calls_the_extension(org_a, admin_profile):
    install_pack(org_a, PACK, RecordingExtension(), admin_profile, sample_data=False)
    ext = RecordingExtension()
    row = remove_pack(org_a, PACK, ext, admin_profile)
    assert row.status == InstalledApp.REMOVED and ext.calls == ["remove"]
    assert Tags.objects.filter(org=org_a, name="Solar").exists()


def test_plan_pack_appends_the_extension_preview(org_a):
    plan = plan_pack(org_a, PACK, RecordingExtension(), "install")
    assert plan["action"] == "install"
    assert OBJECT in plan["items"]
    assert any(i["type"] == "tag" and i["effect"] == "add" for i in plan["items"])


def test_plan_pack_without_an_extension(org_a):
    plan = plan_pack(org_a, PACK, None, "install")
    assert all(i["type"] != "object" for i in plan["items"])


def test_pack_source_can_only_be_registered_once():
    register_pack_source(lambda org, app_id: None)
    with pytest.raises(RuntimeError):
        register_pack_source(lambda org, app_id: None)


def test_heirs_read_a_private_app_wording_through_the_pack_source(org_a, admin_profile):
    words = {"lead.singular": "Enquiry"}
    pack = {**PACK, "terminology": words}
    register_pack_source(lambda org, app_id: pack if app_id == pack["id"] else None)
    install_app(org_a, "real-estate", admin_profile, sample_data=False)
    install_pack(org_a, pack, RecordingExtension(), admin_profile, sample_data=False)
    org_a.refresh_from_db()
    assert org_a.terminology["lead.singular"] == "Enquiry"
    remove_app(org_a, "real-estate", admin_profile)
    org_a.refresh_from_db()
    assert org_a.terminology["lead.singular"] == "Enquiry"


def test_a_shipped_manifest_with_the_private_prefix_fails_to_load(
    tmp_path, monkeypatch
):
    (tmp_path / "x-anything.json").write_text(
        json.dumps({"id": "x-anything", "version": 1, "name": "Nope"})
    )
    monkeypatch.setattr(loader, "PACKS_DIR", tmp_path)
    loader.get_registry.cache_clear()
    try:
        with pytest.raises(PackValidationError, match="x-"):
            loader.get_registry()
    finally:
        loader.get_registry.cache_clear()


def test_the_apps_api_does_not_know_private_ids(admin_client):
    url = "/api/apps/x-test-app/preview/?action=install"
    assert admin_client.get(url).status_code == 404
