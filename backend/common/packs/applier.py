"""Apply a vertical-pack manifest to an org.

The single invariant: create if missing, skip if present. This module never
calls .update(), never calls .save() on a row it did not just construct, and
never deletes. That is what makes re-applying safe and what stops a pack from
destroying tenant configuration.
"""

from __future__ import annotations

import copy
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import SET_NULL
from django.utils import timezone

from automation.capture import suppress_rules
from cases.models import CasePipeline, CaseStage
from common.models import CustomFieldDefinition, Org, PackApplication, Tags
from common.modules import model_module, module_is_available, module_off_message
from common.object_registry import BUILT_IN, object_module
from common.packs.schema import (
    SAMPLE_DATES_KEY,
    SAMPLE_ENTITY_KEYS,
    sample_date_slots,
)
from invoices.models import Product
from leads.models import LeadPipeline, LeadSource, LeadStage
from leads.sources import org_sources
from opportunity.models import DealPipeline, DealStage
from tasks.models import TaskPipeline, TaskStage

logger = logging.getLogger(__name__)

# manifest key -> (pipeline model, stage model, stage supports win_probability)
PIPELINES = {
    "lead_pipeline": (LeadPipeline, LeadStage, True),
    "case_pipeline": (CasePipeline, CaseStage, False),
    "task_pipeline": (TaskPipeline, TaskStage, False),
}

# A list of pipelines, where the three above hold one each.
DEAL_PIPELINES = "deal_pipelines"

# Natural keys: how an industry app (common.apps_catalogue) names each part it
# offers, as {type, target, key}. The record type a pipeline holds:
PIPELINE_TARGETS = {
    "lead_pipeline": "lead",
    "case_pipeline": "ticket",
    "task_pipeline": "task",
    DEAL_PIPELINES: "deal",
}
# The record type a terminology key renames, by its prefix.
WORDING_TARGETS = {
    "lead": "lead",
    "opportunity": "deal",
    "contact": "contact",
    "account": "account",
    "invoice": "invoice",
}
# Products follow deals or invoices; with both off the message names invoices.
PRODUCT_MODULES = ("invoices", "deals")

SAMPLE_TAG_NAME = "Sample data"


def field_target(target_model: str) -> str:
    """The record type ("lead") of a custom field target ("Lead")."""
    return next(
        (e.key for e in BUILT_IN if e.custom_field_target == target_model),
        target_model.lower(),
    )


def wording_target(key: str) -> str | None:
    return WORDING_TARGETS.get(key.partition(".")[0])


def stage_key(pipeline: str, stage: str) -> str:
    return f"{pipeline}/{stage}"


def pipeline_model(manifest_key: str):
    if manifest_key == DEAL_PIPELINES:
        return DealPipeline
    return PIPELINES[manifest_key][0]


def pipeline_modules(manifest_key: str) -> tuple[str, ...]:
    module = model_module(pipeline_model(manifest_key)._meta.label)
    return (module,) if module else ()


def field_modules(target_model: str) -> tuple[str, ...]:
    module = object_module(target=target_model)
    return (module,) if module else ()


def blocked_module(org, modules) -> str | None:
    """The module to name when none of ``modules`` is available, else None."""
    if not modules or any(module_is_available(org, m) for m in modules):
        return None
    return modules[0]


# The existence checks, shared with the planner so a preview and the run after
# it decide alike.
def pipeline_exists(org, manifest_key: str, name: str) -> bool:
    if manifest_key == DEAL_PIPELINES:
        # The deal pipeline API holds names unique ignoring case.
        return DealPipeline.objects.filter(org=org, name__iexact=name).exists()
    return PIPELINES[manifest_key][0].objects.filter(org=org, name=name).exists()


def deal_stage_exists(org, pipeline: str, code: str) -> bool:
    return DealStage.objects.filter(
        org=org, pipeline__name__iexact=pipeline, code=code
    ).exists()


def custom_field_exists(org, target_model: str, key: str) -> bool:
    return CustomFieldDefinition.objects.filter(
        org=org, target_model=target_model, key=key
    ).exists()


def tag_exists(org, name: str) -> bool:
    # Tags.save() derives slug from name; (slug, org) is the unique key.
    return Tags.objects.filter(org=org, slug=Tags.slug_for(name)).exists()


def product_exists(org, sku: str) -> bool:
    return Product.objects.filter(org=org, sku=sku).exists()


class _Report:
    def __init__(self):
        self.created: list[dict] = []
        self.skipped: list[dict] = []
        self.failed: list[dict] = []
        # Every configuration part by natural key, created or not.
        self.offered: list[dict] = []

    def add_created(self, type_: str, name: str) -> None:
        self.created.append({"type": type_, "name": name})

    def add_skipped(
        self, type_: str, name: str, reason: str = "already exists"
    ) -> None:
        self.skipped.append({"type": type_, "name": name, "reason": reason})

    def offer(
        self, type_, target, key, *, row=None, created=False, skipped_module=None
    ) -> None:
        """``row`` is what this run created; wording has none, so it passes
        ``created``."""
        self.offered.append(
            {
                "type": type_,
                "target": target,
                "key": key,
                "created": created or row is not None,
                "id": str(row.pk) if row is not None else None,
                "skipped_module": skipped_module,
            }
        )

    def as_dict(self) -> dict:
        return {
            "created": self.created,
            "skipped": self.skipped,
            "failed": self.failed,
            "offered": self.offered,
        }


def _skip_pipeline(report, key, target, name, stage_keys, org, module) -> None:
    """Record a pipeline left alone, and offer it and each stage for later."""
    if module is not None:
        report.add_skipped(key, name, reason=module_off_message(org, module))
    else:
        report.add_skipped(key, name)
    report.offer("pipeline", target, name, skipped_module=module)
    for stage in stage_keys:
        report.offer("stage", target, stage_key(name, stage), skipped_module=module)


def _apply_pipelines(org, pack: dict, report: _Report) -> None:
    for key, (PipelineModel, StageModel, allow_wp) in PIPELINES.items():
        spec = pack.get(key)
        if not spec:
            continue
        name = spec["name"]
        target = PIPELINE_TARGETS[key]
        stages = spec.get("stages") or []
        module = blocked_module(org, pipeline_modules(key))
        exists = module is None and pipeline_exists(org, key, name)
        if module is not None or exists:
            # Stages are only created beneath a pipeline we just created, so a
            # skipped pipeline takes its stages with it. This avoids
            # half-populated pipelines and keeps stage matching trivial.
            _skip_pipeline(
                report,
                key,
                target,
                name,
                [stage["name"] for stage in stages],
                org,
                module,
            )
            continue

        # is_default is never set from a pack: all three pipeline models carry a
        # one-default-per-org UniqueConstraint. The admin promotes a default.
        pipeline = PipelineModel.objects.create(
            org=org,
            name=name,
            description=spec.get("description", ""),
            is_default=False,
        )
        report.add_created(key, name)
        report.offer("pipeline", target, name, row=pipeline)

        for order, stage in enumerate(stages, start=1):
            fields = {
                "pipeline": pipeline,
                "org": org,
                "name": stage["name"],
                "order": stage.get("order", order),
                "color": stage.get("color", "#6B7280"),
                "stage_type": stage.get("stage_type", "open"),
                "maps_to_status": stage.get("maps_to_status"),
                "wip_limit": stage.get("wip_limit"),
            }
            if allow_wp and "win_probability" in stage:
                fields["win_probability"] = stage["win_probability"]
            row = StageModel.objects.create(**fields)
            report.add_created(f"{key}_stage", stage["name"])
            report.offer("stage", target, stage_key(name, stage["name"]), row=row)


def _apply_deal_pipelines(org, pack: dict, report: _Report) -> None:
    """Like ``_apply_pipelines``, for a list: a pipeline the org has by name
    is skipped with its stages, and none is ever the default."""
    specs = pack.get(DEAL_PIPELINES) or []
    module = blocked_module(org, pipeline_modules(DEAL_PIPELINES)) if specs else None
    target = PIPELINE_TARGETS[DEAL_PIPELINES]
    for spec in specs:
        name = spec["name"]
        if module is not None or pipeline_exists(org, DEAL_PIPELINES, name):
            _skip_pipeline(
                report,
                "deal_pipeline",
                target,
                name,
                [stage["code"] for stage in spec["stages"]],
                org,
                module,
            )
            continue
        pipeline = DealPipeline.objects.create(org=org, name=name, is_default=False)
        report.add_created("deal_pipeline", name)
        report.offer("pipeline", target, name, row=pipeline)
        for stage in spec["stages"]:
            row = DealStage.objects.create(
                org=org,
                pipeline=pipeline,
                code=stage["code"],
                label=stage["label"],
                order=stage["order"],
                kind=stage["kind"],
                expected_days=stage.get("expected_days"),
            )
            report.add_created("deal_pipeline_stage", stage["label"])
            report.offer("stage", target, stage_key(name, stage["code"]), row=row)


def _apply_custom_fields(org, pack: dict, report: _Report) -> None:
    for field in pack.get("custom_fields") or []:
        target = field_target(field["target"])
        module = blocked_module(org, field_modules(field["target"]))
        if module is not None:
            report.add_skipped(
                "custom_field", field["key"], reason=module_off_message(org, module)
            )
            report.offer("field", target, field["key"], skipped_module=module)
            continue
        if custom_field_exists(org, field["target"], field["key"]):
            report.add_skipped("custom_field", field["key"])
            report.offer("field", target, field["key"])
            continue
        row = CustomFieldDefinition.objects.create(
            org=org,
            target_model=field["target"],
            key=field["key"],
            label=field["label"],
            field_type=field["field_type"],
            options=field.get("options"),
            is_required=field.get("is_required", False),
            is_filterable=field.get("is_filterable", False),
            display_order=field.get("display_order", 0),
        )
        report.add_created("custom_field", field["key"])
        report.offer("field", target, field["key"], row=row)


def _apply_tags(org, pack: dict, report: _Report) -> None:
    for tag in pack.get("tags") or []:
        slug = Tags.slug_for(tag["name"])
        if tag_exists(org, tag["name"]):
            report.add_skipped("tag", tag["name"])
            report.offer("tag", None, slug)
            continue
        row = Tags.objects.create(
            org=org,
            name=tag["name"],
            color=tag.get("color", "blue"),
            description=tag.get("description", ""),
        )
        report.add_created("tag", tag["name"])
        report.offer("tag", None, slug, row=row)


def _apply_lead_sources(org, pack: dict, report: _Report) -> None:
    sources = pack.get("lead_sources") or []
    if not sources:
        return
    # Seeds the defaults first so the pack's sources follow them in the list.
    existing = org_sources(org)
    have = {s.key for s in existing}
    order = max((s.order for s in existing), default=-1) + 1
    for source in sources:
        if source["key"] in have:
            report.add_skipped("lead_source", source["label"])
            report.offer("source", None, source["key"])
            continue
        row = LeadSource.objects.create(
            org=org, key=source["key"], label=source["label"], order=order
        )
        have.add(source["key"])
        order += 1
        report.add_created("lead_source", source["label"])
        report.offer("source", None, source["key"], row=row)


def _apply_products(org, pack: dict, report: _Report) -> None:
    module = blocked_module(org, PRODUCT_MODULES)
    for product in pack.get("products") or []:
        # sku is mandatory in a manifest. See schema._validate_product.
        sku = product["sku"]
        if module is not None:
            report.add_skipped(
                "product", product["name"], reason=module_off_message(org, module)
            )
            report.offer("product", None, sku, skipped_module=module)
            continue
        if product_exists(org, sku):
            report.add_skipped("product", product["name"])
            report.offer("product", None, sku)
            continue
        row = Product.objects.create(
            org=org,
            name=product["name"],
            sku=product["sku"],
            description=product.get("description", ""),
            price=product.get("price", 0),
            currency=product.get("currency") or org.default_currency,
            category=product.get("category"),
        )
        report.add_created("product", product["name"])
        report.offer("product", None, sku, row=row)


def _apply_terminology(org, pack: dict, report: _Report) -> None:
    # `org` is the caller's in-memory object, loaded before apply_pack opened
    # its transaction. It can already be stale (e.g. a second apply of a
    # different pack applied via the same object, or genuine concurrency).
    # Deciding "already set?" from that snapshot and then saving it back
    # would overwrite whatever the DB row actually holds. Re-read the row
    # under a lock, INSIDE this transaction, and decide from the locked row
    # instead. That is the only copy of the truth we write from.
    locked = Org.objects.select_for_update().get(pk=org.pk)

    terminology = pack.get("terminology") or {}
    current = dict(locked.terminology or {})
    added = {k: v for k, v in terminology.items() if k not in current}
    for key in terminology:
        if key in current:
            report.add_skipped("terminology", key)
            report.offer("wording", wording_target(key), key)
        else:
            report.add_created("terminology", key)
            report.offer("wording", wording_target(key), key, created=True)

    changed_fields = []
    if added:
        current.update(added)
        locked.terminology = current
        changed_fields.append("terminology")
    # Set regardless of whether this pack carries any terminology, a
    # pipeline-only or products-only pack still applied, and vertical is the
    # field that records that.
    if not locked.vertical:
        locked.vertical = pack["id"]
        changed_fields.append("vertical")
    if changed_fields:
        locked.save(update_fields=changed_fields)
        # Keep the caller's object coherent with what was actually written,
        # in case they read org.terminology/org.vertical after this call.
        org.terminology, org.vertical = locked.terminology, locked.vertical


def sample_models():
    """The models sample data is created in, keyed by report type.

    Ordered as the applier creates them and, reversed, as clear_sample_data
    deletes them: a referenced row exists before the row referencing it, and is
    removed after. Defined once so the apply guard, the applier and the clear
    can never disagree about what "sample data" covers, an entity added to one
    list but not the others is the failure mode this shape prevents.
    """
    from accounts.models import Account
    from cases.models import Case, TimeEntry
    from contacts.models import Contact
    from leads.models import Lead
    from opportunity.models import Opportunity
    from projects.models import Project
    from tasks.models import Task

    return (
        ("sample_account", Account),
        ("sample_contact", Contact),
        ("sample_deal", Opportunity),
        ("sample_ticket", Case),
        ("sample_project", Project),
        ("sample_task", Task),
        ("sample_lead", Lead),
        ("sample_time_entry", TimeEntry),
    )


def _create_sample(report, type_: str, name: str, build):
    """Run one sample insert inside its own savepoint.

    One savepoint per row, around the insert only: a pre-existing row colliding
    on a unique constraint (a lead email, an account name) must skip that row,
    not roll back every pipeline/tag/custom-field/product this apply already
    wrote. transaction.atomic() nests as a savepoint here because apply_pack
    already opened the outer transaction.

    The except covers *only* `build()`. It used to wrap the tags.add() /
    assigned_to.add() follow-up too, and blamed every IntegrityError on a
    duplicate email, which was wrong, and silent, for the one it actually
    produced: assigned_to.add(None) when actor is None. Keeping the M2M work
    outside is what stops this clause from misreporting an unrelated integrity
    error as a collision. ValidationError is caught alongside because Task.save
    calls full_clean, so a bad sample task raises Django's ValidationError
    rather than an IntegrityError. Uncaught, that is a 500 out of an endpoint
    whose whole contract is "additive, never fatal".
    """
    from django.core.exceptions import ValidationError
    from django.db import IntegrityError

    try:
        with transaction.atomic():
            obj = build()
    except (IntegrityError, ValidationError) as exc:
        reason = f"could not be created ({type(exc).__name__})"
        report.add_skipped(type_, name, reason=reason)
        return None
    report.add_created(type_, name)
    return obj


def _skip_off_module(report, org, type_: str, name: str, *needs: str) -> bool:
    """Report a sample row as skipped when its module, or a module its links
    need, is off. ``needs`` are further sample types the row points at."""
    models = dict(sample_models())
    for kind in (type_, *needs):
        key = model_module(models[kind]._meta.label)
        if key is not None and not module_is_available(org, key):
            report.add_skipped(type_, name, reason=module_off_message(org, key))
            return True
    return False


def _sample_moved_to_today(pack: dict) -> dict:
    """A copy of the pack's sample_data with every date moved to the apply day.

    Each date moves by the days between the manifest's ``sample_dates_written_on``
    and today in the org's timezone (the request activated it), so a task written
    as due the next day is due the day after the starter is applied, and a deal
    that closed three weeks before still closed three weeks before. The loaded
    manifest is cached and shared, so it is copied, never edited in place.
    """
    view = {
        "custom_fields": pack.get("custom_fields"),
        "sample_data": copy.deepcopy(pack.get("sample_data") or {}),
    }
    written_on = pack.get(SAMPLE_DATES_KEY)
    if written_on:
        shift = timezone.localdate() - date.fromisoformat(written_on)
        for container, key in sample_date_slots(view):
            moved = date.fromisoformat(str(container[key])) + shift
            container[key] = moved.isoformat()
    return view["sample_data"]


@suppress_rules()
def _apply_sample_data(org, pack: dict, actor, report: _Report) -> None:
    from accounts.models import Account
    from cases.models import Case, CaseStage, TimeEntry
    from contacts.models import Contact
    from leads.models import Lead
    from opportunity.models import Opportunity
    from projects.models import Project
    from tasks.models import Task, TaskStage

    sample = _sample_moved_to_today(pack)
    if not any(sample.get(key) for key in SAMPLE_ENTITY_KEYS):
        return

    # is_sample, not the tag, is the re-apply guard. A tag is just a label a
    # user can freely rename/delete/attach, so it must never gate anything this
    # module decides from. See clear_sample_data for the matching deletion-side
    # rule. The guard spans every sample model, not just Lead: a pack whose
    # sample block is accounts-only must still be blocked from double-applying,
    # and a second pack must not layer its demo rows onto the first pack's.
    for _type, model in sample_models():
        if model.objects.filter(org=org, is_sample=True).exists():
            report.add_skipped("sample_data", pack["id"])
            return

    # The tag is attached purely for visibility/filtering in the UI. It
    # carries no authority over what apply/clear do. Adopting this tag onto a
    # real record (or renaming another tag onto this slug) is harmless,
    # nothing destructive keys off it.
    sample_tag, _ = Tags.objects.get_or_create(
        org=org,
        slug=Tags.slug_for(SAMPLE_TAG_NAME),
        defaults={"name": SAMPLE_TAG_NAME, "color": "gray"},
    )

    def finish(obj):
        """Tag and assign a freshly created sample row."""
        if obj is None:
            return
        obj.tags.add(sample_tag)
        # actor is optional (PackApplication.applied_by is nullable for the
        # same reason. A caller may apply without an assigning admin, e.g. a
        # future management command). Without an assignee the admin who applied
        # the pack has no ordinary "my records" query that reaches these rows,
        # but that is a lesser loss than silently mis-skipping every sample row,
        # which is what happened before this guard.
        if actor is not None:
            obj.assigned_to.add(actor)

    # Every reference below resolves against these maps alone. Rows this apply
    # just created. Never against a queryset of the org's existing records: a
    # sample contact that latched onto a tenant's real "Acme Corp" would put demo
    # rows inside real customer data, and would then make that real account a
    # deletion candidate when the sample data is cleared.
    accounts: dict[str, Account] = {}
    contacts: dict[str, Contact] = {}
    deals: dict[str, Opportunity] = {}
    tickets: dict[str, Case] = {}

    for spec in sample.get("accounts") or []:
        obj = _create_sample(
            report,
            "sample_account",
            spec["name"],
            lambda s=spec: Account.objects.create(
                org=org,
                name=s["name"],
                email=s.get("email"),
                phone=s.get("phone"),
                website=s.get("website"),
                industry=s.get("industry"),
                city=s.get("city"),
                country=s.get("country"),
                description=s.get("description"),
                custom_fields=s.get("custom_fields") or {},
                is_sample=True,
            ),
        )
        if obj is not None:
            accounts[spec["name"]] = obj
            finish(obj)

    for spec in sample.get("contacts") or []:
        name = f"{spec.get('first_name', '')} {spec.get('last_name', '')}".strip()
        obj = _create_sample(
            report,
            "sample_contact",
            name,
            lambda s=spec: Contact.objects.create(
                org=org,
                first_name=s["first_name"],
                last_name=s["last_name"],
                email=s.get("email"),
                phone=s.get("phone"),
                title=s.get("title"),
                department=s.get("department"),
                organization=s.get("organization"),
                account=accounts.get(s.get("account")),
                city=s.get("city"),
                country=s.get("country"),
                description=s.get("description"),
                custom_fields=s.get("custom_fields") or {},
                is_sample=True,
            ),
        )
        if obj is not None:
            contacts[name] = obj
            finish(obj)
            # Account.contacts is the M2M the account page actually reads; the
            # Contact.account FK alone leaves the account's contact list empty.
            # See the "two account links on Contact" split. Both are populated
            # here so the demo looks right from either side.
            if obj.account_id:
                obj.account.contacts.add(obj)

    for spec in sample.get("deals") or []:
        if _skip_off_module(report, org, "sample_deal", spec["name"]):
            continue
        obj = _create_sample(
            report,
            "sample_deal",
            spec["name"],
            lambda s=spec: Opportunity.objects.create(
                org=org,
                name=s["name"],
                account=accounts.get(s.get("account")),
                # Into the org's default pipeline. A code the admin has since
                # removed from it, or none, settles on its first open stage
                # (`Opportunity.save`).
                stage=s.get("stage") or "",
                amount=s.get("amount"),
                currency=s.get("currency") or org.default_currency,
                probability=s.get("probability") or 0,
                closed_on=s.get("closed_on"),
                lead_source=s.get("lead_source"),
                description=s.get("description"),
                custom_fields=s.get("custom_fields") or {},
                is_sample=True,
            ),
        )
        if obj is not None:
            deals[spec["name"]] = obj
            finish(obj)

    case_stages = {s.name: s for s in CaseStage.objects.filter(org=org)}
    for spec in sample.get("tickets") or []:
        if _skip_off_module(report, org, "sample_ticket", spec["name"]):
            continue
        obj = _create_sample(
            report,
            "sample_ticket",
            spec["name"],
            lambda s=spec: Case.objects.create(
                org=org,
                name=s["name"],
                status=s["status"],
                priority=s["priority"],
                case_type=s.get("case_type"),
                account=accounts.get(s.get("account")),
                stage=case_stages.get(s.get("stage")),
                description=s.get("description"),
                custom_fields=s.get("custom_fields") or {},
                is_sample=True,
            ),
        )
        if obj is not None:
            tickets[spec["name"]] = obj
            finish(obj)
            for contact_name in spec.get("contacts") or []:
                linked = contacts.get(contact_name)
                if linked is not None:
                    obj.contacts.add(linked)

    lead_stages = {s.name: s for s in LeadStage.objects.filter(org=org)}
    leads: dict[str, Lead] = {}
    for spec in sample.get("leads") or []:
        if _skip_off_module(report, org, "sample_lead", spec["title"]):
            continue
        obj = _create_sample(
            report,
            "sample_lead",
            spec["title"],
            lambda s=spec: Lead.objects.create(
                org=org,
                title=s["title"],
                first_name=s.get("first_name", ""),
                last_name=s.get("last_name", ""),
                email=s.get("email"),
                phone=s.get("phone"),
                source=s.get("source"),
                status=s.get("status"),
                stage=lead_stages.get(s.get("stage")),
                opportunity_amount=s.get("opportunity_amount"),
                currency=s.get("currency"),
                custom_fields=s.get("custom_fields") or {},
                is_sample=True,
            ),
        )
        if obj is not None:
            leads[spec["title"]] = obj
            finish(obj)

    projects: dict[str, Project] = {}
    for spec in sample.get("projects") or []:
        if _skip_off_module(report, org, "sample_project", spec["name"]):
            continue
        account = accounts.get(spec["account"])
        if account is None:
            report.add_skipped(
                "sample_project", spec["name"], reason="its sample account is missing"
            )
            continue
        obj = _create_sample(
            report,
            "sample_project",
            spec["name"],
            lambda s=spec, a=account: Project.objects.create(
                org=org,
                name=s["name"],
                account=a,
                deal=deals.get(s.get("deal")),
                billing_type=s.get("billing_type") or "time_and_materials",
                currency=s.get("currency") or org.default_currency,
                hourly_rate=s.get("hourly_rate"),
                budget_hours=s.get("budget_hours"),
                budget_amount=s.get("budget_amount"),
                start_date=s.get("start_date"),
                description=s.get("description") or "",
                created_by=actor.user if actor is not None else None,
                is_sample=True,
            ),
        )
        if obj is not None:
            projects[spec["name"]] = obj
            finish(obj)

    # Tasks after every entity a task can point at, so each parent map is
    # fully populated by the time this runs.
    task_stages = {s.name: s for s in TaskStage.objects.filter(org=org)}
    parent_types = {
        "deal": "sample_deal",
        "ticket": "sample_ticket",
        "lead": "sample_lead",
        "project": "sample_project",
    }
    for spec in sample.get("tasks") or []:
        named = tuple(t for k, t in parent_types.items() if spec.get(k))
        if _skip_off_module(report, org, "sample_task", spec["title"], *named):
            continue
        obj = _create_sample(
            report,
            "sample_task",
            spec["title"],
            lambda s=spec: Task.objects.create(
                org=org,
                title=s["title"],
                status=s["status"],
                priority=s["priority"],
                due_date=s.get("due_date"),
                description=s.get("description"),
                stage=task_stages.get(s.get("stage")),
                # At most one of these is set; schema._validate_sample_data
                # rejects a pack that names more, matching Task.clean.
                account=accounts.get(s.get("account")),
                opportunity=deals.get(s.get("deal")),
                case=tickets.get(s.get("ticket")),
                lead=leads.get(s.get("lead")),
                project=projects.get(s.get("project")),
                custom_fields=s.get("custom_fields") or {},
                is_sample=True,
            ),
        )
        finish(obj)

    tz = timezone.get_current_timezone()
    for spec in sample.get("time_entries") or []:
        name = f"{spec['project']}, {spec['date']}"
        if _skip_off_module(report, org, "sample_time_entry", name, "sample_project"):
            continue
        project = projects.get(spec["project"])
        if project is None or actor is None:
            reason = (
                "its sample project is missing"
                if project is None
                else "there is no one to log it for"
            )
            report.add_skipped("sample_time_entry", name, reason=reason)
            continue
        start = datetime.combine(date.fromisoformat(spec["date"]), time(9), tzinfo=tz)
        _create_sample(
            report,
            "sample_time_entry",
            name,
            lambda s=spec, p=project, at=start: TimeEntry.objects.create(
                org=org,
                project=p,
                profile=actor,
                started_at=at,
                ended_at=at + timedelta(hours=float(Decimal(str(s["hours"])))),
                description=s.get("description") or "",
                billable=p.billing_type != "non_billable",
                hourly_rate=p.hourly_rate,
                currency=p.currency,
                is_sample=True,
            ),
        )


def _referencing_relations(model):
    """Every non-M2M reverse relation through which another row points at `model`.

    Derived from the model registry rather than hand-listed, because a list of
    accessor names is exactly the kind of thing that silently rots: the next FK
    someone adds to Account would not be in it, and the omission shows up as
    deleted tenant data rather than as a failing import.

    All three on_delete behaviours matter here, which is why nothing is filtered
    by kind:

      CASCADE   deleting the sample row destroys the tenant's row with it
                (Case.account, Opportunity.account, Order.account, and the five
                children of Case including billable TimeEntry)
      PROTECT   deleting the sample row raises ProtectedError and aborts the
                whole clear (Invoice/Estimate/RecurringInvoice on Account)
      SET_NULL  deleting the sample row silently unlinks the tenant's row
                (Contact.account, Task.account, and friends)

    M2M relations are excluded on purpose: removing a through-row neither
    deletes nor orphans the other side, so it is not a reason to retain.
    """
    return [rel for rel in model._meta.related_objects if not rel.many_to_many]


def _ids_with_foreign_children(model, ids: list, retained_pks: set) -> set:
    """Of `ids`, those still referenced by a row this clear is not going to remove.

    One query per relation rather than per row. A row carrying `is_sample=True`
    normally does not count: it is the pack's own, and it has either already been
    deleted (children are processed first) or is about to be. A sample task
    pointing at a sample account is not a reason to keep that account, and a
    sample case that is the `parent` of another sample case would otherwise make
    the pair permanently undeletable.

    `retained_pks` is the exception, and it is what makes retention hold. A
    sample ticket kept because it carries billable time is no longer going
    anywhere, so its sample account must be kept too, or deleting the account
    would cascade the ticket away and take that billable time with it, which is
    exactly the loss the retention rule exists to prevent. Because the loop runs
    children first, every retained child is already known by the time its parent
    is considered.
    """
    if not ids:
        return set()
    from django.db.models import Q

    blocked = set()
    for rel in _referencing_relations(model):
        related, field = rel.related_model, rel.field.name
        qs = related.objects.filter(**{f"{field}__in": ids})
        has_flag = any(f.name == "is_sample" for f in related._meta.fields)
        if model._meta.label == "projects.Project" and rel.on_delete is SET_NULL:
            # Deleting a project keeps its time, tasks and invoices, unlinked
            # (Project's docstring), so those never keep a sample project. A
            # sample row this clear keeps does, or it would be left orphaned.
            if not has_flag:
                continue
            qs = qs.filter(is_sample=True, pk__in=retained_pks)
        elif has_flag:
            qs = qs.exclude(Q(is_sample=True) & ~Q(pk__in=retained_pks))
        blocked.update(pk for pk in qs.values_list(field, flat=True) if pk is not None)
    return blocked


def _billed_sample_ids(model, rows) -> set:
    """Sample time already on an invoice: kept like real data, since the invoice
    was raised from it."""
    if model._meta.label != "cases.TimeEntry":
        return set()
    return set(rows.filter(invoice__isnull=False).values_list("pk", flat=True))


def clear_sample_data(org, actor=None) -> dict:
    """Delete the demo records the applier created for THIS org, and nothing else.

    Keyed on `is_sample` alone. A server-set, non-serializer-writable boolean on
    every sample model, never on the "Sample data" tag. `Tags.save()` recomputes its
    slug on every save and the tag CRUD endpoints have no reserved-slug guard, so
    the tag can be renamed onto this slug, adopted from an existing tag, or
    attached to any real record through the ordinary tag picker. None of that is
    destructive here: the tag is not touched by this function at all, and
    is_sample cannot be set through any client-facing serializer.

    Two rules make this safe to hand to an admin as a single button:

    1. **Children before parents.** sample_models is reversed, so sample tasks
       and leads go before tickets, tickets before deals and contacts, and
       accounts last. A sample ticket is therefore already gone by the time its
       sample account is considered, and only the tenant's own rows are left to
       reason about.

    2. **Never take real data with it, and never fail because of it.** A user who
       logs a real deal against a demo account, bills hours against a demo
       ticket, or raises a real invoice on a demo account must lose none of it,
       and in the invoice case a naive delete would not merely lose data, it
       would raise ProtectedError and abort the entire clear. Any sample row
       still referenced by a non-sample row is kept and reported as retained.

    Returns {"deleted": {type: n}, "retained": {type: n}, "switched_off":
    {type: n}}. Retained is a normal outcome, not an error, and the UI is
    expected to say so. Rows of a module that is switched off are never deleted
    and are counted under switched_off instead.

    `actor` is optional (mirrors apply_pack's PackApplication.applied_by) and is
    used only to attribute the audit record. It plays no part in the delete,
    which is already org-scoped.
    """
    from common.audit_log import audit_log

    deleted: dict[str, int] = {}
    retained: dict[str, int] = {}
    switched_off: dict[str, int] = {}
    # Accumulated across models so a kept child keeps its parent. See
    # _ids_with_foreign_children.
    retained_pks: set = set()

    with transaction.atomic():
        for type_, model in reversed(sample_models()):
            rows = model.objects.filter(org=org, is_sample=True)
            ids = list(rows.values_list("pk", flat=True))
            module = model_module(model._meta.label)
            if module is not None and not module_is_available(org, module):
                # An off module's rows are never deleted; keeping them in
                # retained_pks keeps the sample accounts they point at.
                if ids:
                    switched_off[type_] = len(ids)
                    retained_pks.update(ids)
                continue
            blocked = _ids_with_foreign_children(model, ids, retained_pks)
            blocked |= _billed_sample_ids(model, rows)
            if blocked:
                retained[type_] = len(blocked)
                retained_pks.update(blocked)

            safe_ids = [pk for pk in ids if pk not in blocked]
            if not safe_ids:
                continue
            _total, by_model = model.objects.filter(pk__in=safe_ids).delete()

            # `_total` sums every row the collector removed, including the M2M
            # through-table rows (tags/contacts/assigned_to/teams) cascaded off
            # each deleted record, not the number of records deleted. Pull the
            # model-specific count from the per-model breakdown instead of a
            # second, racy count() query.
            count = by_model.get(model._meta.label, 0)
            if count:
                deleted[type_] = count

    # This is the only destructive operation in the feature; apply_pack records
    # itself via PackApplication, so a delete leaving no trace at all would be
    # the odd one out. See AuditLogger.sample_data_cleared.
    audit_log.sample_data_cleared(
        actor.user if actor is not None else None, org, sum(deleted.values())
    )

    return {"deleted": deleted, "retained": retained, "switched_off": switched_off}


def lock_org(org) -> None:
    """Serialize every pack and app write to one org until the transaction ends.

    Keyed on org alone, not org+pack. Two of our match keys (pipeline name,
    invoice template name) are conventions rather than DB constraints, so
    without this two racing applies of the same pack could both pass the
    existence check and both create. _apply_terminology also mutates the
    single shared Org row, so applies of *different* packs must serialize too.

    pg_advisory_xact_lock is Postgres-only; the SQLite test backend has no such
    function, so it is skipped there, as `set_rls_context` does.
    """
    from django.db import connection

    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [str(org.id)])


@transaction.atomic
def apply_pack(org, pack: dict, actor, *, parts=None, sample_data=True) -> dict:
    """Apply `pack` to `org`. Additive-only. Returns the created/skipped report.

    ``parts`` is the manifest cut down to what an app install or update adds
    (`common.apps_catalogue`); sample rows always come from ``pack``, whose
    custom fields say which of their values are dates.
    """
    # actor must belong to org. _apply_sample_data does
    # lead.assigned_to.add(actor) with no further org check of its own, so
    # without this guard a caller that passes a mismatched (org, actor) pair
    # ; e.g. an org-B apply given an org-A profile: creates cross-org
    # assignments: an org-A profile ends up assigned to org-B's leads, and
    # that profile can then read those leads through an ordinary "my leads"
    # query, which is a tenant-isolation break. This is enforced here, not
    # only in the view, because the view is not the only caller (management
    # commands, tests, future callers) and a correct-by-construction view is
    # not a substitute for the invariant actually being checked.
    if actor is not None and actor.org_id != org.id:
        raise ValueError("actor must belong to the org the pack is being applied to")

    lock_org(org)

    config = pack if parts is None else parts
    report = _Report()
    _apply_pipelines(org, config, report)
    _apply_deal_pipelines(org, config, report)
    _apply_custom_fields(org, config, report)
    _apply_tags(org, config, report)
    _apply_lead_sources(org, config, report)
    _apply_products(org, config, report)
    _apply_terminology(org, config, report)
    if sample_data:
        _apply_sample_data(org, pack, actor, report)

    PackApplication.objects.create(
        org=org,
        pack_id=pack["id"],
        pack_version=pack["version"],
        applied_by=actor,
        result=report.as_dict(),
    )
    return report.as_dict()
