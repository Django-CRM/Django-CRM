"""Industry apps: the vertical packs, installed, updated and removed per org.

An app is a pack manifest plus the org's ``InstalledApp`` row. The applier
stays the engine (create if missing, never update, never delete); the row adds
the version, ``owned`` (what the app created, which only removal switches off)
and ``offered`` (everything the app ever offered, which is how an update tells
a new part from one the org deleted). Each action runs under the applier's
per-org lock and ``suppress_rules()``, and ends with the app's extension, if
an installed Django app registered one. See docs/features/industry-apps.md.
"""

from django.db import transaction
from django.utils import timezone
from rest_framework.fields import DateTimeField

from automation.capture import suppress_rules
from common.audit_log import audit_log
from common.models import InstalledApp, Org
from common.packs.applier import PIPELINES, apply_pack, lock_org
from common.packs.loader import get_pack, get_registry
from common.packs.planner import (
    AppConflict,
    AppExtension,
    AppNotFound,
    app_extension,
    build_plan,
    has_update,
    owned_row,
    ref_of,
    register_app_extension,
    skipped_modules,
    unregister_app_extension,
)

__all__ = [
    "AppConflict",
    "AppExtension",
    "AppNotFound",
    "app_payload",
    "apps_payload",
    "install_app",
    "register_app_extension",
    "remove_app",
    "unregister_app_extension",
    "upgrade_app",
]

_STATE_FIELDS = ["terminology", "vertical", "modules_off"]


def _pack(app_id):
    pack = get_pack(app_id)
    if pack is None:
        raise AppNotFound(app_id)
    return pack


def _check_actor(org, actor):
    if actor is not None and actor.org_id != org.id:
        raise ValueError("actor must belong to the org the app is installed on")


def _locked_row(org, app_id):
    return (
        InstalledApp.objects.select_for_update().filter(org=org, app_id=app_id).first()
    )


def _switch(row, on: bool) -> None:
    row.is_active = on
    row.save(update_fields=["is_active", "updated_at"])


def _subset(pack, items) -> dict:
    """``pack`` cut down to ``items``; a pipeline brings its stages."""
    parts = {"id": pack["id"], "terminology": {}}
    for item in items:
        if item.section == "terminology":
            parts["terminology"][item.key] = item.spec
        elif item.section in PIPELINES:
            parts[item.section] = item.spec
        else:
            parts.setdefault(item.section, []).append(item.spec)
    return parts


def _record(org, row, plan, report) -> None:
    """Fold a run's report and the plan's marks into ``owned`` and ``offered``."""
    owned = {}
    for entry in row.owned:
        if entry.get("off"):
            # Installing a removed app: wording it set again comes back
            # through the report, and anything else stays owned while it exists.
            if entry["type"] == "wording":
                continue
            entry = {k: v for k, v in entry.items() if k != "off"}
            if owned_row(org, entry) is None:
                continue
        owned[ref_of(entry)] = entry
    offered = {ref_of(e): e for e in row.offered}
    for entry in report["offered"]:
        ref = ref_of(entry)
        offered[ref] = {
            "type": entry["type"],
            "target": entry["target"],
            "key": entry["key"],
            "skipped_module": entry["skipped_module"],
        }
        if entry["created"]:
            owned[ref] = {
                "type": entry["type"],
                "target": entry["target"],
                "key": entry["key"],
                "id": entry["id"],
            }
    for item, skipped in plan.mark:
        offered[item.ref] = {
            "type": item.type,
            "target": item.target,
            "key": item.key,
            "skipped_module": skipped,
        }
    row.owned = list(owned.values())
    row.offered = list(offered.values())


def _lock(org):
    """Take the per-org lock, then the org row, and plan and write from that
    copy, so a module switched or a word changed meanwhile cannot slip past."""
    lock_org(org)
    return Org.objects.select_for_update().get(pk=org.pk)


def _sync(org, locked) -> None:
    for name in _STATE_FIELDS:
        setattr(org, name, getattr(locked, name))


def _extend(action: str, app_id, org, actor) -> None:
    extension = app_extension(app_id)
    if extension is not None:
        getattr(extension, action)(org, actor)


def install_app(org, app_id, actor, sample_data: bool, *, request=None):
    """Install ``app_id`` on ``org``, or install a removed one again.

    Raises ``AppNotFound`` for an id outside the catalogue and ``AppConflict``
    when the app is already installed.
    """
    pack = _pack(app_id)
    _check_actor(org, actor)
    with transaction.atomic(), suppress_rules():
        locked = _lock(org)
        row = _locked_row(locked, app_id)
        plan = build_plan(locked, pack, row, "install")
        for entry, obj in plan.turn_on:
            _switch(obj, True)
            entry.pop("off")
        now = timezone.now()
        report = apply_pack(
            locked, pack, actor, parts=_subset(pack, plan.run), sample_data=sample_data
        )
        if row is None:
            row = InstalledApp(org=locked, app_id=app_id)
        _record(locked, row, plan, report)
        row.status = InstalledApp.INSTALLED
        row.installed_version = pack["version"]
        row.installed_by = actor
        row.installed_at = now
        row.removed_at = None
        row.save()
        _extend("install", app_id, locked, actor)
        audit_log.app_changed(
            "APP_INSTALLED", _user(actor), locked, pack, row.installed_version, request
        )
    _sync(org, locked)
    return row


def upgrade_app(org, app_id, actor, *, request=None):
    """Add what the app offers now and never offered before, and parts skipped
    for a module that is back on. ``AppConflict`` when that adds nothing and
    the app's extension has nothing pending."""
    pack = _pack(app_id)
    _check_actor(org, actor)
    with transaction.atomic(), suppress_rules():
        locked = _lock(org)
        row = _locked_row(locked, app_id)
        plan = build_plan(locked, pack, row, "upgrade")
        report = apply_pack(
            locked, pack, actor, parts=_subset(pack, plan.run), sample_data=False
        )
        _record(locked, row, plan, report)
        row.installed_version = pack["version"]
        row.save()
        _extend("upgrade", app_id, locked, actor)
        audit_log.app_changed(
            "APP_UPGRADED", _user(actor), locked, pack, row.installed_version, request
        )
    _sync(org, locked)
    return row


def remove_app(org, app_id, actor, *, request=None):
    """Switch off the fields, sources and products the app owns and put back
    its wording, keeping every record. ``AppConflict`` when not installed."""
    pack = _pack(app_id)
    _check_actor(org, actor)
    with transaction.atomic(), suppress_rules():
        locked = _lock(org)
        row = _locked_row(locked, app_id)
        others = list(
            InstalledApp.objects.select_for_update()
            .filter(org=locked, status=InstalledApp.INSTALLED)
            .exclude(app_id=app_id)
            .order_by("created_at")
        )
        plan = build_plan(locked, pack, row, "remove", others)
        for entry, obj in plan.turn_off:
            _switch(obj, False)
            entry["off"] = True
        if plan.restore:
            # Read again: a word is put back only while it is still the app's.
            locked.refresh_from_db(fields=["terminology"])
            terms = dict(locked.terminology or {})
            mine = pack.get("terminology") or {}
            for entry in plan.restore:
                if terms.get(entry["key"]) == mine.get(entry["key"]):
                    del terms[entry["key"]]
                    entry["off"] = True
            if terms != locked.terminology:
                locked.terminology = terms
                locked.save(update_fields=["terminology"])
        handed = {id(entry) for entry, _heir in plan.hand_over}
        for entry, heir in plan.hand_over:
            heir.owned = [e for e in heir.owned if ref_of(e) != ref_of(entry)]
            heir.owned.append(entry)
            heir.save(update_fields=["owned", "updated_at"])
        row.owned = [e for e in row.owned if id(e) not in handed]
        row.status = InstalledApp.REMOVED
        row.removed_at = timezone.now()
        row.save()
        _extend("remove", app_id, locked, actor)
        audit_log.app_changed(
            "APP_REMOVED", _user(actor), locked, pack, row.installed_version, request
        )
    _sync(org, locked)
    return row


def _user(actor):
    return actor.user if actor is not None else None


def app_payload(org, pack: dict, row) -> dict:
    """One app as ``GET /api/apps/`` lists it for ``org``."""
    status = row.status if row is not None else "not_installed"
    installed = status == InstalledApp.INSTALLED
    by = row.installed_by if row is not None else None
    return {
        "id": pack["id"],
        "name": pack["name"],
        "description": pack.get("description", ""),
        "version": pack["version"],
        "status": status,
        "installed_version": row.installed_version if row is not None else None,
        "update_available": has_update(org, pack, row),
        "skipped_modules": skipped_modules(pack, row) if installed else [],
        "installed_at": (
            DateTimeField().to_representation(row.installed_at)
            if row is not None
            else None
        ),
        "installed_by_details": (
            {"id": str(by.id), "name": by.user.name or by.user.email}
            if by is not None
            else None
        ),
    }


def apps_payload(org) -> dict:
    rows = {
        r.app_id: r
        for r in InstalledApp.objects.filter(org=org).select_related(
            "installed_by__user"
        )
    }
    return {
        "apps": [
            app_payload(org, pack, rows.get(app_id))
            for app_id, pack in get_registry().items()
        ]
    }
