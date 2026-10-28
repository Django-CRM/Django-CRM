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

Column-matching mode (`preview_mapped`, `commit_mapped`) takes a table read
from any .csv or .xlsx plus a column-to-field mapping, and runs the same row
validator. Where the template path refuses the whole file, it skips a row
with no name or that duplicates a lead, and drops a bad optional value while
keeping the row. It finds duplicates the way the contacts importer does: by
email across the org, and by phone, or by first and last name when the row
has no usable email or phone, among the leads the importer may open.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models.functions import Lower

from automation.capture import suppress_rules
from common.custom_fields import validate_payload as validate_custom_fields_payload
from common.import_mapping import (
    FULL_NAME,
    SKIP_DUPLICATE,
    SKIP_ROW,
    MappedPreview,
    SkippedRow,
    describe_columns,
    describe_fields,
    mapped_records,
)
from common.models import Profile, Tags, Teams
from common.spreadsheet import SpreadsheetError, Table
from common.validators import normalize_phone
from contacts.services.csv_import import CONTACT_SYNONYMS
from leads.access import visible_leads_qs
from leads.alerts import quiet
from leads.models import Lead
from leads.serializer import LeadCreateSerializer
from leads.sources import org_sources
from leads.workflow import IRREVERSIBLE_STATUSES

REQUIRED_HEADERS = ("first_name", "last_name")
# Every field the create API accepts, bar `is_active`: an import that creates
# leads already hidden from the list is a mistake, not a use case. The source
# detail and reference are for intakes and the forms, not for a sheet.
FIELD_HEADERS = tuple(
    name
    for name in LeadCreateSerializer.Meta.fields
    if name not in ("is_active", "source_detail", "source_ref")
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

# The fields a mapped column may target: exactly the template's columns, plus a
# full name that is split into first and last. Nothing server-derived (org,
# creator, ids, `is_active`) is here, so a mapping can never set it. None is
# required: a row needs a first or last name, which either column gives.
LEAD_FIELDS: tuple[tuple[str, str, bool], ...] = (
    ("title", "Lead title", False),
    ("salutation", "Salutation", False),
    ("first_name", "First name", False),
    ("last_name", "Last name", False),
    ("email", "Email", False),
    ("phone", "Phone", False),
    ("job_title", "Job title", False),
    ("website", "Website", False),
    ("linkedin_url", "LinkedIn URL", False),
    ("status", "Status", False),
    ("source", "Source", False),
    ("industry", "Industry", False),
    ("rating", "Rating", False),
    ("opportunity_amount", "Deal value", False),
    ("currency", "Currency", False),
    ("probability", "Win probability (%)", False),
    ("close_date", "Expected close date", False),
    ("address_line", "Address", False),
    ("city", "City", False),
    ("state", "State", False),
    ("postcode", "Postal code", False),
    ("country", "Country", False),
    ("last_contacted", "Last contacted", False),
    ("next_follow_up", "Next follow-up", False),
    ("description", "Notes", False),
    ("company_name", "Company", False),
    ("assigned_emails", "Assigned to (emails)", False),
    ("team_names", "Teams", False),
    ("tags", "Tags", False),
    (FULL_NAME, "Full name (split into first and last)", False),
)

# Headers that guess each field; every field also matches its own name, ranked
# below its listed names (see `guess_mapping`). The person fields a lead shares
# with a contact reuse the contacts table, which covers the Google Contacts,
# Outlook and HubSpot exports. A bare "Title" stays this lead's own title (the
# template's header), so it is never listed under `salutation`. HubSpot's
# "Contact owner" holds a person's name, not an email, so nothing here guesses
# assigned_emails.
# Keep "title" first so a bare "Title" header stays this lead's own title.
LEAD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "title": ("lead title", "subject"),
    "salutation": ("salutation", "prefix", "honorific"),
    "first_name": CONTACT_SYNONYMS["first_name"],
    "last_name": CONTACT_SYNONYMS["last_name"],
    "email": CONTACT_SYNONYMS["email"],
    "phone": CONTACT_SYNONYMS["phone"],
    "job_title": CONTACT_SYNONYMS["title"],
    "website": ("website url", "web page", "website 1 - value"),
    "linkedin_url": CONTACT_SYNONYMS["linkedin_url"],
    "status": ("lead status",),
    "source": ("lead source", "source"),
    "industry": (),
    "rating": (),
    "opportunity_amount": (),
    "currency": (),
    "probability": (),
    "close_date": (),
    "address_line": CONTACT_SYNONYMS["address_line"],
    "city": CONTACT_SYNONYMS["city"],
    "state": CONTACT_SYNONYMS["state"],
    "postcode": CONTACT_SYNONYMS["postcode"],
    "country": CONTACT_SYNONYMS["country"],
    "last_contacted": (),
    "next_follow_up": (),
    "description": CONTACT_SYNONYMS["description"],
    "company_name": CONTACT_SYNONYMS["organization"],
    "assigned_emails": (),
    "team_names": CONTACT_SYNONYMS["team_names"],
    "tags": CONTACT_SYNONYMS["tags"],
    # In everyday lead sheets, "Lead Name" holds the person.
    FULL_NAME: (*CONTACT_SYNONYMS[FULL_NAME], "lead name"),
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

    refusal = _custom_field_refusal(org)
    if refusal:
        return _header_error(refusal)

    parsed: list[tuple[int, dict[str, str]]] = []
    errors: list[RowError] = []
    for idx, raw_row in enumerate(data_rows, start=1):
        if not any((cell or "").strip() for cell in raw_row):
            continue
        if not legacy and any((cell or "").strip() for cell in raw_row[len(headers) :]):
            errors.append(
                RowError(
                    idx,
                    "",
                    "Row has more cells than the header; quote values that contain commas",
                )
            )
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

    profiles, teams, archived = _reference_maps(parsed, org)
    valid: list[ValidatedRow] = []
    seen_emails: dict[str, int] = {}

    for idx, record in parsed:
        row_errors, validated = _validate_row(
            idx, record, request, profiles, teams, seen_emails, legacy, archived
        )
        if row_errors:
            errors.extend(row_errors)
            continue
        valid.append(validated)
        email = validated.serializer.validated_data.get("email")
        if email:
            seen_emails[email.lower()] = idx

    errors.sort(key=lambda e: e.row)
    return ImportResult(valid=valid, errors=errors)


def _custom_field_refusal(org) -> str | None:
    """The message refusing the whole file, or None when the org allows it.

    The create API refuses a lead missing a required custom field, and a file
    has no way to supply one, so an import would be a way around it.
    """
    _, cf_errors = validate_custom_fields_payload("Lead", {}, org)
    if not cf_errors:
        return None
    return (
        "Your organisation requires custom field(s) on every lead that CSV "
        f"import cannot set: {', '.join(sorted(cf_errors))}"
    )


def _reference_maps(parsed, org) -> tuple[dict[str, str], ...]:
    """One query per reference type, scoped to `org`. Profiles and teams are
    keyed lowercase; archived tags by slug, see `Tags.archived_errors`."""
    emails: set[str] = set()
    team_names: set[str] = set()
    tag_names: set[str] = set()
    for _idx, record in parsed:
        emails.update(e.lower() for e in _split_multi(record.get("assigned_emails")))
        team_names.update(t.lower() for t in _split_multi(record.get("team_names")))
        tag_names.update(_split_multi(record.get("tags")))

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

    return profiles, teams, Tags.archived_errors(org, tag_names)


_NEEDS_NAME = "A lead needs a first or last name"
_EMAIL_EXISTS = "A lead with this email already exists"


def _source_lookup(request) -> dict[str, str]:
    """The org's active sources by lowercase key and label, to the key. Read
    once per import (kept on the request), not once per row."""
    lookup = getattr(request, "_lead_source_lookup", None)
    if lookup is None:
        lookup = {}
        for source in org_sources(request.profile.org, create=False):
            if source.is_active:
                lookup.setdefault(source.label.lower(), source.key)
                lookup[source.key.lower()] = source.key
        request._lead_source_lookup = lookup
    return lookup


def _validate_row(idx, record, request, profiles, teams, seen_emails, legacy, archived):
    errors: list[RowError] = []

    if legacy:
        if not any(record.get(h) for h in ("first_name", "last_name", "title")):
            errors.append(
                RowError(
                    idx, "first_name", "A lead needs a first name, last name or title"
                )
            )
    elif not record.get("first_name") and not record.get("last_name"):
        errors.append(RowError(idx, "first_name", _NEEDS_NAME))

    # A blank cell means "not supplied", so the model default applies, the
    # same as leaving the field off a create request.
    data = {h: v for h, v in record.items() if h in FIELD_HEADERS and v}
    for name, choices in CHOICE_MAPS.items():
        if name in data:
            data[name] = choices.get(data[name].lower(), data[name])

    if "source" in data:
        data["source"] = _source_lookup(request).get(
            data["source"].strip().lower(), data["source"]
        )

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
        elif archived_error := archived.get(Tags.slug_for(name)):
            errors.append(RowError(idx, "tags", archived_error))

    if errors:
        return errors, None
    return [], ValidatedRow(
        row=idx,
        serializer=serializer,
        assigned_ids=assigned_ids,
        team_ids=team_ids,
        tag_names=tag_names,
    )


_CONCURRENT_CONFLICT = (
    "A lead was created at the same time that conflicts with this "
    "import (likely a duplicate email). Run the preview again."
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
        return {"error": True, "message": _CONCURRENT_CONFLICT, "created": 0}


@quiet()
@suppress_rules()
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
        tag, _ = Tags.objects.get_or_create(slug=slug, org=org, defaults={"name": name})
        cache[slug] = tag
    return cache[slug]


# Column-matching mode


def preview_mapped(table: Table, mapping: list[str | None], request) -> MappedPreview:
    """Validate a mapped table against the caller's org without writing.

    A row with no name, or whose email is already a lead (in this org or
    earlier in the file, case-insensitively), is skipped with its original
    cells. A "converted" status is dropped before validation. When the row
    then fails validation, an email that is already a lead by the create
    serializer's own match skips it as a duplicate; otherwise the failing
    values are dropped (counted per field) and the row is validated again,
    and a second failure skips it. A row that validates is still skipped as
    a duplicate when its phone, or its name once it has no email or phone
    left, matches an earlier row or a lead the importer may open
    (`_duplicate`).

    Raises SpreadsheetError when the org requires a lead custom field, which
    no file can set.
    """
    org = request.profile.org
    refusal = _custom_field_refusal(org)
    if refusal:
        raise SpreadsheetError(refusal)

    records = mapped_records(table, mapping)
    parsed = [(row, record) for row, record, _cells in records]
    profiles, teams, archived = _reference_maps(parsed, org)
    emails = {r["email"].lower() for _row, r in parsed if r.get("email")}
    existing = set()
    if emails:
        existing = set(
            Lead.objects.filter(org=org)
            .annotate(email_lower=Lower("email"))
            .filter(email_lower__in=emails)
            .values_list("email_lower", flat=True)
        )
    phones, names = _visible_phones_and_names(parsed, request.profile)

    valid: list[ValidatedRow] = []
    skipped: list[SkippedRow] = []
    dropped: Counter = Counter()
    seen_emails: dict[str, int] = {}
    seen_phones: dict[str, int] = {}
    seen_names: dict[tuple, int] = {}
    for row, record, cells in records:
        if not record.get("first_name") and not record.get("last_name"):
            skipped.append(SkippedRow(row, "first_name", _NEEDS_NAME, cells, SKIP_ROW))
            continue
        email = record.get("email", "").lower()
        if email in existing:
            skipped.append(
                SkippedRow(row, "email", _EMAIL_EXISTS, cells, SKIP_DUPLICATE)
            )
            continue
        if email in seen_emails:
            message = f"Duplicate email also used by row {seen_emails[email]}"
            skipped.append(SkippedRow(row, "email", message, cells, SKIP_DUPLICATE))
            continue

        removed: set[str] = set()
        status = record.get("status", "")
        if CHOICE_MAPS["status"].get(status.lower(), status) in IRREVERSIBLE_STATUSES:
            removed.add("status")
        kept = {k: v for k, v in record.items() if k not in removed}
        errors, validated = _validate_row(
            row, kept, request, profiles, teams, seen_emails, False, archived
        )
        if errors and _email_is_taken(errors, kept, org):
            skipped.append(
                SkippedRow(row, "email", _EMAIL_EXISTS, cells, SKIP_DUPLICATE)
            )
            continue
        if errors:
            removed |= {e.field for e in errors} & kept.keys()
            kept = {k: v for k, v in kept.items() if k not in removed}
            errors, validated = _validate_row(
                row, kept, request, profiles, teams, seen_emails, False, archived
            )
        if errors:
            skipped.append(
                SkippedRow(row, errors[0].field, errors[0].message, cells, SKIP_ROW)
            )
            continue
        data = validated.serializer.validated_data
        duplicate = _duplicate(data, seen_phones, seen_names, phones, names)
        if duplicate:
            skipped.append(SkippedRow(row, *duplicate, cells, SKIP_DUPLICATE))
            continue
        dropped.update(removed)
        valid.append(validated)
        email, phone = data.get("email"), data.get("phone")
        if email:
            seen_emails[email.lower()] = row
        if normalized := normalize_phone(phone or ""):
            seen_phones[normalized] = row
        if not email and not phone:
            seen_names[_name_key(data.get("first_name"), data.get("last_name"))] = row

    return MappedPreview(
        columns=describe_columns(table, mapping),
        fields=describe_fields(LEAD_FIELDS),
        valid=valid,
        skipped=skipped,
        dropped=dropped,
    )


_PHONE_EXISTS = "A lead with this phone number already exists in your organization"
_NAME_EXISTS = (
    "A lead with this name already exists; add an email or phone to confirm "
    "it's a different person"
)


def _name_key(first, last) -> tuple[str, str]:
    return ((first or "").lower(), (last or "").lower())


def _visible_phones_and_names(parsed, profile) -> tuple[set[str], set[tuple]]:
    """The normalized phones and name keys of the leads `profile` may open.

    Neither field has a database constraint, so, as in the contacts importer,
    both are matched only among `visible_leads_qs`: across the org, "A lead
    with this phone number already exists" for a number held only by a lead
    the importer cannot open would confirm that lead exists. Email stays
    org-wide, because the per-org index refuses that duplicate anyway.

    One query each per file, and the phone query only when a row has a phone.
    There is no normalized phone column, so phones are normalized here. Every
    visible lead's name is keyed, not only those matching rows that arrive
    with no email or phone, because dropping a bad email or phone (or a name
    half that is too long) leaves a row whose stored values decide the check.
    """
    leads = visible_leads_qs(profile)
    phones: set[str] = set()
    if any(record.get("phone") for _row, record in parsed):
        phones = {
            normalize_phone(phone)
            for phone in leads.exclude(phone__isnull=True)
            .exclude(phone="")
            .values_list("phone", flat=True)
        }
    names = {
        _name_key(first, last)
        for first, last in leads.values_list("first_name", "last_name")
    }
    return phones, names


def _duplicate(data, seen_phones, seen_names, phones, names) -> tuple[str, str] | None:
    """The field and message that skip a valid row as a duplicate, or None.

    `data` is the row's validated data, so a value dropped as unreadable is
    already gone: a row whose bad email and phone were dropped is name-checked
    like one that never had them. The rules are the contacts importer's. A
    phone matches on `normalize_phone`, within the file and against `phones`.
    The first and last name, compared case-insensitively, are checked only
    when the row has neither an email nor a phone, since nothing else tells
    two same-named people apart. A lead may have one name, whose missing half
    compares as blank.
    """
    phone = data.get("phone")
    if normalized := normalize_phone(phone or ""):
        if normalized in seen_phones:
            return (
                "phone",
                "Duplicate phone number also used by row "
                f"{seen_phones[normalized]} in this file",
            )
        if normalized in phones:
            return "phone", _PHONE_EXISTS
    if not data.get("email") and not phone:
        key = _name_key(data.get("first_name"), data.get("last_name"))
        if key in seen_names:
            return (
                "first_name",
                f"Same name as row {seen_names[key]} in this file; add an email "
                "or phone to either row to confirm they're different people",
            )
        if key in names:
            return "first_name", _NAME_EXISTS
    return None


def _email_is_taken(errors, record, org) -> bool:
    """Whether an email error is the create serializer's duplicate check.

    The bulk pre-check compares Python's lower() with the database's LOWER(),
    which can disagree outside ASCII, so this repeats the serializer's own
    `email__iexact` match rather than trusting the pre-check or the message.
    """
    email = record.get("email")
    return bool(
        email
        and any(e.field == "email" for e in errors)
        and Lead.objects.filter(org=org, email__iexact=email).exists()
    )


def commit_mapped(table: Table, mapping: list[str | None], request) -> dict[str, Any]:
    """Create the importable rows of a mapped table in one transaction."""
    preview = preview_mapped(table, mapping, request)
    report = preview.to_dict()
    if not preview.valid:
        return {
            "error": True,
            "message": "Nothing in this file can be imported.",
            "skipped": report["skipped"],
            "created": 0,
        }
    try:
        result = _commit_validated(preview.valid, request.profile)
    except IntegrityError:
        return {"error": True, "message": _CONCURRENT_CONFLICT, "created": 0}
    return {**result, "skipped": report["skipped"], "dropped": report["dropped"]}
