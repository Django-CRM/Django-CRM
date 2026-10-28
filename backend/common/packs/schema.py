"""Strict validation for vertical-pack manifests.

Unknown keys are errors, not ignored. A typo in a pack file must fail in CI,
not silently drop a stage. See docs/superpowers/specs/2026-08-01-vertical-packs-design.md §6.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from common.custom_fields import is_supported_target, validate_definition_options
from leads.sources import derive_key

TOP_LEVEL_KEYS = {
    "id",
    "version",
    "name",
    "description",
    "terminology",
    "lead_pipeline",
    "case_pipeline",
    "task_pipeline",
    "deal_pipelines",
    "custom_fields",
    "tags",
    "lead_sources",
    "products",
    "sample_data",
    "sample_dates_written_on",
}

# The day a manifest's sample dates were written for. The applier moves every
# sample date by the days since then, so a task written as "due today" is due
# on the day the starter is applied, not on a date that has long passed.
SAMPLE_DATES_KEY = "sample_dates_written_on"

# Where sample rows carry dates: the sample section, the custom field target
# its rows belong to, and the built-in date fields of that entity.
_SAMPLE_DATE_FIELDS = (
    ("accounts", "Account", ()),
    ("contacts", "Contact", ()),
    ("deals", "Opportunity", ("closed_on",)),
    ("tickets", "Case", ()),
    ("leads", "Lead", ()),
    ("projects", "Project", ("start_date",)),
    ("tasks", "Task", ("due_date",)),
    ("time_entries", "TimeEntry", ("date",)),
)


def sample_date_slots(raw: dict):
    """Yield ``(container, key)`` for every date value in ``raw``'s sample rows.

    Covers the built-in date fields and every value of a custom field the
    manifest declares with ``field_type`` "date" for that row's entity. The
    validator checks each one, and the applier rewrites each one in a copy.
    """
    date_keys: dict[str, set] = {}
    for field in raw.get("custom_fields") or []:
        if field.get("field_type") == "date":
            date_keys.setdefault(field.get("target"), set()).add(field.get("key"))
    sample = raw.get("sample_data") or {}
    for section, target, builtins in _SAMPLE_DATE_FIELDS:
        for row in sample.get(section) or []:
            for key in builtins:
                if row.get(key) is not None:
                    yield row, key
            custom = row.get("custom_fields") or {}
            for key in sorted(date_keys.get(target, ())):
                if custom.get(key) is not None:
                    yield custom, key


#  is_default is a recognised key on both, not a typo, but is rejected by
# _reject_is_default with a specific message rather than lumped in with
# genuinely unknown keys. See _reject_is_default for why this matters.
PIPELINE_KEYS = {"name", "description", "stages", "is_default"}
STAGE_KEYS = {
    "name",
    "order",
    "color",
    "stage_type",
    "maps_to_status",
    "wip_limit",
    "is_default",
}
# win_probability exists on LeadStage only (leads/models.py:296).
LEAD_STAGE_KEYS = STAGE_KEYS | {"win_probability"}

DEAL_PIPELINE_KEYS = {"name", "stages", "is_default"}
DEAL_STAGE_KEYS = {"code", "label", "order", "kind", "expected_days"}
DEAL_STAGE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")

CUSTOM_FIELD_KEYS = {
    "target",
    "key",
    "label",
    "field_type",
    "options",
    "is_required",
    "is_filterable",
    "display_order",
}
FIELD_TYPES = {"text", "textarea", "number", "dropdown", "date", "checkbox"}

TAG_KEYS = {"name", "color", "description"}
LEAD_SOURCE_KEYS = {"key", "label"}
PRODUCT_KEYS = {"name", "sku", "price", "currency", "category", "description"}

SAMPLE_LEAD_KEYS = {
    "title",
    "first_name",
    "last_name",
    "email",
    "phone",
    "source",
    "status",
    "stage",
    "custom_fields",
    "opportunity_amount",
    "currency",
}
SAMPLE_ACCOUNT_KEYS = {
    "name",
    "email",
    "phone",
    "website",
    "industry",
    "city",
    "country",
    "description",
    "custom_fields",
}
SAMPLE_CONTACT_KEYS = {
    "first_name",
    "last_name",
    "email",
    "phone",
    "title",
    "department",
    "organization",
    "account",
    "city",
    "country",
    "description",
    "custom_fields",
}
SAMPLE_DEAL_KEYS = {
    "name",
    "account",
    "stage",
    "amount",
    "currency",
    "probability",
    "closed_on",
    "lead_source",
    "description",
    "custom_fields",
}
SAMPLE_TICKET_KEYS = {
    "name",
    "status",
    "priority",
    "case_type",
    "account",
    "contacts",
    "stage",
    "description",
    "custom_fields",
}
SAMPLE_TASK_KEYS = {
    "title",
    "status",
    "priority",
    "due_date",
    "description",
    "stage",
    "account",
    "deal",
    "ticket",
    "lead",
    "project",
    "custom_fields",
}
SAMPLE_PROJECT_KEYS = {
    "name",
    "account",
    "deal",
    "billing_type",
    "currency",
    "hourly_rate",
    "budget_hours",
    "budget_amount",
    "start_date",
    "description",
}
# Logged by the person applying the pack, stopped, at the project's rate.
SAMPLE_TIME_ENTRY_KEYS = {"project", "date", "hours", "description"}

# Every entity `sample_data` understands, in the order the applier creates them.
# A later entity may reference an earlier one by name, never the reverse.
# common.packs.applier imports this rather than restating it.
SAMPLE_ENTITY_KEYS = (
    "accounts",
    "contacts",
    "deals",
    "tickets",
    "leads",
    "projects",
    "tasks",
    "time_entries",
)

# Task.clean (tasks/models.py) rejects a Task linked to more than one parent, and
# Task.save calls full_clean, so the DB write raises rather than truncating. The
# schema enforces the same rule so a bad pack fails in CI instead of at apply.
SAMPLE_TASK_PARENTS = ("account", "deal", "ticket", "lead", "project")


class PackValidationError(Exception):
    """Raised when a manifest is malformed. Message names the offending key."""


def _reject_unknown(where: str, obj: dict, allowed: set[str]) -> None:
    if not isinstance(obj, dict):
        raise PackValidationError(f"{where}: expected an object")
    extra = set(obj) - allowed
    if extra:
        raise PackValidationError(f"{where}: unknown key(s) {sorted(extra)}")


def _reject_is_default(where: str, obj: dict) -> None:
    # Two independent reasons, one rule. All three pipeline models have a
    # one-default-per-org UniqueConstraint (IntegrityError), and
    # InvoiceTemplate.save() silently demotes other defaults (a disguised
    # update). Packs create; admins promote.
    #
    # Called BEFORE _reject_unknown at every site that uses it, so a pack
    # containing is_default gets this specific, actionable message instead of
    # a generic unknown-key error. is_default is deliberately INCLUDED in
    # PIPELINE_KEYS/STAGE_KEYS (a recognised key, not a typo) precisely so
    # _reject_unknown does not also flag it. This guard must be the sole
    # gatekeeper, or a stub-to-no-op could hide behind _reject_unknown's
    # "unknown key(s) [...]" message, which also contains the substring
    # "is_default" and would let a loose regex match pass for the wrong
    # reason.
    if isinstance(obj, dict) and "is_default" in obj:
        raise PackValidationError(
            f"{where}: is_default is not allowed in a pack, the admin promotes a default"
        )


def _stage_type_choices(pipeline_key: str) -> set[str]:
    """STAGE_TYPE_CHOICES differs per pipeline model:
    LeadStage = open/won/lost (leads/models.py:271),
    CaseStage = open/closed/rejected (cases/models.py:531),
    TaskStage = open/in_progress/completed (tasks/models.py:240).
    Lazy-imported so schema.py stays importable before the Django app
    registry is configured, same reasoning as _validate_tag's lazy import
    of Tags.
    """
    from cases.models import CaseStage
    from leads.models import LeadStage
    from tasks.models import TaskStage

    model = {
        "lead_pipeline": LeadStage,
        "case_pipeline": CaseStage,
        "task_pipeline": TaskStage,
    }[pipeline_key]
    return {choice for choice, _label in model.STAGE_TYPE_CHOICES}


def _maps_to_status_choices(pipeline_key: str) -> set[str] | None:
    """maps_to_status choices also differ per pipeline model. LeadStage maps
    to LEAD_STATUS and CaseStage maps to STATUS_CHOICE (leads/models.py:293,
    cases/models.py:551), both plain tuples in common/utils.py, lazy-imported
    for consistency with _stage_type_choices even though they don't touch the
    app registry. TaskStage.maps_to_status has no `choices` at all
    (tasks/models.py:257. A freeform CharField), so there is no vocabulary to
    validate task-pipeline stages against: this returns None and the caller
    skips the check rather than inventing a restriction the model doesn't
    have.
    """
    from common.utils import LEAD_STATUS, STATUS_CHOICE

    choices = {
        "lead_pipeline": LEAD_STATUS,
        "case_pipeline": STATUS_CHOICE,
        "task_pipeline": None,
    }[pipeline_key]
    return None if choices is None else {choice for choice, _label in choices}


def _validate_stage(
    where: str,
    stage: dict,
    *,
    allow_win_probability: bool,
    valid_stage_types: set[str],
    valid_maps_to_status: set[str] | None,
) -> None:
    _reject_is_default(where, stage)
    allowed = LEAD_STAGE_KEYS if allow_win_probability else STAGE_KEYS
    _reject_unknown(where, stage, allowed)
    if not stage.get("name"):
        raise PackValidationError(f"{where}: name is required")
    stage_type = stage.get("stage_type", "open")
    if stage_type not in valid_stage_types:
        raise PackValidationError(f"{where}: stage_type {stage_type!r} is not valid")
    maps_to_status = stage.get("maps_to_status")
    if maps_to_status and valid_maps_to_status is not None:
        if maps_to_status not in valid_maps_to_status:
            raise PackValidationError(
                f"{where}: maps_to_status {maps_to_status!r} is not valid"
            )
    if "win_probability" in stage:
        # Copied onto Lead.probability by the board move, which has a 0-100
        # CheckConstraint, and LeadStage carries the same one. bool is an int
        # subclass, so it is refused by name.
        wp = stage["win_probability"]
        if isinstance(wp, bool) or not isinstance(wp, int) or not 0 <= wp <= 100:
            raise PackValidationError(
                f"{where}: win_probability {wp!r} must be an integer from 0 to 100"
            )


def _validate_pipeline(
    where: str,
    pipeline: dict,
    *,
    pipeline_key: str,
    allow_win_probability: bool,
) -> None:
    _reject_is_default(where, pipeline)
    _reject_unknown(where, pipeline, PIPELINE_KEYS)
    if not pipeline.get("name"):
        raise PackValidationError(f"{where}: name is required")
    valid_stage_types = _stage_type_choices(pipeline_key)
    valid_maps_to_status = _maps_to_status_choices(pipeline_key)
    for i, stage in enumerate(pipeline.get("stages") or []):
        _validate_stage(
            f"{where}.stages[{i}]",
            stage,
            allow_win_probability=allow_win_probability,
            valid_stage_types=valid_stage_types,
            valid_maps_to_status=valid_maps_to_status,
        )
        # A board move into such a stage would set Duplicate without a merge.
        # `CaseStageSerializer` refuses it for a stage made through the API;
        # a pack writes stages without that serializer, so it is refused here.
        if pipeline_key == "case_pipeline" and stage.get("maps_to_status") == (
            "Duplicate"
        ):
            from cases.workflow import DUPLICATE_BY_MERGE_ONLY

            raise PackValidationError(
                f"{where}.stages[{i}]: maps_to_status 'Duplicate' is not "
                f"allowed. {DUPLICATE_BY_MERGE_ONLY}"
            )


def _deal_text(where: str, obj: dict, field: str) -> str:
    value = obj.get(field)
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > 100
    ):
        raise PackValidationError(
            f"{where}: {field} must be 1 to 100 characters with no outer spaces"
        )
    return value


def _validate_deal_stage(where: str, stage: dict, seen: dict) -> None:
    from opportunity.workflow import OPEN, STAGE_KINDS

    _reject_unknown(where, stage, DEAL_STAGE_KEYS)
    code = stage.get("code")
    if not isinstance(code, str) or not DEAL_STAGE_CODE.fullmatch(code):
        raise PackValidationError(
            f"{where}: code must be a lowercase letter, then up to 63 lowercase "
            "letters, digits or underscores"
        )
    label = _deal_text(where, stage, "label")
    order = stage.get("order")
    if isinstance(order, bool) or not isinstance(order, int) or order < 1:
        raise PackValidationError(f"{where}: order must be a whole number from 1")
    for field, value in (("code", code), ("label", label.casefold()), ("order", order)):
        if value in seen[field]:
            raise PackValidationError(
                f"{where}: {field} {stage[field]!r} is listed twice in this pipeline"
            )
        seen[field].add(value)
    kind = stage.get("kind")
    if kind not in {k for k, _label in STAGE_KINDS}:
        raise PackValidationError(f"{where}: kind {kind!r} is not open, won or lost")
    seen["kind"].add(kind)
    if "expected_days" in stage:
        days = stage["expected_days"]
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3650:
            raise PackValidationError(
                f"{where}: expected_days must be a whole number from 1 to 3650"
            )
        # The deal pipeline API clears it on a closed stage, where nothing ages.
        if kind != OPEN:
            raise PackValidationError(f"{where}: expected_days is for open stages only")


def _validate_deal_pipelines(where: str, pipelines) -> None:
    """The rules the deal pipeline API holds a pipeline to, so a pack never
    creates one the API would refuse (opportunity/views/pipeline_views.py)."""
    from opportunity.workflow import DEFAULT_PIPELINE_NAME, STAGE_KINDS

    if not isinstance(pipelines, list):
        raise PackValidationError(f"{where}: expected a list")
    names: set[str] = set()
    for i, pipeline in enumerate(pipelines):
        at = f"{where}[{i}]"
        _reject_is_default(at, pipeline)
        _reject_unknown(at, pipeline, DEAL_PIPELINE_KEYS)
        name = _deal_text(at, pipeline, "name")
        # An org without a default yet gets one by this name on its first deal
        # read; a pack pipeline holding the name would make that create fail.
        if name.casefold() == DEFAULT_PIPELINE_NAME.casefold():
            raise PackValidationError(
                f"{at}: {name!r} is the name of every org's default deal pipeline"
            )
        if name.casefold() in names:
            raise PackValidationError(f"{at}: name {name!r} is listed twice")
        names.add(name.casefold())
        stages = pipeline.get("stages")
        if not isinstance(stages, list) or not stages:
            raise PackValidationError(f"{at}: stages must be a non-empty list")
        seen = {"code": set(), "label": set(), "order": set(), "kind": set()}
        for j, stage in enumerate(stages):
            _validate_deal_stage(f"{at}.stages[{j}]", stage, seen)
        for kind, label in STAGE_KINDS:
            if kind not in seen["kind"]:
                raise PackValidationError(
                    f"{at}: no {label.lower()} stage; a deal pipeline needs at "
                    "least one open, one won and one lost stage"
                )


def _validate_custom_field(where: str, field: dict) -> None:
    _reject_unknown(where, field, CUSTOM_FIELD_KEYS)
    target = field.get("target")
    if not is_supported_target(target):
        raise PackValidationError(
            f"{where}: target {target!r} is not a supported entity"
        )
    if not field.get("key"):
        raise PackValidationError(f"{where}: key is required")
    if not field.get("label"):
        raise PackValidationError(f"{where}: label is required")
    field_type = field.get("field_type")
    if field_type not in FIELD_TYPES:
        raise PackValidationError(f"{where}: field_type {field_type!r} is not valid")
    # Reuse the exact validator the API uses, so a pack can never create a
    # definition the API would reject.
    try:
        validate_definition_options(field_type, field.get("options"))
    except Exception as exc:  # DRF ValidationError
        raise PackValidationError(f"{where}: {exc}") from exc


def _validate_tag(where: str, tag: dict) -> None:
    from common.models import Tags

    _reject_unknown(where, tag, TAG_KEYS)
    if not tag.get("name"):
        raise PackValidationError(f"{where}: name is required")
    name_error = Tags.name_error(tag["name"])
    if name_error:
        raise PackValidationError(f"{where}: {name_error}")
    color = tag.get("color", "blue")
    valid = {choice for choice, _label in Tags.COLOR_CHOICES}
    if color not in valid:
        raise PackValidationError(f"{where}: color {color!r} is not a valid tag colour")


def pack_source_keys(raw: dict) -> set[str]:
    """The sources a pack's sample leads may use: the defaults every org has,
    and the ones the pack adds."""
    from leads.sources import DEFAULT_SOURCES

    keys = {key for key, _label in DEFAULT_SOURCES}
    keys.update(
        s["key"]
        for s in raw.get("lead_sources") or []
        if isinstance(s, dict) and isinstance(s.get("key"), str)
    )
    return keys


def _validate_lead_sources(where: str, sources) -> None:
    if not isinstance(sources, list):
        raise PackValidationError(f"{where}: expected a list")
    seen: set[str] = set()
    for i, source in enumerate(sources):
        at = f"{where}[{i}]"
        if not isinstance(source, dict):
            raise PackValidationError(f"{at}: expected an object")
        _reject_unknown(at, source, LEAD_SOURCE_KEYS)
        _require(at, source, "key", "label")
        key, label = source["key"], source["label"]
        if not isinstance(key, str) or not isinstance(label, str):
            raise PackValidationError(f"{at}: key and label must be strings")
        if key != derive_key(key) or not 0 < len(key) <= 64:
            raise PackValidationError(
                f"{at}: key must be lowercase, single-spaced, at most 64 characters"
            )
        if not 0 < len(label) <= 100:
            raise PackValidationError(f"{at}: label must be 1 to 100 characters")
        if key in seen:
            raise PackValidationError(f"{at}: key {key!r} is listed twice")
        seen.add(key)


def _validate_product(where: str, product: dict) -> None:
    _reject_unknown(where, product, PRODUCT_KEYS)
    if not product.get("name"):
        raise PackValidationError(f"{where}: name is required")
    # Product.sku is nullable and Postgres allows unlimited NULLs in a unique
    # index, so a sku-less product would double-create on every re-apply.
    if not product.get("sku"):
        raise PackValidationError(f"{where}: sku is required for pack products")
    if "price" in product:
        try:
            Decimal(str(product["price"]))
        except (InvalidOperation, TypeError):
            raise PackValidationError(f"{where}: price is not a number") from None


def _require(where: str, obj: dict, *keys: str) -> None:
    for key in keys:
        if not obj.get(key):
            raise PackValidationError(f"{where}: {key} is required")


def _validate_choice(where: str, value, field: str, allowed: set[str]) -> None:
    """Reject a value the model's `choices` would reject.

    Django does not enforce `choices` on .create(), only full_clean() does, and
    of the six sample models only Task calls it. So without this check a pack
    could write a stage or status no UI can render and no filter can find, and
    the row would look fine in the DB. Skips None/absent: every one of these
    fields is optional unless _require said otherwise.
    """
    if value is None:
        return
    if value not in allowed:
        raise PackValidationError(
            f"{where}: {field} {value!r} is not valid (expected one of {sorted(allowed)})"
        )


def _validate_amount(where: str, value, field: str) -> None:
    """A number of 0 or more, or absent."""
    if value is None:
        return
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise PackValidationError(f"{where}: {field} is not a number") from None
    if not amount.is_finite() or amount < 0:
        raise PackValidationError(f"{where}: {field} must be 0 or more")


def _validate_date(where: str, value, field: str) -> None:
    if value is None:
        return
    from datetime import date

    try:
        date.fromisoformat(str(value))
    except ValueError:
        raise PackValidationError(
            f"{where}: {field} {value!r} is not an ISO date (YYYY-MM-DD)"
        ) from None


def _reference_index(where: str, rows: list, key_of, label: str) -> set[str]:
    """Build the set of names later entities may reference, rejecting duplicates.

    A reference is resolved by name, so two sample accounts called "Northwind"
    would make `"account": "Northwind"` ambiguous, and the applier would pick
    one arbitrarily. Catching it here keeps the reference scheme honest.
    """
    seen: set[str] = set()
    for i, row in enumerate(rows):
        name = key_of(row)
        if name in seen:
            raise PackValidationError(
                f"{where}[{i}]: duplicate {label} {name!r}. Sample references are by name, "
                "so each must be unique within the pack"
            )
        seen.add(name)
    return seen


def _check_reference(
    where: str, value, field: str, known: set[str], target: str
) -> None:
    if value is None:
        return
    if value not in known:
        raise PackValidationError(
            f"{where}: {field} {value!r} does not match any {target} in this pack"
        )


def _pipeline_stage_names(raw: dict, pipeline_key: str) -> set[str]:
    spec = raw.get(pipeline_key) or {}
    return {s.get("name") for s in (spec.get("stages") or []) if s.get("name")}


def _validate_sample_data(where: str, sample: dict, raw: dict) -> None:
    """Validate sample_data, including every cross-entity name reference.

    References are checked against the pack's own lists, not the database,
    because that is the only thing true at authoring time, and because the
    applier deliberately resolves them the same way. A sample contact must
    never attach itself to a tenant's real account (see _apply_sample_data),
    so "resolvable" here means "declared in this pack" and nothing wider.
    """
    from common.utils import (
        CASE_TYPE,
        COUNTRIES,
        CURRENCY_CODES,
        INDCHOICES,
        LEAD_STATUS,
        PRIORITY_CHOICE,
        SOURCES,
        STAGES,
        STATUS_CHOICE,
    )
    from projects.models import STARTING_BILLING_TYPES
    from tasks.models import Task

    _reject_unknown(where, sample, set(SAMPLE_ENTITY_KEYS))

    countries = {c for c, _label in COUNTRIES}
    currencies = {c for c, _label in CURRENCY_CODES}
    industries = {c for c, _label in INDCHOICES}

    accounts = sample.get("accounts") or []
    contacts = sample.get("contacts") or []
    deals = sample.get("deals") or []
    tickets = sample.get("tickets") or []
    tasks = sample.get("tasks") or []
    leads = sample.get("leads") or []
    projects = sample.get("projects") or []
    time_entries = sample.get("time_entries") or []

    def contact_name(row):
        return f"{row.get('first_name', '')} {row.get('last_name', '')}".strip()

    known_accounts = _reference_index(
        f"{where}.accounts", accounts, lambda r: r.get("name"), "account name"
    )
    known_contacts = _reference_index(
        f"{where}.contacts", contacts, contact_name, "contact name"
    )
    known_deals = _reference_index(
        f"{where}.deals", deals, lambda r: r.get("name"), "deal name"
    )
    known_tickets = _reference_index(
        f"{where}.tickets", tickets, lambda r: r.get("name"), "ticket name"
    )
    known_leads = _reference_index(
        f"{where}.leads", leads, lambda r: r.get("title"), "lead title"
    )
    known_projects = _reference_index(
        f"{where}.projects", projects, lambda r: r.get("name"), "project name"
    )

    lead_stages = _pipeline_stage_names(raw, "lead_pipeline")
    case_stages = _pipeline_stage_names(raw, "case_pipeline")
    task_stages = _pipeline_stage_names(raw, "task_pipeline")

    for i, row in enumerate(accounts):
        at = f"{where}.accounts[{i}]"
        _reject_unknown(at, row, SAMPLE_ACCOUNT_KEYS)
        _require(at, row, "name")
        _validate_choice(at, row.get("industry"), "industry", industries)
        _validate_choice(at, row.get("country"), "country", countries)

    for i, row in enumerate(contacts):
        at = f"{where}.contacts[{i}]"
        _reject_unknown(at, row, SAMPLE_CONTACT_KEYS)
        _require(at, row, "first_name", "last_name")
        _validate_choice(at, row.get("country"), "country", countries)
        _check_reference(
            at, row.get("account"), "account", known_accounts, "sample account"
        )

    for i, row in enumerate(deals):
        at = f"{where}.deals[{i}]"
        _reject_unknown(at, row, SAMPLE_DEAL_KEYS)
        _require(at, row, "name")
        _validate_choice(at, row.get("stage"), "stage", {c for c, _l in STAGES})
        _validate_choice(
            at, row.get("lead_source"), "lead_source", {c for c, _l in SOURCES}
        )
        _validate_choice(at, row.get("currency"), "currency", currencies)
        _validate_date(at, row.get("closed_on"), "closed_on")
        _check_reference(
            at, row.get("account"), "account", known_accounts, "sample account"
        )

    for i, row in enumerate(tickets):
        at = f"{where}.tickets[{i}]"
        _reject_unknown(at, row, SAMPLE_TICKET_KEYS)
        # status and priority have no model default and are not nullable.
        _require(at, row, "name", "status", "priority")
        _validate_choice(
            at, row.get("status"), "status", {c for c, _l in STATUS_CHOICE}
        )
        _validate_choice(
            at, row.get("priority"), "priority", {c for c, _l in PRIORITY_CHOICE}
        )
        _validate_choice(
            at, row.get("case_type"), "case_type", {c for c, _l in CASE_TYPE}
        )
        _validate_choice(at, row.get("stage"), "stage", case_stages)
        _check_reference(
            at, row.get("account"), "account", known_accounts, "sample account"
        )
        for name in row.get("contacts") or []:
            _check_reference(at, name, "contacts", known_contacts, "sample contact")

    for i, row in enumerate(tasks):
        at = f"{where}.tasks[{i}]"
        _reject_unknown(at, row, SAMPLE_TASK_KEYS)
        _require(at, row, "title", "status", "priority")
        _validate_choice(
            at, row.get("status"), "status", {c for c, _l in Task.STATUS_CHOICES}
        )
        _validate_choice(
            at, row.get("priority"), "priority", {c for c, _l in Task.PRIORITY_CHOICES}
        )
        _validate_choice(at, row.get("stage"), "stage", task_stages)
        _validate_date(at, row.get("due_date"), "due_date")
        parents = [p for p in SAMPLE_TASK_PARENTS if row.get(p)]
        if len(parents) > 1:
            raise PackValidationError(
                f"{at}: a task may link to at most one parent, got {parents} "
                "(tasks.Task.clean rejects more, and Task.save runs full_clean)"
            )
        _check_reference(
            at, row.get("account"), "account", known_accounts, "sample account"
        )
        _check_reference(at, row.get("deal"), "deal", known_deals, "sample deal")
        _check_reference(
            at, row.get("ticket"), "ticket", known_tickets, "sample ticket"
        )
        _check_reference(at, row.get("lead"), "lead", known_leads, "sample lead")
        _check_reference(
            at, row.get("project"), "project", known_projects, "sample project"
        )

    for i, row in enumerate(projects):
        at = f"{where}.projects[{i}]"
        _reject_unknown(at, row, SAMPLE_PROJECT_KEYS)
        _require(at, row, "name", "account")
        _validate_choice(
            at,
            row.get("billing_type"),
            "billing_type",
            {c for c, _l in STARTING_BILLING_TYPES},
        )
        _validate_choice(at, row.get("currency"), "currency", currencies)
        for key in ("hourly_rate", "budget_hours", "budget_amount"):
            _validate_amount(at, row.get(key), key)
        _check_reference(
            at, row.get("account"), "account", known_accounts, "sample account"
        )
        _check_reference(at, row.get("deal"), "deal", known_deals, "sample deal")

    for i, row in enumerate(time_entries):
        at = f"{where}.time_entries[{i}]"
        _reject_unknown(at, row, SAMPLE_TIME_ENTRY_KEYS)
        _require(at, row, "project", "date", "hours")
        _validate_amount(at, row.get("hours"), "hours")
        if not 0 < Decimal(str(row["hours"])) <= 24:
            raise PackValidationError(f"{at}: hours must be more than 0 and at most 24")
        _check_reference(
            at, row.get("project"), "project", known_projects, "sample project"
        )

    for i, row in enumerate(leads):
        at = f"{where}.leads[{i}]"
        _reject_unknown(at, row, SAMPLE_LEAD_KEYS)
        _require(at, row, "title")
        _validate_choice(at, row.get("stage"), "stage", lead_stages)
        # Lead.status carries `choices` the applier never checks, and a source
        # is a key the applier never checks either. Both were unvalidated here
        # until three shipped packs were found writing
        # source="website". A key the org's list does not hold, so the demo row
        # rendered as a blank dropdown and no source filter could reach it. The
        # row looked fine in the database, which is precisely why this needs a
        # schema check rather than trust.
        _validate_choice(at, row.get("source"), "source", pack_source_keys(raw))
        _validate_choice(at, row.get("status"), "status", {c for c, _l in LEAD_STATUS})
        _validate_choice(at, row.get("currency"), "currency", currencies)


def validate_manifest(raw: dict) -> dict:
    """Validate a manifest. Returns it unchanged, or raises PackValidationError."""
    _reject_unknown("manifest", raw, TOP_LEVEL_KEYS)

    for key in ("id", "name"):
        if not raw.get(key):
            raise PackValidationError(f"manifest: {key} is required")
    if not isinstance(raw.get("version"), int):
        raise PackValidationError("manifest: version must be an integer")

    terminology = raw.get("terminology") or {}
    if not isinstance(terminology, dict):
        raise PackValidationError("manifest.terminology: expected an object")
    for k, v in terminology.items():
        if not isinstance(v, str) or len(v) > 64:
            raise PackValidationError(
                f"manifest.terminology[{k!r}]: must be a string of at most 64 characters"
            )

    for key, allow_wp in (
        ("lead_pipeline", True),
        ("case_pipeline", False),
        ("task_pipeline", False),
    ):
        if key in raw:
            _validate_pipeline(
                f"manifest.{key}",
                raw[key],
                pipeline_key=key,
                allow_win_probability=allow_wp,
            )
    if "deal_pipelines" in raw:
        _validate_deal_pipelines("manifest.deal_pipelines", raw["deal_pipelines"])

    for i, field in enumerate(raw.get("custom_fields") or []):
        _validate_custom_field(f"manifest.custom_fields[{i}]", field)
    for i, tag in enumerate(raw.get("tags") or []):
        _validate_tag(f"manifest.tags[{i}]", tag)
    if "lead_sources" in raw:
        _validate_lead_sources("manifest.lead_sources", raw["lead_sources"])
    for i, product in enumerate(raw.get("products") or []):
        _validate_product(f"manifest.products[{i}]", product)

    if "sample_data" in raw:
        # Passes the whole manifest: sample rows name stages that live in the
        # pipeline sections, so the reference check needs both halves.
        _validate_sample_data("manifest.sample_data", raw["sample_data"], raw)

    slots = list(sample_date_slots(raw))
    for container, key in slots:
        _validate_date("manifest.sample_data", container[key], key)
    if raw.get(SAMPLE_DATES_KEY) is not None:
        _validate_date("manifest", raw[SAMPLE_DATES_KEY], SAMPLE_DATES_KEY)
    elif slots:
        raise PackValidationError(
            f"manifest: {SAMPLE_DATES_KEY} is required when sample_data holds dates,"
            " so they can be moved to the day the pack is applied"
        )

    return raw
