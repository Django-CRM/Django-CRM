"""What installing, updating or removing an industry app would do, worked out
without writing anything.

``build_plan`` is what the services in ``common.apps_catalogue`` carry out, and
``plan_app`` is the preview the API answers with. The existence checks are the
applier's own, so a preview and the run after it decide alike.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from accounts.models import Account
from common.models import CustomFieldDefinition, InstalledApp, Tags
from common.modules import KEYS, model_module, module_is_available
from common.packs.applier import (
    DEAL_PIPELINES,
    PIPELINE_TARGETS,
    PIPELINES,
    PRODUCT_MODULES,
    blocked_module,
    custom_field_exists,
    deal_stage_exists,
    field_modules,
    field_target,
    pipeline_exists,
    pipeline_model,
    pipeline_modules,
    product_exists,
    sample_models,
    stage_key,
    tag_exists,
    wording_target,
)
from common.packs.loader import get_pack
from contacts.models import Contact
from invoices.models import Product
from leads.models import Lead, LeadSource
from leads.sources import source_keys

ACTIONS = ("install", "upgrade", "remove")
TYPE_ORDER = (
    "wording",
    "object",
    "field",
    "pipeline",
    "stage",
    "tag",
    "source",
    "product",
)
NO_SAMPLE = {"offered": False, "default_on": False, "records": 0}

# How each renamable record type reads before any app renames it.
_WORDS = {
    "lead": ("Lead", "Leads"),
    "opportunity": ("Deal", "Deals"),
    "contact": ("Contact", "Contacts"),
    "account": ("Account", "Accounts"),
    "invoice": ("Invoice", "Invoices"),
}

# Removal switches these off and a later install back on; the rest stay as they are.
SWITCHED = {"field": CustomFieldDefinition, "source": LeadSource, "product": Product}
_PIPELINE_MODELS = {target: pipeline_model(k) for k, target in PIPELINE_TARGETS.items()}

_SAMPLE_SECTIONS = {
    "accounts": "sample_account",
    "contacts": "sample_contact",
    "deals": "sample_deal",
    "tickets": "sample_ticket",
    "leads": "sample_lead",
    "projects": "sample_project",
    "tasks": "sample_task",
    "time_entries": "sample_time_entry",
}
_TASK_PARENTS = {
    "deal": "sample_deal",
    "ticket": "sample_ticket",
    "lead": "sample_lead",
    "project": "sample_project",
}


class AppNotFound(Exception):
    pass


class AppConflict(Exception):
    """The action does not apply to the app's state; the message says why."""


class AppExtension(Protocol):
    """What an installed Django app adds to one industry app beyond its pack.

    ``preview`` answers items shaped like ``Plan.add`` rows, of type
    ``object``. ``pending`` is whether an update would add something.
    ``install``, ``upgrade`` and ``remove`` run after the pack's own parts,
    inside the action's lock, transaction and ``suppress_rules()``.
    """

    def preview(self, org, action: str) -> list[dict]: ...
    def pending(self, org) -> bool: ...
    def install(self, org, actor) -> None: ...
    def upgrade(self, org, actor) -> None: ...
    def remove(self, org, actor) -> None: ...


_EXTENSION_METHODS = ("preview", "pending", "install", "upgrade", "remove")
_extensions: dict[str, AppExtension] = {}


def register_app_extension(app_id: str, extension: AppExtension) -> None:
    """Attach ``extension`` to the app ``app_id``, once, from ``AppConfig.ready``."""
    if get_pack(app_id) is None:
        raise RuntimeError(f"There is no app {app_id!r} to extend.")
    missing = [
        m for m in _EXTENSION_METHODS if not callable(getattr(extension, m, None))
    ]
    if missing:
        raise TypeError(f"An app extension needs {', '.join(missing)}.")
    if app_id in _extensions:
        raise RuntimeError(f"The app {app_id!r} already has an extension.")
    _extensions[app_id] = extension


def unregister_app_extension(app_id: str) -> None:
    _extensions.pop(app_id, None)


def app_extension(app_id: str) -> AppExtension | None:
    return _extensions.get(app_id)


def _extension_pending(org, extension: AppExtension | None) -> bool:
    return extension is not None and bool(extension.pending(org))


_pack_source: Callable[[object, str], dict | None] | None = None


def register_pack_source(source: Callable[[object, str], dict | None]) -> None:
    """Let ``installed_pack`` find manifests the shipped registry lacks, once."""
    global _pack_source
    if _pack_source is not None:
        raise RuntimeError("A pack source is already registered.")
    _pack_source = source


def unregister_pack_source() -> None:
    global _pack_source
    _pack_source = None


def installed_pack(org, app_id: str) -> dict | None:
    pack = get_pack(app_id)
    if pack is None and _pack_source is not None:
        pack = _pack_source(org, app_id)
    return pack


@dataclass(eq=False)
class Item:
    """One part an app offers, named by natural key. Sample rows are not parts."""

    type: str
    target: str | None
    key: str
    label: str
    section: str
    spec: object
    modules: tuple[str, ...] = ()
    pipeline: str | None = None

    @property
    def ref(self) -> tuple:
        return (self.type, self.target, self.key)


def ref_of(entry: dict) -> tuple:
    return (entry["type"], entry["target"], entry["key"])


def wording_label(key: str, value: str) -> str:
    entity, _, form = key.partition(".")
    words = _WORDS.get(entity)
    if words is None or form not in ("singular", "plural"):
        return f"{key}: {value}"
    return f"{words[form == 'plural']} called {value}"


def app_items(pack: dict) -> list[Item]:
    items = [
        Item("wording", wording_target(k), k, wording_label(k, v), "terminology", v)
        for k, v in (pack.get("terminology") or {}).items()
    ]
    for f in pack.get("custom_fields") or []:
        target = field_target(f["target"])
        modules = field_modules(f["target"])
        items.append(
            Item("field", target, f["key"], f["label"], "custom_fields", f, modules)
        )
    for section in PIPELINES:
        spec = pack.get(section)
        if not spec:
            continue
        name, target = spec["name"], PIPELINE_TARGETS[section]
        modules = pipeline_modules(section)
        items.append(Item("pipeline", target, name, name, section, spec, modules))
        for stage in spec.get("stages") or []:
            label = f"{stage['name']} ({name})"
            key = stage_key(name, stage["name"])
            items.append(
                Item("stage", target, key, label, section, stage, modules, name)
            )
    target, modules = PIPELINE_TARGETS[DEAL_PIPELINES], pipeline_modules(DEAL_PIPELINES)
    for spec in pack.get(DEAL_PIPELINES) or []:
        name = spec["name"]
        items.append(
            Item("pipeline", target, name, name, DEAL_PIPELINES, spec, modules)
        )
        for stage in spec["stages"]:
            label = f"{stage['label']} ({name})"
            key = stage_key(name, stage["code"])
            items.append(
                Item("stage", target, key, label, DEAL_PIPELINES, stage, modules, name)
            )
    for tag in pack.get("tags") or []:
        slug = Tags.slug_for(tag["name"])
        items.append(Item("tag", None, slug, tag["name"], "tags", tag))
    for source in pack.get("lead_sources") or []:
        key, label = source["key"], source["label"]
        items.append(Item("source", None, key, label, "lead_sources", source))
    for product in pack.get("products") or []:
        sku, name = product["sku"], product["name"]
        items.append(
            Item("product", None, sku, name, "products", product, PRODUCT_MODULES)
        )
    return items


def owned_row(org, entry: dict):
    """The row an ``owned`` entry names, while it still exists."""
    if entry["type"] in SWITCHED:
        model = SWITCHED[entry["type"]]
    elif entry["type"] == "tag":
        model = Tags
    elif entry["type"] == "pipeline":
        model = _PIPELINE_MODELS.get(entry["target"])
    else:
        return None
    if model is None or not entry.get("id"):
        return None
    return model.objects.filter(org=org, pk=entry["id"]).first()


def _row_label(row) -> str:
    return getattr(row, "label", None) or row.name


def _offered(row) -> dict:
    return {ref_of(e): e for e in (row.offered if row is not None else [])}


def _stage_exists(org, item: Item) -> bool:
    if item.section == DEAL_PIPELINES:
        return deal_stage_exists(org, item.pipeline, item.spec["code"])
    StageModel = PIPELINES[item.section][1]
    return StageModel.objects.filter(
        org=org, pipeline__name=item.pipeline, name=item.spec["name"]
    ).exists()


def _additions(org, items, offered) -> list[Item]:
    """Parts never offered, and parts skipped for a module that is back on.
    Stages come with their pipeline."""
    out = []
    for item in items:
        if item.type == "stage":
            continue
        seen = offered.get(item.ref)
        if seen is None or (
            seen.get("skipped_module") and blocked_module(org, item.modules) is None
        ):
            out.append(item)
    return out


def skipped_modules(pack: dict, row) -> list[str]:
    refs = {item.ref for item in app_items(pack)}
    found = {
        e["skipped_module"]
        for e in row.offered
        if e.get("skipped_module") and ref_of(e) in refs
    }
    return [k for k in KEYS if k in found]


def _sample_records(org, pack: dict) -> int:
    """Sample rows the applier would try to create, less off-module ones."""
    models = dict(sample_models())

    def on(type_):
        key = model_module(models[type_]._meta.label)
        return key is None or module_is_available(org, key)

    sample = pack.get("sample_data") or {}
    count = 0
    for section, type_ in _SAMPLE_SECTIONS.items():
        for row in sample.get(section) or []:
            needs = [type_]
            if section == "tasks":
                needs += [t for parent, t in _TASK_PARENTS.items() if row.get(parent)]
            if section == "time_entries":
                needs.append("sample_project")
            count += all(on(t) for t in needs)
    return count


def _sample_block(org, pack: dict) -> dict:
    records = _sample_records(org, pack)
    if not records or any(
        model.objects.filter(org=org, is_sample=True).exists()
        for _type, model in sample_models()
    ):
        return dict(NO_SAMPLE)
    real = any(
        model.objects.filter(org=org, is_sample=False).exists()
        for model in (Lead, Contact, Account)
    )
    return {"offered": True, "default_on": not real, "records": records}


@dataclass
class Plan:
    """``rows`` is the preview. ``run``: the parts the applier goes over.
    ``mark``: ``(stage item, skipped_module)`` recorded as offered without a
    run. ``turn_on`` and ``turn_off``: ``(owned entry, row)``. ``restore``:
    owned wording entries. ``hand_over``: ``(owned entry, InstalledApp)`` for a
    part another installed app offers too, which passes to that app instead of
    being switched off."""

    action: str
    rows: list = field(default_factory=list)
    run: list = field(default_factory=list)
    mark: list = field(default_factory=list)
    turn_on: list = field(default_factory=list)
    turn_off: list = field(default_factory=list)
    restore: list = field(default_factory=list)
    hand_over: list = field(default_factory=list)
    sample: dict = field(default_factory=lambda: dict(NO_SAMPLE))

    def add(self, type_, target, label, effect) -> None:
        self.rows.append(
            {"type": type_, "target": target, "label": label, "effect": effect}
        )

    def preview(self) -> dict:
        items = sorted(self.rows, key=lambda r: TYPE_ORDER.index(r["type"]))
        return {"action": self.action, "items": items, "sample_data": self.sample}


class _Present:
    """Existence checks against one org, with its terminology and sources read once."""

    def __init__(self, org):
        self.org = org
        self.terms = org.terminology if isinstance(org.terminology, dict) else {}
        self._sources = None

    def __call__(self, item: Item) -> bool:
        org = self.org
        if item.type == "wording":
            return item.key in self.terms
        if item.type == "field":
            return custom_field_exists(org, item.spec["target"], item.key)
        if item.type == "pipeline":
            return pipeline_exists(org, item.section, item.key)
        if item.type == "stage":
            return _stage_exists(org, item)
        if item.type == "tag":
            return tag_exists(org, item.spec["name"])
        if item.type == "source":
            if self._sources is None:
                self._sources = source_keys(org, active_only=False)
            return item.key in self._sources
        return product_exists(org, item.key)


def _add_run(plan: Plan, org, items, present) -> None:
    for item in items:
        label = item.label
        if blocked_module(org, item.modules) is not None:
            effect = "skipped_module"
        elif present(item):
            effect = "keep"
            if item.type == "wording":
                # The org's own word stays, so the row names it.
                label = wording_label(item.key, present.terms[item.key])
        else:
            effect = "add"
        plan.add(item.type, item.target, label, effect)
    plan.run = list(items)


def _plan_install(org, pack, row) -> Plan:
    plan = Plan("install")
    items = app_items(pack)
    present = _Present(org)
    if row is None:
        run = [i for i in items if i.type != "stage"]
    else:
        # A removed app: what removal switched off comes back on, wording
        # included, and what was never offered is added; what the org deleted
        # stays deleted.
        back = set()
        for entry in row.owned:
            if not entry.get("off"):
                continue
            if entry["type"] == "wording":
                back.add(entry["key"])
                continue
            obj = owned_row(org, entry)
            if obj is not None and not obj.is_active:
                plan.turn_on.append((entry, obj))
                plan.add(entry["type"], entry["target"], _row_label(obj), "turn_on")
        offered = _offered(row)
        run = _additions(org, items, offered) + [
            i for i in items if i.type == "wording" and i.key in back
        ]
    _add_run(plan, org, run, present)
    if row is not None:
        _mark_new_stages(plan, org, items, offered, present)
    plan.sample = _sample_block(org, pack)
    return plan


def _mark_new_stages(plan: Plan, org, items, offered, present) -> None:
    """Stages new to a pipeline the app offered before: recorded as offered,
    and named for the admin when the pipeline is there without them. The app
    never adds a stage to a pipeline that exists."""
    with_pipeline = {(i.target, i.key) for i in plan.run if i.type == "pipeline"}
    for stage in items:
        if stage.type != "stage" or stage.ref in offered:
            continue
        if (stage.target, stage.pipeline) in with_pipeline:
            continue
        parent = offered.get(("pipeline", stage.target, stage.pipeline)) or {}
        skipped = parent.get("skipped_module")
        plan.mark.append((stage, skipped))
        if (
            skipped is None
            and pipeline_exists(org, stage.section, stage.pipeline)
            and not present(stage)
        ):
            plan.add("stage", stage.target, stage.label, "add_by_hand")


def _plan_upgrade(org, pack, row) -> Plan:
    plan = Plan("upgrade")
    items = app_items(pack)
    offered = _offered(row)
    present = _Present(org)
    _add_run(plan, org, _additions(org, items, offered), present)
    _mark_new_stages(plan, org, items, offered, present)
    return plan


def _adds(plan: Plan) -> bool:
    return any(r["effect"] == "add" for r in plan.rows)


def has_update(org, pack: dict, row, extension: AppExtension | None = None) -> bool:
    """Whether updating would add something: a part never offered or skipped
    for a module that is back, which the org does not have, or what the app's
    extension has pending. A newer version with nothing to add, or a stage
    only to add by hand, is no update."""
    if row is None or row.status != InstalledApp.INSTALLED:
        return False
    return _adds(_plan_upgrade(org, pack, row)) or _extension_pending(org, extension)


def _heirs(org, others) -> dict:
    """Parts other installed apps offer, by natural key: ``(row, wording)``."""
    heirs = {}
    for other in others:
        terms = (installed_pack(org, other.app_id) or {}).get("terminology") or {}
        for entry in other.offered:
            if entry.get("skipped_module"):
                continue
            heirs.setdefault(ref_of(entry), (other, terms.get(entry["key"])))
    return heirs


def _plan_remove(org, pack, row, others) -> Plan:
    plan = Plan("remove")
    terms = org.terminology if isinstance(org.terminology, dict) else {}
    mine = pack.get("terminology") or {}
    heirs = _heirs(org, others)
    for entry in row.owned:
        kind, target = entry["type"], entry["target"]
        heir = heirs.get(ref_of(entry))
        if kind == "wording":
            current = terms.get(entry["key"])
            if current is None:
                continue
            label = wording_label(entry["key"], current)
            if current != mine.get(entry["key"]):
                plan.add(kind, target, label, "keep")
            elif heir is not None and heir[1] == current:
                plan.hand_over.append((entry, heir[0]))
                plan.add(kind, target, label, "keep")
            else:
                plan.restore.append(entry)
                plan.add(kind, target, label, "restore_wording")
        elif kind in SWITCHED:
            obj = owned_row(org, entry)
            if obj is None or not obj.is_active:
                continue
            if heir is not None:
                plan.hand_over.append((entry, heir[0]))
                plan.add(kind, target, _row_label(obj), "keep")
            else:
                plan.turn_off.append((entry, obj))
                plan.add(kind, target, _row_label(obj), "turn_off")
        elif kind in ("pipeline", "tag"):
            obj = owned_row(org, entry)
            if obj is not None:
                plan.add(kind, target, _row_label(obj), "keep")
    return plan


def build_plan(
    org, pack: dict, row, action: str, others=(), extension: AppExtension | None = None
) -> Plan:
    """``row`` is the org's ``InstalledApp`` for this app or None; ``others``
    are its other installed apps, which removal hands shared parts to;
    ``extension`` is the one whose ``pending()`` counts as an update."""
    installed = row is not None and row.status == InstalledApp.INSTALLED
    if action == "install":
        if installed:
            raise AppConflict("This app is already installed.")
        return _plan_install(org, pack, row)
    if not installed:
        raise AppConflict("This app is not installed.")
    if action == "upgrade":
        plan = _plan_upgrade(org, pack, row)
        if not _adds(plan) and not _extension_pending(org, extension):
            raise AppConflict("This app has no update.")
        return plan
    return _plan_remove(org, pack, row, others)


def plan_pack(org, pack: dict, extension: AppExtension | None, action: str) -> dict:
    """The preview of ``action`` for ``pack`` on ``org``. Writes nothing."""
    if action not in ACTIONS:
        raise ValueError(f"Unknown action {action!r}")
    app_id = pack["id"]
    row = InstalledApp.objects.filter(org=org, app_id=app_id).first()
    others = []
    if action == "remove":
        others = list(
            InstalledApp.objects.filter(org=org, status=InstalledApp.INSTALLED)
            .exclude(app_id=app_id)
            .order_by("created_at")
        )
    plan = build_plan(org, pack, row, action, others, extension)
    if extension is not None:
        plan.rows.extend(extension.preview(org, action))
    return plan.preview()


def plan_app(org, app_id: str, action: str) -> dict:
    """The preview of ``action`` for ``app_id`` on ``org``. Writes nothing."""
    pack = get_pack(app_id)
    if pack is None:
        raise AppNotFound(app_id)
    return plan_pack(org, pack, app_extension(app_id), action)
