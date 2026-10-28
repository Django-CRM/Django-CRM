"""App extensions: parts an installed Django app adds to an industry app.

The catalogue calls an app's extension inside the same transaction as the
pack's own parts, adds its preview items, and counts what it has pending as an
update. Without one, an app behaves exactly as before.
"""

import pytest
from django.db import connection

from automation import capture
from common.apps_catalogue import (
    AppConflict,
    install_app,
    register_app_extension,
    remove_app,
    unregister_app_extension,
    upgrade_app,
)
from common.models import CustomFieldDefinition, InstalledApp, PackApplication
from common.packs.loader import get_pack
from common.packs.planner import TYPE_ORDER, plan_app
from leads.models import Lead, LeadPipeline

APP = "real-estate"
OBJECT = {"type": "object", "target": "", "label": "Property", "effect": "add"}


class FakeExtension:
    def __init__(self):
        self.items = [dict(OBJECT)]
        self.is_pending = False
        self.fail = None
        self.calls = []

    def _call(self, name, org, actor):
        self.calls.append(
            {
                "method": name,
                "org": org.pk,
                "actor": actor,
                "atomic": connection.in_atomic_block,
                "rules_suppressed": capture._suppressed.get(),
                "pipeline": LeadPipeline.objects.filter(org=org, name="Sales").exists(),
                "fields_on": CustomFieldDefinition.objects.filter(
                    org=org, key="budget_max", is_active=True
                ).exists(),
            }
        )
        if self.fail == name:
            raise RuntimeError("the extension failed")

    def preview(self, org, action):
        self.calls.append({"method": "preview", "action": action})
        return [dict(item) for item in self.items]

    def pending(self, org):
        self.calls.append({"method": "pending"})
        return self.is_pending

    def install(self, org, actor):
        self._call("install", org, actor)

    def upgrade(self, org, actor):
        self._call("upgrade", org, actor)

    def remove(self, org, actor):
        self._call("remove", org, actor)

    def called(self, method):
        return [c for c in self.calls if c["method"] == method]


@pytest.fixture
def extension():
    ext = FakeExtension()
    register_app_extension(APP, ext)
    yield ext
    unregister_app_extension(APP)


def _app(client):
    res = client.get("/api/apps/")
    assert res.status_code == 200
    return next(a for a in res.json()["apps"] if a["id"] == APP)


def test_extension_install_runs_after_pack_parts_in_one_transaction(
    org_a, admin_profile, extension
):
    extension.fail = "install"

    with pytest.raises(RuntimeError, match="the extension failed"):
        install_app(org_a, APP, admin_profile, sample_data=True)

    [failed] = extension.called("install")
    assert failed["pipeline"] is True
    assert not InstalledApp.objects.filter(org=org_a).exists()
    assert not LeadPipeline.objects.filter(org=org_a).exists()
    assert not CustomFieldDefinition.objects.filter(org=org_a).exists()
    assert not PackApplication.objects.filter(org=org_a).exists()
    assert not Lead.objects.filter(org=org_a).exists()
    org_a.refresh_from_db()
    assert (org_a.terminology, org_a.vertical) == ({}, "")

    extension.fail = None
    install_app(org_a, APP, admin_profile, sample_data=False)

    assert extension.called("install")[-1] == {
        "method": "install",
        "org": org_a.pk,
        "actor": admin_profile,
        "atomic": True,
        "rules_suppressed": True,
        "pipeline": True,
        "fields_on": True,
    }
    assert InstalledApp.objects.get(org=org_a, app_id=APP).status == "installed"


def test_extension_upgrade_alone_makes_an_update_available(
    org_a, admin_profile, admin_client, extension
):
    install_app(org_a, APP, admin_profile, sample_data=False)
    assert _app(admin_client)["update_available"] is False
    extension.is_pending = True
    extension.items = [dict(OBJECT, effect="add"), dict(OBJECT, label="Site visit")]

    assert _app(admin_client)["update_available"] is True
    res = admin_client.get(f"/api/apps/{APP}/preview/?action=upgrade")
    assert res.status_code == 200
    assert res.json()["items"] == extension.items
    assert extension.calls[-1] == {"method": "preview", "action": "upgrade"}

    res = admin_client.post(f"/api/apps/{APP}/upgrade/", {}, format="json")

    assert res.status_code == 200
    [call] = extension.called("upgrade")
    assert call["actor"] == admin_profile
    assert (call["atomic"], call["rules_suppressed"]) == (True, True)
    row = InstalledApp.objects.get(org=org_a, app_id=APP)
    assert row.installed_version == get_pack(APP)["version"]


def test_upgrade_with_nothing_pending_anywhere_is_a_conflict(
    org_a, admin_profile, admin_client, extension
):
    install_app(org_a, APP, admin_profile, sample_data=False)

    assert _app(admin_client)["update_available"] is False
    with pytest.raises(AppConflict):
        upgrade_app(org_a, APP, admin_profile)
    res = admin_client.get(f"/api/apps/{APP}/preview/?action=upgrade")
    assert res.status_code == 409
    res = admin_client.post(f"/api/apps/{APP}/upgrade/", {}, format="json")
    assert res.status_code == 409
    assert extension.called("upgrade") == []
    assert extension.called("pending")


def test_extension_remove_is_called(org_a, admin_profile, extension):
    install_app(org_a, APP, admin_profile, sample_data=False)

    remove_app(org_a, APP, admin_profile)

    [call] = extension.called("remove")
    assert call["org"] == org_a.pk and call["actor"] == admin_profile
    assert (call["atomic"], call["rules_suppressed"]) == (True, True)
    # After the pack's own removal: its fields are already off.
    assert call["fields_on"] is False
    assert InstalledApp.objects.get(org=org_a, app_id=APP).status == "removed"


def test_extension_remove_that_fails_keeps_the_app_installed(
    org_a, admin_profile, extension
):
    install_app(org_a, APP, admin_profile, sample_data=False)
    extension.fail = "remove"

    with pytest.raises(RuntimeError):
        remove_app(org_a, APP, admin_profile)

    assert InstalledApp.objects.get(org=org_a, app_id=APP).status == "installed"
    assert CustomFieldDefinition.objects.get(org=org_a, key="budget_max").is_active


def test_preview_appends_extension_items_after_wording(
    org_a, admin_profile, admin_client, extension
):
    res = admin_client.get(f"/api/apps/{APP}/preview/?action=install")

    assert res.status_code == 200
    items = res.json()["items"]
    types = [i["type"] for i in items]
    assert types == sorted(types, key=TYPE_ORDER.index)
    assert TYPE_ORDER[:2] == ("wording", "object")
    first_object = types.index("object")
    assert set(types[:first_object]) == {"wording"}
    assert types[first_object + 1] == "field"
    assert items[first_object] == OBJECT
    assert extension.calls[-1] == {"method": "preview", "action": "install"}

    install_app(org_a, APP, admin_profile, sample_data=False)
    extension.items = [dict(OBJECT, effect="turn_off")]
    preview = plan_app(org_a, APP, "remove")

    assert dict(OBJECT, effect="turn_off") in preview["items"]
    assert extension.calls[-1] == {"method": "preview", "action": "remove"}


def test_no_extension_behaves_as_before(org_a, admin_profile, admin_client):
    preview = admin_client.get(f"/api/apps/{APP}/preview/?action=install").json()
    assert "object" not in {i["type"] for i in preview["items"]}

    install_app(org_a, APP, admin_profile, sample_data=False)

    app = _app(admin_client)
    assert (app["status"], app["update_available"]) == ("installed", False)
    assert admin_client.post(f"/api/apps/{APP}/upgrade/", {}).status_code == 409
    remove = plan_app(org_a, APP, "remove")["items"]
    assert "object" not in {i["type"] for i in remove}
    row = remove_app(org_a, APP, admin_profile)
    assert row.status == "removed"


def test_second_extension_for_one_app_raises(extension):
    with pytest.raises(RuntimeError, match=APP):
        register_app_extension(APP, FakeExtension())

    unregister_app_extension(APP)
    register_app_extension(APP, extension)


def test_an_extension_for_an_unknown_app_or_missing_a_method_is_refused():
    with pytest.raises(RuntimeError, match="no-such-app"):
        register_app_extension("no-such-app", FakeExtension())

    class Partial:
        def preview(self, org, action):
            return []

    with pytest.raises(TypeError, match="pending"):
        register_app_extension(APP, Partial())
    unregister_app_extension(APP)
