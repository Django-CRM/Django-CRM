"""Apply a vertical-pack manifest to an org.

The single invariant: create if missing, skip if present. This module never
calls .update(), never calls .save() on a row it did not just construct, and
never deletes. That is what makes re-applying safe and what stops a pack from
destroying tenant configuration.
"""

from __future__ import annotations

import logging

from django.db import transaction

from cases.models import CasePipeline, CaseStage
from common.models import CustomFieldDefinition, Org, PackApplication, Tags
from common.packs.schema import SAMPLE_ENTITY_KEYS
from invoices.models import Product
from leads.models import LeadPipeline, LeadStage
from tasks.models import TaskPipeline, TaskStage

logger = logging.getLogger(__name__)

# manifest key -> (pipeline model, stage model, stage supports win_probability)
PIPELINES = {
    "lead_pipeline": (LeadPipeline, LeadStage, True),
    "case_pipeline": (CasePipeline, CaseStage, False),
    "task_pipeline": (TaskPipeline, TaskStage, False),
}

SAMPLE_TAG_NAME = "Sample data"


class _Report:
    def __init__(self):
        self.created: list[dict] = []
        self.skipped: list[dict] = []
        self.failed: list[dict] = []

    def add_created(self, type_: str, name: str) -> None:
        self.created.append({"type": type_, "name": name})

    def add_skipped(
        self, type_: str, name: str, reason: str = "already exists"
    ) -> None:
        self.skipped.append({"type": type_, "name": name, "reason": reason})

    def as_dict(self) -> dict:
        return {"created": self.created, "skipped": self.skipped, "failed": self.failed}


def _apply_pipelines(org, pack: dict, report: _Report) -> None:
    for key, (PipelineModel, StageModel, allow_wp) in PIPELINES.items():
        spec = pack.get(key)
        if not spec:
            continue
        name = spec["name"]
        if PipelineModel.objects.filter(org=org, name=name).exists():
            # Stages are only created beneath a pipeline we just created, so a
            # skipped pipeline takes its stages with it. This avoids
            # half-populated pipelines and keeps stage matching trivial.
            report.add_skipped(key, name)
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

        for order, stage in enumerate(spec.get("stages") or [], start=1):
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
            StageModel.objects.create(**fields)
            report.add_created(f"{key}_stage", stage["name"])


def _apply_custom_fields(org, pack: dict, report: _Report) -> None:
    for field in pack.get("custom_fields") or []:
        exists = CustomFieldDefinition.objects.filter(
            org=org, target_model=field["target"], key=field["key"]
        ).exists()
        if exists:
            report.add_skipped("custom_field", field["key"])
            continue
        CustomFieldDefinition.objects.create(
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


def _apply_tags(org, pack: dict, report: _Report) -> None:
    for tag in pack.get("tags") or []:
        # Tags.save() derives slug from name; (slug, org) is the unique key.
        if Tags.objects.filter(org=org, slug=Tags.slug_for(tag["name"])).exists():
            report.add_skipped("tag", tag["name"])
            continue
        Tags.objects.create(
            org=org,
            name=tag["name"],
            color=tag.get("color", "blue"),
            description=tag.get("description", ""),
        )
        report.add_created("tag", tag["name"])


def _apply_products(org, pack: dict, report: _Report) -> None:
    for product in pack.get("products") or []:
        # sku is mandatory in a manifest. See schema._validate_product.
        if Product.objects.filter(org=org, sku=product["sku"]).exists():
            report.add_skipped("product", product["name"])
            continue
        Product.objects.create(
            org=org,
            name=product["name"],
            sku=product["sku"],
            description=product.get("description", ""),
            price=product.get("price", 0),
            currency=product.get("currency") or org.default_currency,
            category=product.get("category"),
        )
        report.add_created("product", product["name"])


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
        else:
            report.add_created("terminology", key)

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


def _sample_models():
    """The six models sample data is created in, keyed by report type.

    Ordered as the applier creates them and, reversed, as clear_sample_data
    deletes them: a referenced row exists before the row referencing it, and is
    removed after. Defined once so the apply guard, the applier and the clear
    can never disagree about what "sample data" covers, a seventh entity added
    to one list but not the others is the failure mode this shape prevents.
    """
    from accounts.models import Account
    from cases.models import Case
    from contacts.models import Contact
    from leads.models import Lead
    from opportunity.models import Opportunity
    from tasks.models import Task

    return (
        ("sample_account", Account),
        ("sample_contact", Contact),
        ("sample_deal", Opportunity),
        ("sample_ticket", Case),
        ("sample_task", Task),
        ("sample_lead", Lead),
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


def _apply_sample_data(org, pack: dict, actor, report: _Report) -> None:
    from accounts.models import Account
    from cases.models import Case, CaseStage
    from contacts.models import Contact
    from leads.models import Lead
    from opportunity.models import Opportunity
    from tasks.models import Task, TaskStage

    sample = pack.get("sample_data") or {}
    if not any(sample.get(key) for key in SAMPLE_ENTITY_KEYS):
        return

    # is_sample, not the tag, is the re-apply guard. A tag is just a label a
    # user can freely rename/delete/attach, so it must never gate anything this
    # module decides from. See clear_sample_data for the matching deletion-side
    # rule. The guard spans every sample model, not just Lead: a pack whose
    # sample block is accounts-only must still be blocked from double-applying,
    # and a second pack must not layer its demo rows onto the first pack's.
    for _type, model in _sample_models():
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

    # Tasks last: they are the only entity that can point at any of the others,
    # so every parent map is fully populated by the time this runs.
    task_stages = {s.name: s for s in TaskStage.objects.filter(org=org)}
    for spec in sample.get("tasks") or []:
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
                custom_fields=s.get("custom_fields") or {},
                is_sample=True,
            ),
        )
        finish(obj)


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
        if any(f.name == "is_sample" for f in related._meta.fields):
            qs = qs.exclude(Q(is_sample=True) & ~Q(pk__in=retained_pks))
        blocked.update(pk for pk in qs.values_list(field, flat=True) if pk is not None)
    return blocked


def clear_sample_data(org, actor=None) -> dict:
    """Delete the demo records the applier created for THIS org, and nothing else.

    Keyed on `is_sample` alone. A server-set, non-serializer-writable boolean on
    all six models, never on the "Sample data" tag. `Tags.save()` recomputes its
    slug on every save and the tag CRUD endpoints have no reserved-slug guard, so
    the tag can be renamed onto this slug, adopted from an existing tag, or
    attached to any real record through the ordinary tag picker. None of that is
    destructive here: the tag is not touched by this function at all, and
    is_sample cannot be set through any client-facing serializer.

    Two rules make this safe to hand to an admin as a single button:

    1. **Children before parents.** _sample_models is reversed, so sample tasks
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

    Returns {"deleted": {type: n}, "retained": {type: n}}. Retained is a normal
    outcome, not an error, and the UI is expected to say so.

    `actor` is optional (mirrors apply_pack's PackApplication.applied_by) and is
    used only to attribute the audit record. It plays no part in the delete,
    which is already org-scoped.
    """
    from common.audit_log import audit_log

    deleted: dict[str, int] = {}
    retained: dict[str, int] = {}
    # Accumulated across models so a kept child keeps its parent. See
    # _ids_with_foreign_children.
    retained_pks: set = set()

    with transaction.atomic():
        for type_, model in reversed(_sample_models()):
            rows = model.objects.filter(org=org, is_sample=True)
            ids = list(rows.values_list("pk", flat=True))
            blocked = _ids_with_foreign_children(model, ids, retained_pks)
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

    return {"deleted": deleted, "retained": retained}


@transaction.atomic
def apply_pack(org, pack: dict, actor) -> dict:
    """Apply `pack` to `org`. Additive-only. Returns the created/skipped report."""
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

    # Serialize concurrent applies to the same org, keyed on org alone, not
    # org+pack. Two of our match keys (pipeline name, invoice template name)
    # are conventions rather than DB constraints, so without this two racing
    # applies of the same pack could both pass the existence check and both
    # create. But _apply_terminology also mutates the single shared Org row,
    # and that row is not pack-scoped: two concurrent applies of *different*
    # packs to the same org must serialize too, or one can clobber the
    # other's terminology/vertical write. Locking on org id alone covers
    # both cases.
    #
    # pg_advisory_xact_lock is Postgres-only. Production and every real
    # deployment run Postgres (RLS requires it: see RLS_SETUP.md), but the
    # test suite runs on SQLite (crm/test_settings.py), which has no such
    # function and would raise OperationalError. Guard on connection.vendor,
    # matching the existing pattern in common/tasks.py's set_rls_context and
    # common/management/commands/manage_rls.py: skip the Postgres-only
    # statement on other backends instead of trying to emulate it. This is a
    # real no-op only on SQLite, which is test-only in this codebase. On
    # every backend that ships to production, the lock is taken.
    from django.db import connection

    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [str(org.id)])

    report = _Report()
    _apply_pipelines(org, pack, report)
    _apply_custom_fields(org, pack, report)
    _apply_tags(org, pack, report)
    _apply_products(org, pack, report)
    _apply_terminology(org, pack, report)
    _apply_sample_data(org, pack, actor, report)

    PackApplication.objects.create(
        org=org,
        pack_id=pack["id"],
        pack_version=pack["version"],
        applied_by=actor,
        result=report.as_dict(),
    )
    return report.as_dict()
