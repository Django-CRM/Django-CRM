"""CSV import for tickets (cases).

Two-phase: `parse_and_validate` reads + validates a CSV without touching the DB
beyond read-only lookups; `commit_rows` writes inside a transaction. Both phases
re-run validation so the commit endpoint is safe even if called directly.

Reference fields (`account_name`, `contact_emails`, `assigned_emails`,
`team_names`) must resolve within the caller's org, and `account_name` and
`contact_emails` only among the records the importer may open; missing
references are reported as row errors rather than silently dropped. Tags are
auto-created at commit if not present.

All reference lookups are bulk-prefetched once per call (one SELECT per
reference type), not per-row, a 5000-row file with five reference columns
runs ~6 queries during validation instead of ~25k.
"""

from __future__ import annotations

import csv
import datetime
import io
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from django.db import transaction
from django.db.models.functions import Lower

from accounts.access import visible_accounts_qs
from cases.access import visible_cases_qs
from cases.models import Case
from cases.signals import route_after_relations
from cases.workflow import DUPLICATE_BY_MERGE_ONLY
from common.models import Profile, Tags, Teams
from common.utils import CASE_TYPE, PRIORITY_CHOICE, STATUS_CHOICE
from contacts.access import replace_visible_contacts, visible_contacts_qs

REQUIRED_HEADERS = ("name", "status", "priority")
OPTIONAL_HEADERS = (
    "description",
    "case_type",
    "account_name",
    "contact_emails",
    "assigned_emails",
    "team_names",
    "tags",
    "closed_on",
)
KNOWN_HEADERS = REQUIRED_HEADERS + OPTIONAL_HEADERS

MAX_ROWS = 5000
NAME_MAX_LEN = 64
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class RowError:
    row: int  # 1-based, matching CSV line numbers (header is row 0)
    field: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"row": self.row, "field": self.field, "message": self.message}


@dataclass
class ValidatedRow:
    """A row that passed validation; ID fields are resolved to UUIDs (strings)."""

    row: int
    name: str
    status: str
    priority: str
    description: str = ""
    case_type: str | None = None
    closed_on: str | None = None
    account_id: str | None = None
    contact_ids: list[str] = field(default_factory=list)
    assigned_ids: list[str] = field(default_factory=list)
    team_ids: list[str] = field(default_factory=list)
    tag_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "name": self.name,
            "status": self.status,
            "priority": self.priority,
            "description": self.description,
            "case_type": self.case_type,
            "closed_on": self.closed_on,
            "account_id": self.account_id,
            "contact_ids": list(self.contact_ids),
            "assigned_ids": list(self.assigned_ids),
            "team_ids": list(self.team_ids),
            "tag_names": list(self.tag_names),
        }


@dataclass
class ImportResult:
    valid: list[ValidatedRow]
    errors: list[RowError]
    header_error: str | None = None

    @property
    def summary(self) -> dict[str, int]:
        return {
            "total": len(self.valid) + len({e.row for e in self.errors}),
            "valid": len(self.valid),
            "invalid": len({e.row for e in self.errors}),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "header_error": self.header_error,
            "valid": [r.to_dict() for r in self.valid],
            "errors": [e.to_dict() for e in self.errors],
            "summary": self.summary,
        }


@dataclass
class _RefMaps:
    """All reference values needed to validate + resolve rows in one pass.

    Keys are lowercased; values are the canonical UUID string for the match.
    `existing_case_names` is a set (not a map) because we only need membership.
    """

    existing_case_names: set[str]
    accounts: dict[str, str]
    contacts: dict[str, str]
    profiles: dict[str, str]
    teams: dict[str, str]


def _decode(file_bytes: bytes) -> str | None:
    """Decode a CSV byte string as UTF-8 (with or without BOM).

    Returns None for non-UTF-8 inputs rather than falling back to Latin-1,
    which would silently turn corrupted UTF-8 into mojibake and pass
    validation. Callers surface a "save as UTF-8" header_error.
    """
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _normalize_headers(raw_headers: Iterable[str]) -> list[str]:
    return [(h or "").strip().lower() for h in raw_headers]


def _coerce_choice(raw: str, choices: tuple[tuple[str, str], ...]) -> str | None:
    """Match against choice values case-insensitively; return canonical value or None."""
    if not raw:
        return None
    needle = raw.strip().lower()
    for value, _label in choices:
        if value.lower() == needle:
            return value
    return None


def _split_multi(raw: str) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(";") if part.strip()]


def parse_and_validate(file_bytes: bytes, org, profile) -> ImportResult:
    """Parse a CSV byte string and validate every row against the given org.

    All reference lookups (account, contacts, assignees, teams) are scoped to
    `org` so a malicious CSV can't reach across tenants.
    """
    text = _decode(file_bytes)
    if text is None:
        return ImportResult(
            valid=[],
            errors=[],
            header_error="File could not be decoded as UTF-8. Save your CSV as UTF-8 and try again.",
        )
    # Python's csv module accepts NUL since 3.11, and Postgres refuses it in
    # any string parameter, so one would 500 the insert.
    if "\x00" in text:
        return ImportResult(
            valid=[],
            errors=[],
            header_error="CSV could not be read: it contains NUL characters",
        )
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        # A single field over the csv module's 128 KB limit raises here, which
        # used to reach the view as a 500.
        return ImportResult(
            valid=[], errors=[], header_error=f"CSV could not be read: {exc}"
        )
    if not rows:
        return ImportResult(valid=[], errors=[], header_error="CSV is empty")

    headers = _normalize_headers(rows[0])
    missing = [h for h in REQUIRED_HEADERS if h not in headers]
    if missing:
        return ImportResult(
            valid=[],
            errors=[],
            header_error=f"Missing required header(s): {', '.join(missing)}",
        )
    unknown = [h for h in headers if h and h not in KNOWN_HEADERS]
    if unknown:
        return ImportResult(
            valid=[],
            errors=[],
            header_error=f"Unknown header(s): {', '.join(unknown)}",
        )

    data_rows = rows[1:]
    if len(data_rows) > MAX_ROWS:
        return ImportResult(
            valid=[],
            errors=[],
            header_error=f"Too many rows ({len(data_rows)}); limit is {MAX_ROWS}",
        )

    # First pass: parse each row into a dict and skip blanks.
    parsed: list[tuple[int, dict[str, str]]] = []
    for idx, raw_row in enumerate(data_rows, start=1):
        if not any((cell or "").strip() for cell in raw_row):
            continue
        record = {
            h: (raw_row[i].strip() if i < len(raw_row) else "")
            for i, h in enumerate(headers)
        }
        parsed.append((idx, record))

    ref_maps = _build_ref_maps(parsed, org, profile)

    valid: list[ValidatedRow] = []
    errors: list[RowError] = []
    seen_names: dict[str, int] = {}  # name.lower() -> row number for dup-within-file

    for idx, record in parsed:
        row_errors, validated = _validate_and_build(idx, record, ref_maps, seen_names)
        if row_errors:
            errors.extend(row_errors)
            continue
        valid.append(validated)
        seen_names[validated.name.lower()] = idx

    return ImportResult(valid=valid, errors=errors)


def _build_ref_maps(parsed: list[tuple[int, dict[str, str]]], org, profile) -> _RefMaps:
    """Bulk-prefetch every reference value referenced anywhere in the file.

    One query per reference type, scoped to `org`. Keys are lowercased so
    row-level lookups can be O(1) and case-insensitive.
    """
    candidate_names: set[str] = set()
    account_names: set[str] = set()
    contact_emails: set[str] = set()
    assigned_emails: set[str] = set()
    team_names: set[str] = set()

    for _idx, record in parsed:
        name = record.get("name", "")
        if name:
            candidate_names.add(name.lower())
        account_name = record.get("account_name", "")
        if account_name:
            account_names.add(account_name.lower())
        for email in _split_multi(record.get("contact_emails", "")):
            contact_emails.add(email.lower())
        for email in _split_multi(record.get("assigned_emails", "")):
            assigned_emails.add(email.lower())
        for team in _split_multi(record.get("team_names", "")):
            team_names.add(team.lower())

    # Only tickets the importer may open. Ticket names carry no DB constraint,
    # so this check is a courtesy against importing the same ticket twice, not
    # a rule the database would enforce. Checked across the whole org it was
    # an oracle: "A ticket with this name already exists" for a name held only
    # by a ticket the importer cannot open confirmed that ticket existed.
    existing_case_names: set[str] = set()
    if candidate_names:
        existing_case_names = set(
            visible_cases_qs(profile)
            .annotate(name_lower=Lower("name"))
            .filter(name_lower__in=candidate_names)
            .values_list("name_lower", flat=True)
        )

    # Only accounts the importer may open, for the reason given for contacts
    # below: "No account named ..." for a name nobody holds and a valid row
    # for one held by a hidden account told the importer the hidden one existed.
    accounts: dict[str, str] = {}
    if account_names:
        for pk, name_lower in (
            visible_accounts_qs(profile, profile.user)
            .annotate(name_lower=Lower("name"))
            .filter(name_lower__in=account_names)
            .values_list("id", "name_lower")
        ):
            # First-write wins so multiple same-named accounts resolve deterministically.
            accounts.setdefault(name_lower, str(pk))

    # Only contacts the importer may open. Resolving across the whole org
    # made the preview an oracle: "No contact with email" for an address
    # nobody holds, silence for one held by a contact the importer cannot
    # see. Now both get the same row error.
    contacts: dict[str, str] = {}
    if contact_emails:
        for pk, email_lower in (
            visible_contacts_qs(profile)
            .annotate(email_lower=Lower("email"))
            .filter(email_lower__in=contact_emails)
            .values_list("id", "email_lower")
        ):
            contacts.setdefault(email_lower, str(pk))

    profiles: dict[str, str] = {}
    if assigned_emails:
        for pk, email_lower in (
            Profile.objects.filter(org=org, is_active=True)
            .annotate(email_lower=Lower("user__email"))
            .filter(email_lower__in=assigned_emails)
            .values_list("id", "email_lower")
        ):
            profiles.setdefault(email_lower, str(pk))

    teams: dict[str, str] = {}
    if team_names:
        for pk, name_lower in (
            Teams.objects.filter(org=org)
            .annotate(name_lower=Lower("name"))
            .filter(name_lower__in=team_names)
            .values_list("id", "name_lower")
        ):
            teams.setdefault(name_lower, str(pk))

    return _RefMaps(
        existing_case_names=existing_case_names,
        accounts=accounts,
        contacts=contacts,
        profiles=profiles,
        teams=teams,
    )


def _validate_and_build(
    idx: int,
    record: dict,
    refs: _RefMaps,
    seen_names: dict[str, int],
) -> tuple[list[RowError], ValidatedRow | None]:
    """Validate a single row and, on success, return the resolved ValidatedRow.

    Combines validation and reference resolution so we only walk the row once
    and only touch the prefetched ref maps (no DB calls here).
    """
    errors: list[RowError] = []

    name = record.get("name", "")
    if not name:
        errors.append(RowError(idx, "name", "Name is required"))
    elif len(name) > NAME_MAX_LEN:
        errors.append(RowError(idx, "name", f"Name exceeds {NAME_MAX_LEN} characters"))
    else:
        lowered = name.lower()
        prior = seen_names.get(lowered)
        if prior:
            errors.append(
                RowError(idx, "name", f"Duplicate of row {prior} within this file")
            )
        elif lowered in refs.existing_case_names:
            errors.append(
                RowError(idx, "name", "A ticket with this name already exists")
            )

    status = _coerce_choice(record.get("status", ""), STATUS_CHOICE)
    if status == "Duplicate":
        errors.append(RowError(idx, "status", DUPLICATE_BY_MERGE_ONLY))
    elif not status:
        valid_values = ", ".join(v for v, _ in STATUS_CHOICE if v != "Duplicate")
        errors.append(RowError(idx, "status", f"Status must be one of: {valid_values}"))

    priority = _coerce_choice(record.get("priority", ""), PRIORITY_CHOICE)
    if not priority:
        valid_values = ", ".join(v for v, _ in PRIORITY_CHOICE)
        errors.append(
            RowError(idx, "priority", f"Priority must be one of: {valid_values}")
        )

    case_type_raw = record.get("case_type", "")
    case_type: str | None = None
    if case_type_raw:
        case_type = _coerce_choice(case_type_raw, CASE_TYPE)
        if case_type is None:
            valid_values = ", ".join(v for v, _ in CASE_TYPE)
            errors.append(
                RowError(idx, "case_type", f"Case type must be one of: {valid_values}")
            )

    closed_on = record.get("closed_on", "")
    if closed_on:
        # The regex alone let 2025-02-30 through to `Case.objects.create`,
        # which raised a 500 after the preview had called the row valid.
        try:
            if not DATE_RE.match(closed_on):
                raise ValueError
            datetime.date.fromisoformat(closed_on)
        except ValueError:
            errors.append(
                RowError(
                    idx, "closed_on", "Date must be a real date in YYYY-MM-DD format"
                )
            )

    account_id: str | None = None
    account_name = record.get("account_name", "")
    if account_name:
        account_id = refs.accounts.get(account_name.lower())
        if account_id is None:
            errors.append(
                RowError(
                    idx,
                    "account_name",
                    f"No account you can open is named '{account_name}'",
                )
            )

    contact_ids: list[str] = []
    for email in _split_multi(record.get("contact_emails", "")):
        if not EMAIL_RE.match(email):
            errors.append(
                RowError(idx, "contact_emails", f"'{email}' is not a valid email")
            )
            continue
        resolved = refs.contacts.get(email.lower())
        if resolved is None:
            errors.append(
                RowError(
                    idx,
                    "contact_emails",
                    f"No contact you can open has the email '{email}'",
                )
            )
        else:
            contact_ids.append(resolved)

    assigned_ids: list[str] = []
    for email in _split_multi(record.get("assigned_emails", "")):
        if not EMAIL_RE.match(email):
            errors.append(
                RowError(idx, "assigned_emails", f"'{email}' is not a valid email")
            )
            continue
        resolved = refs.profiles.get(email.lower())
        if resolved is None:
            errors.append(
                RowError(
                    idx,
                    "assigned_emails",
                    f"No active member with email '{email}'",
                )
            )
        else:
            assigned_ids.append(resolved)

    team_ids: list[str] = []
    for team_name in _split_multi(record.get("team_names", "")):
        resolved = refs.teams.get(team_name.lower())
        if resolved is None:
            errors.append(RowError(idx, "team_names", f"No team named '{team_name}'"))
        else:
            team_ids.append(resolved)

    tag_names = _split_multi(record.get("tags", ""))
    for tag_name in tag_names:
        name_error = Tags.name_error(tag_name)
        if name_error:
            errors.append(RowError(idx, "tags", f"Tag '{tag_name}': {name_error}"))

    if errors:
        return errors, None

    return [], ValidatedRow(
        row=idx,
        name=name,
        status=status or "",
        priority=priority or "",
        description=record.get("description", ""),
        case_type=case_type,
        closed_on=closed_on or None,
        account_id=account_id,
        contact_ids=contact_ids,
        assigned_ids=assigned_ids,
        team_ids=team_ids,
        tag_names=tag_names,
    )


@transaction.atomic
def commit_rows(file_bytes: bytes, org, profile) -> dict[str, Any]:
    """Re-parse the uploaded CSV and create cases in a single transaction.

    Returning structured counts lets the UI render a per-row outcome strip.
    On any unexpected error within the txn the whole batch is rolled back.
    """
    result = parse_and_validate(file_bytes, org, profile)
    if result.header_error:
        return {
            "error": True,
            "header_error": result.header_error,
            "created": 0,
        }
    if result.errors:
        # Don't write anything if any row failed; users must fix the file first.
        return {
            "error": True,
            "message": "Fix the invalid rows before importing",
            "errors": [e.to_dict() for e in result.errors],
            "created": 0,
        }

    created_ids: list[str] = []
    tag_cache: dict[str, Tags] = {}

    for vr in result.valid:
        # Routed once its contacts, assignees, teams and tags are on it.
        with route_after_relations():
            case = Case.objects.create(
                name=vr.name,
                status=vr.status,
                priority=vr.priority,
                case_type=vr.case_type,
                description=vr.description,
                closed_on=vr.closed_on,
                account_id=vr.account_id,
                org=org,
                created_by=profile.user,
            )
            if vr.contact_ids:
                # Resolved only among the contacts the importer may open (see
                # `_build_ref_maps`), and linked the way every contact write is.
                replace_visible_contacts(case.contacts, vr.contact_ids, profile)
            if vr.assigned_ids:
                case.assigned_to.set(vr.assigned_ids)
            if vr.team_ids:
                case.teams.set(vr.team_ids)
            if vr.tag_names:
                tag_objs = [
                    _get_or_create_tag(name, org, tag_cache) for name in vr.tag_names
                ]
                case.tags.set(tag_objs)
        created_ids.append(str(case.id))

    return {
        "error": False,
        "created": len(created_ids),
        "ids": created_ids,
    }


def _get_or_create_tag(name: str, org, cache: dict[str, Tags]) -> Tags:
    # Slug+org is the unique key on Tags; using it for get_or_create lets Django
    # collapse the SELECT-then-INSERT race for concurrent imports of the same tag.
    slug = Tags.slug_for(name)
    if slug in cache:
        return cache[slug]
    tag, _ = Tags.objects.get_or_create(
        slug=slug,
        org=org,
        defaults={"name": name},
    )
    cache[slug] = tag
    return tag
