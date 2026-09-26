"""Strict validation for vertical-pack manifests.

Unknown keys are errors, not ignored. A typo in a pack file must fail in CI,
not silently drop a stage. See docs/superpowers/specs/2026-08-01-vertical-packs-design.md §6.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from common.custom_fields import SUPPORTED_TARGETS, validate_definition_options

TOP_LEVEL_KEYS = {
    "id",
    "version",
    "name",
    "description",
    "terminology",
    "lead_pipeline",
    "case_pipeline",
    "task_pipeline",
    "custom_fields",
    "tags",
    "products",
    "sample_data",
}

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
    "custom_fields",
}

# Every entity `sample_data` understands, in the order the applier creates them
# . A later entity may reference an earlier one by name, never the reverse.
# Tasks are last because a task is the only row that can point at any of the
# other five. common.packs.applier imports this rather than restating it.
SAMPLE_ENTITY_KEYS = ("accounts", "contacts", "deals", "tickets", "leads", "tasks")

# Task.clean (tasks/models.py) rejects a Task linked to more than one parent, and
# Task.save calls full_clean, so the DB write raises rather than truncating. The
# schema enforces the same rule so a bad pack fails in CI instead of at apply.
SAMPLE_TASK_PARENTS = ("account", "deal", "ticket", "lead")


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


def _validate_custom_field(where: str, field: dict) -> None:
    _reject_unknown(where, field, CUSTOM_FIELD_KEYS)
    target = field.get("target")
    if target not in SUPPORTED_TARGETS:
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
        LEAD_SOURCE,
        LEAD_STATUS,
        PRIORITY_CHOICE,
        SOURCES,
        STAGES,
        STATUS_CHOICE,
    )
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

    for i, row in enumerate(leads):
        at = f"{where}.leads[{i}]"
        _reject_unknown(at, row, SAMPLE_LEAD_KEYS)
        _require(at, row, "title")
        _validate_choice(at, row.get("stage"), "stage", lead_stages)
        # Lead.source and Lead.status carry `choices` the applier never checks.
        # Both were unvalidated here until three shipped packs were found writing
        # source="website". A value LEAD_SOURCE does not contain, so the demo row
        # rendered as a blank dropdown and no source filter could reach it. The
        # row looked fine in the database, which is precisely why this needs a
        # schema check rather than trust.
        _validate_choice(at, row.get("source"), "source", {c for c, _l in LEAD_SOURCE})
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

    for i, field in enumerate(raw.get("custom_fields") or []):
        _validate_custom_field(f"manifest.custom_fields[{i}]", field)
    for i, tag in enumerate(raw.get("tags") or []):
        _validate_tag(f"manifest.tags[{i}]", tag)
    for i, product in enumerate(raw.get("products") or []):
        _validate_product(f"manifest.products[{i}]", product)

    if "sample_data" in raw:
        # Passes the whole manifest: sample rows name stages that live in the
        # pipeline sections, so the reference check needs both halves.
        _validate_sample_data("manifest.sample_data", raw["sample_data"], raw)

    return raw
