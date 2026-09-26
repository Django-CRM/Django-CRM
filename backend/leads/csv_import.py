"""CSV import for leads.

Same two-phase contract as the contacts and cases importers:
`parse_and_validate` reads and validates a CSV with read-only lookups, and
`commit_rows` re-runs that validation and writes inside one transaction, so
the commit endpoint is safe to call without a preview.

Every row is validated by `LeadCreateSerializer`, the serializer behind
`POST /api/leads/`, so an imported lead obeys exactly the rules a lead created
one at a time does (field lengths, choices, email format, one lead per email
per org). This module adds only what a file needs on top of that: header
checks, duplicates within the file, and resolving `assigned_emails`,
`team_names` and `tags` inside the caller's org.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models.functions import Lower

from common.custom_fields import validate_payload as validate_custom_fields_payload
from common.models import Profile, Tags, Teams
from leads.models import Lead
from leads.serializer import LeadCreateSerializer
from leads.workflow import IRREVERSIBLE_STATUSES

REQUIRED_HEADERS = ("first_name", "last_name")
# Every field the create API accepts, bar `is_active`: an import that creates
# leads already hidden from the list is a mistake, not a use case.
FIELD_HEADERS = tuple(
    name for name in LeadCreateSerializer.Meta.fields if name != "is_active"
)
REFERENCE_HEADERS = ("assigned_emails", "team_names", "tags")
KNOWN_HEADERS = FIELD_HEADERS + REFERENCE_HEADERS
# Column names from the 1.9.2 `upload/` endpoint, for the fields that have
# since been renamed; its other columns (`title`, `email`, `city`, ...) already
# match. Applied to every import, so an old file reads the same at
# preview/commit as at `upload/`. No alias is itself a field name, so none can
# shadow one, and a file naming a field twice is refused.
HEADER_ALIASES = {
    "first name": "first_name",
    "last name": "last_name",
    "address": "address_line",
}

MAX_ROWS = 5000

# Choice cells match on the stored value or its label, case-insensitively, so
# "In Process" and "in process" both land as "in process". Anything else is
# passed through unchanged for the serializer to reject.
CHOICE_MAPS = {
    f.name: {
        str(text).lower(): value
        for value, label in f.choices
        for text in (value, label)
    }
    for f in Lead._meta.get_fields()
    if f.name in FIELD_HEADERS and getattr(f, "choices", None)
}


@dataclass
class RowError:
    row: int  # 1-based over the data rows; the header is not counted
    field: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"row": self.row, "field": self.field, "message": self.message}


@dataclass
class ValidatedRow:
    row: int
    serializer: LeadCreateSerializer
    assigned_ids: list[str] = field(default_factory=list)
    team_ids: list[str] = field(default_factory=list)
    tag_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = self.serializer.validated_data
        return {
            "row": self.row,
            "first_name": data.get("first_name"),
            "last_name": data.get("last_name"),
            "title": data.get("title"),
            "email": data.get("email"),
            "company_name": data.get("company_name"),
            "status": data.get("status"),
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
        invalid = len({e.row for e in self.errors})
        return {
            "total": len(self.valid) + invalid,
            "valid": len(self.valid),
            "invalid": invalid,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "header_error": self.header_error,
            "valid": [r.to_dict() for r in self.valid],
            "errors": [e.to_dict() for e in self.errors],
            "summary": self.summary,
        }


def _header_error(message: str) -> ImportResult:
    return ImportResult(valid=[], errors=[], header_error=message)


def _decode(file_bytes: bytes) -> str | None:
    """UTF-8 with or without a BOM; None for anything else."""
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _split_multi(raw: str) -> list[str]:
    return [part.strip() for part in (raw or "").split(";") if part.strip()]


def parse_and_validate(file_bytes: bytes, request, legacy=False) -> ImportResult:
    """Validate every row of the CSV against the caller's org.

    `request` is what `LeadCreateSerializer` takes to find the org; it is the
    only source of the org here, never the file.

    `legacy` is the deprecated `upload/` endpoint, which has to keep taking
    the files 1.9.2 took: no header is required, a column it does not know is
    ignored rather than refused (1.9.2's `account_name` among them), and a
    row may be identified by its title instead of a name.
    """
    org = request.profile.org
    text = _decode(file_bytes)
    if text is None:
        return _header_error(
            "File could not be decoded as UTF-8. Save your CSV as UTF-8 and try again."
        )
    # Python's csv module accepts NUL since 3.11, and Postgres refuses it in
    # any string parameter, so one would 500 the reference lookups below.
    if "\x00" in text:
        return _header_error("CSV could not be read: it contains NUL characters")
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        return _header_error(f"CSV could not be read: {exc}")
    if not rows:
        return _header_error("CSV is empty")

    headers = [(h or "").strip().lower() for h in rows[0]]
    headers = [HEADER_ALIASES.get(h, h) for h in headers]
    repeated = sorted(
        {h for h in headers if h in KNOWN_HEADERS and headers.count(h) > 1}
    )
    if repeated:
        return _header_error(f"Header(s) given more than once: {', '.join(repeated)}")
    if not legacy:
        missing = [h for h in REQUIRED_HEADERS if h not in headers]
        if missing:
            return _header_error(f"Missing required header(s): {', '.join(missing)}")
        unknown = [h for h in headers if h and h not in KNOWN_HEADERS]
        if unknown:
            return _header_error(f"Unknown header(s): {', '.join(unknown)}")

    data_rows = rows[1:]
    if len(data_rows) > MAX_ROWS:
        return _header_error(f"Too many rows ({len(data_rows)}); limit is {MAX_ROWS}")

    # The create API refuses a lead missing a required custom field, and a
    # CSV has no way to supply one, so an import would be a way around it.
    _, cf_errors = validate_custom_fields_payload("Lead", {}, org)
    if cf_errors:
        return _header_error(
            "Your organisation requires custom field(s) on every lead that CSV "
            f"import cannot set: {', '.join(sorted(cf_errors))}"
        )

    parsed: list[tuple[int, dict[str, str]]] = []
    for idx, raw_row in enumerate(data_rows, start=1):
        if not any((cell or "").strip() for cell in raw_row):
            continue
        parsed.append(
            (
                idx,
                {
                    h: (raw_row[i].strip() if i < len(raw_row) else "")
                    for i, h in enumerate(headers)
                    if h
                },
            )
        )

    profiles, teams = _reference_maps(parsed, org)
    valid: list[ValidatedRow] = []
    errors: list[RowError] = []
    seen_emails: dict[str, int] = {}

    for idx, record in parsed:
        row_errors, validated = _validate_row(
            idx, record, request, profiles, teams, seen_emails, legacy
        )
        if row_errors:
            errors.extend(row_errors)
            continue
        valid.append(validated)
        email = validated.serializer.validated_data.get("email")
        if email:
            seen_emails[email.lower()] = idx

    return ImportResult(valid=valid, errors=errors)


def _reference_maps(parsed, org) -> tuple[dict[str, str], dict[str, str]]:
    """One query per reference type, scoped to `org`, keyed lowercase."""
    emails: set[str] = set()
    team_names: set[str] = set()
    for _idx, record in parsed:
        emails.update(e.lower() for e in _split_multi(record.get("assigned_emails")))
        team_names.update(t.lower() for t in _split_multi(record.get("team_names")))

    profiles: dict[str, str] = {}
    if emails:
        for pk, email in (
            Profile.objects.filter(org=org, is_active=True)
            .annotate(email_lower=Lower("user__email"))
            .filter(email_lower__in=emails)
            .values_list("id", "email_lower")
        ):
            profiles.setdefault(email, str(pk))

    teams: dict[str, str] = {}
    if team_names:
        for pk, name in (
            Teams.objects.filter(org=org)
            .annotate(name_lower=Lower("name"))
            .filter(name_lower__in=team_names)
            .values_list("id", "name_lower")
        ):
            teams.setdefault(name, str(pk))

    return profiles, teams


def _validate_row(idx, record, request, profiles, teams, seen_emails, legacy):
    errors: list[RowError] = []

    if legacy:
        if not any(record.get(h) for h in ("first_name", "last_name", "title")):
            errors.append(
                RowError(
                    idx, "first_name", "A lead needs a first name, last name or title"
                )
            )
    elif not record.get("first_name") and not record.get("last_name"):
        errors.append(RowError(idx, "first_name", "A lead needs a first or last name"))

    # A blank cell means "not supplied", so the model default applies, the
    # same as leaving the field off a create request.
    data = {h: v for h, v in record.items() if h in FIELD_HEADERS and v}
    for name, choices in CHOICE_MAPS.items():
        if name in data:
            data[name] = choices.get(data[name].lower(), data[name])

    if data.get("status") in IRREVERSIBLE_STATUSES:
        errors.append(
            RowError(
                idx,
                "status",
                f"A lead cannot be imported as {data['status']}. Import it, "
                "then convert it so its account, contact and deal are created.",
            )
        )

    email = data.get("email", "")
    prior = seen_emails.get(email.lower()) if email else None
    if prior:
        errors.append(
            RowError(idx, "email", f"Duplicate email also used by row {prior}")
        )

    serializer = LeadCreateSerializer(
        data=data, request_obj=request, context={"request": request}
    )
    if not serializer.is_valid():
        for name, messages in serializer.errors.items():
            errors.append(RowError(idx, name, " ".join(str(m) for m in messages)))

    assigned_ids: list[str] = []
    for email_ref in _split_multi(record.get("assigned_emails")):
        pk = profiles.get(email_ref.lower())
        if pk is None:
            errors.append(
                RowError(
                    idx,
                    "assigned_emails",
                    f"No active user '{email_ref}' in your organisation",
                )
            )
        elif pk not in assigned_ids:
            assigned_ids.append(pk)

    team_ids: list[str] = []
    for name in _split_multi(record.get("team_names")):
        pk = teams.get(name.lower())
        if pk is None:
            errors.append(
                RowError(idx, "team_names", f"No team '{name}' in your organisation")
            )
        elif pk not in team_ids:
            team_ids.append(pk)

    tag_names = _split_multi(record.get("tags"))
    for name in tag_names:
        name_error = Tags.name_error(name)
        if name_error:
            errors.append(RowError(idx, "tags", f"Tag '{name}': {name_error}"))

    if errors:
        return errors, None
    return [], ValidatedRow(
        row=idx,
        serializer=serializer,
        assigned_ids=assigned_ids,
        team_ids=team_ids,
        tag_names=tag_names,
    )


def commit_rows(file_bytes: bytes, request, legacy=False) -> dict[str, Any]:
    """Re-validate the file and create every lead, or none of them."""
    result = parse_and_validate(file_bytes, request, legacy)
    if result.header_error:
        return {"error": True, "header_error": result.header_error, "created": 0}
    if result.errors:
        return {
            "error": True,
            "message": "Fix the invalid rows before importing",
            "errors": [e.to_dict() for e in result.errors],
            "created": 0,
        }
    try:
        return _commit_validated(result.valid, request.profile)
    except IntegrityError:
        # A lead with one of these emails was created between validation and
        # the insert. The exception text is not returned: on Postgres it names
        # the constraint and echoes the conflicting key.
        return {
            "error": True,
            "message": (
                "A lead was created at the same time that conflicts with this "
                "import (likely a duplicate email). Run the preview again."
            ),
            "created": 0,
        }


@transaction.atomic
def _commit_validated(rows: list[ValidatedRow], profile) -> dict[str, Any]:
    org = profile.org
    created_ids: list[str] = []
    tag_cache: dict[str, Tags] = {}
    for vr in rows:
        lead = vr.serializer.save(org=org, created_by=profile.user)
        if vr.assigned_ids:
            lead.assigned_to.set(vr.assigned_ids)
        if vr.team_ids:
            lead.teams.set(vr.team_ids)
        if vr.tag_names:
            lead.tags.set([_get_or_create_tag(n, org, tag_cache) for n in vr.tag_names])
        created_ids.append(str(lead.id))
    return {"error": False, "created": len(created_ids), "ids": created_ids}


def _get_or_create_tag(name: str, org, cache: dict[str, Tags]) -> Tags:
    # slug+org is how Tags are keyed per org; getting by it collapses the
    # SELECT-then-INSERT race when two imports name the same new tag.
    slug = Tags.slug_for(name)
    if slug not in cache:
        cache[slug], _ = Tags.objects.get_or_create(
            slug=slug, org=org, defaults={"name": name}
        )
    return cache[slug]
