"""CSV import for contacts.

Two-phase: `parse_and_validate` reads + validates a CSV without writing to the
DB beyond read-only lookups; `commit_rows` writes inside a transaction. Both
phases re-run validation so the commit endpoint is safe even if called
directly.

Duplicate policy (per org):
  * email: hard error (DB enforces a per-org case-insensitive unique
                constraint; we mirror it in the importer so users see a clean
                row-level message instead of an IntegrityError at commit).
  * phone: hard error. There is no DB constraint, but most callers
                consider phone a unique identifier in practice. We normalize
                using `common.validators.normalize_phone` (digits-only, last
                10) so "+1 (202) 555-1234" matches "2025551234".
  * full name. Hard error ONLY when the row has neither email nor phone to
                disambiguate from an existing same-named contact. Two people
                can legitimately share a name when other identifiers differ.

Phone and full name are matched only against contacts the importer may open
(`visible_contacts_qs`): nothing in the database forces them unique, so a
refusal that named a hidden contact would only confirm it exists. Email is
checked across the org because the constraint forces it.

All reference lookups (account, assignees, teams) are bulk-prefetched once per
call (one SELECT per reference type), scoped to the caller's org so a
malicious CSV cannot reach across tenants. `account_name` resolves only among
the accounts the importer may open, so a hidden account reads as a missing one.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction
from django.db.models.functions import Lower

from accounts.access import visible_accounts_qs
from common.models import Profile, Tags, Teams
from common.utils import COUNTRIES
from common.validators import normalize_phone
from contacts.access import visible_contacts_qs
from contacts.models import Contact
from contacts.services.account_link import link_primary_account

REQUIRED_HEADERS = ("first_name", "last_name")
OPTIONAL_HEADERS = (
    "email",
    "phone",
    "organization",
    "title",
    "department",
    "do_not_call",
    "linkedin_url",
    "address_line",
    "city",
    "state",
    "postcode",
    "country",
    "description",
    "account_name",
    "assigned_emails",
    "team_names",
    "tags",
)
KNOWN_HEADERS = REQUIRED_HEADERS + OPTIONAL_HEADERS

MAX_ROWS = 5000
NAME_MAX_LEN = 255
# Column widths the regexes below do not bound. SQLite ignores them, Postgres
# raises DataError, so an over-long value previewed as valid and 500'd on commit.
EMAIL_MAX_LEN = Contact._meta.get_field("email").max_length
LINKEDIN_URL_MAX_LEN = Contact._meta.get_field("linkedin_url").max_length
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[\d\s\-\(\)\+\.]{7,25}$")
_url_validator = URLValidator(schemes=("http", "https"))
TRUTHY = {"1", "true", "yes", "y", "t"}
FALSY = {"", "0", "false", "no", "n", "f"}
COUNTRY_CODES = {code.upper() for code, _label in COUNTRIES}


@dataclass
class RowError:
    row: int  # 1-based, matching CSV line numbers (header is row 0)
    field: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"row": self.row, "field": self.field, "message": self.message}


@dataclass
class ValidatedRow:
    """A row that passed validation; reference fields resolved to UUID strings."""

    row: int
    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = None
    organization: str | None = None
    title: str | None = None
    department: str | None = None
    do_not_call: bool = False
    linkedin_url: str | None = None
    address_line: str | None = None
    city: str | None = None
    state: str | None = None
    postcode: str | None = None
    country: str | None = None
    description: str | None = None
    account_id: str | None = None
    assigned_ids: list[str] = field(default_factory=list)
    team_ids: list[str] = field(default_factory=list)
    tag_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "email": self.email,
            "phone": self.phone,
            "organization": self.organization,
            "title": self.title,
            "department": self.department,
            "do_not_call": self.do_not_call,
            "linkedin_url": self.linkedin_url,
            "address_line": self.address_line,
            "city": self.city,
            "state": self.state,
            "postcode": self.postcode,
            "country": self.country,
            "description": self.description,
            "account_id": self.account_id,
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
    """Reference values needed to validate + resolve rows in one pass.

    Keys are lowercased; values are canonical UUID strings for the match.
    The three "existing_*" sets exist only for duplicate detection.
    """

    accounts: dict[str, str]
    profiles: dict[str, str]
    teams: dict[str, str]
    existing_emails: set[str]
    existing_phones: set[str]
    existing_full_names: set[str]


def _decode(file_bytes: bytes) -> str | None:
    """Decode CSV bytes as UTF-8 (with or without BOM).

    Return None for non-UTF-8 so the caller can surface a "save as UTF-8"
    header_error rather than producing mojibake that passes validation.
    """
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _normalize_headers(raw_headers: Iterable[str]) -> list[str]:
    return [(h or "").strip().lower() for h in raw_headers]


def _split_multi(raw: str) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(";") if part.strip()]


def _parse_bool(raw: str) -> bool | None:
    if raw is None:
        return False
    val = raw.strip().lower()
    if val in TRUTHY:
        return True
    if val in FALSY:
        return False
    return None  # caller treats None as "invalid input"


def parse_and_validate(file_bytes: bytes, org, profile) -> ImportResult:
    """Parse a CSV byte string and validate every row against the given org.

    All reference and duplicate lookups are scoped to `org` so a malicious CSV
    cannot reach across tenants. `profile` is the importer, whose account
    access bounds `account_name`.
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

    # First pass: parse each row into a dict and skip blank rows.
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
    seen_emails: dict[str, int] = {}  # email.lower() -> row number
    seen_phones: dict[str, int] = {}  # normalized phone -> row number
    # Tracks rows that had NO email and NO phone, the same disambiguation
    # rule used vs the DB. A row with an email/phone is exempt because the
    # email/phone uniqueness checks already prevent silent duplicates.
    seen_full_names: dict[str, int] = {}  # "first|last".lower() -> row number

    for idx, record in parsed:
        row_errors, validated = _validate_and_build(
            idx, record, ref_maps, seen_emails, seen_phones, seen_full_names
        )
        if row_errors:
            errors.extend(row_errors)
            continue
        valid.append(validated)
        if validated.email:
            seen_emails[validated.email.lower()] = idx
        if validated.phone:
            normalized = normalize_phone(validated.phone)
            if normalized:
                seen_phones[normalized] = idx
        if not validated.email and not validated.phone:
            key = f"{validated.first_name.lower()}|{validated.last_name.lower()}"
            seen_full_names[key] = idx

    return ImportResult(valid=valid, errors=errors)


def _build_ref_maps(parsed: list[tuple[int, dict[str, str]]], org, profile) -> _RefMaps:
    """Bulk-prefetch every reference value referenced anywhere in the file.

    One query per reference type, scoped to `org`. Keys are lowercased so
    row-level lookups are O(1) and case-insensitive. The three "existing_*"
    sets cover the duplicate-detection policy: email (DB-unique), phone
    (normalized digits), and full_name (only used when row has no email or
    phone for disambiguation).
    """
    account_names: set[str] = set()
    assigned_emails: set[str] = set()
    team_names: set[str] = set()
    candidate_emails: set[str] = set()
    candidate_phones: set[str] = set()
    candidate_full_names: set[str] = set()

    for _idx, record in parsed:
        account_name = record.get("account_name", "")
        if account_name:
            account_names.add(account_name.lower())
        for email in _split_multi(record.get("assigned_emails", "")):
            assigned_emails.add(email.lower())
        for team in _split_multi(record.get("team_names", "")):
            team_names.add(team.lower())
        email = record.get("email", "")
        if email:
            candidate_emails.add(email.lower())
        phone = record.get("phone", "")
        if phone:
            normalized = normalize_phone(phone)
            if normalized:
                candidate_phones.add(normalized)
        first = record.get("first_name", "")
        last = record.get("last_name", "")
        if first and last and not email and not phone:
            candidate_full_names.add(f"{first.lower()}|{last.lower()}")

    # Only accounts the importer may open. Resolving across the whole org told
    # the importer a hidden account existed ("No account named ..." for a name
    # nobody holds, a valid row for a hidden one), and linking a contact to an
    # account hands it to that account's assignees (`has_contact_access`).
    accounts: dict[str, str] = {}
    if account_names:
        for pk, name_lower in (
            visible_accounts_qs(profile, profile.user)
            .annotate(name_lower=Lower("name"))
            .filter(name_lower__in=account_names)
            .values_list("id", "name_lower")
        ):
            accounts.setdefault(name_lower, str(pk))

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

    existing_emails: set[str] = set()
    if candidate_emails:
        existing_emails = set(
            Contact.objects.filter(org=org)
            .annotate(email_lower=Lower("email"))
            .filter(email_lower__in=candidate_emails)
            .values_list("email_lower", flat=True)
        )

    # Phone and full name carry no DB constraint, so unlike email these two
    # checks are a courtesy, and they look only at contacts the importer may
    # open. Across the whole org they were an oracle: "A contact with this
    # phone number already exists" for a number held only by a hidden contact
    # confirmed that contact existed.
    existing_phones: set[str] = set()
    if candidate_phones:
        # No normalized column, so scan the visible contacts that have a phone
        # and normalize in Python. Bounded by org size, not file size, fine for
        # any reasonable tenant.
        for raw_phone in (
            visible_contacts_qs(profile)
            .exclude(phone__isnull=True)
            .exclude(phone="")
            .values_list("phone", flat=True)
        ):
            normalized = normalize_phone(raw_phone)
            if normalized and normalized in candidate_phones:
                existing_phones.add(normalized)

    existing_full_names: set[str] = set()
    if candidate_full_names:
        # Only run this query if any row has a full name but no email and no
        # phone. That's the only case where full-name dedup applies.
        for first, last in (
            visible_contacts_qs(profile)
            .annotate(
                first_lower=Lower("first_name"),
                last_lower=Lower("last_name"),
            )
            .values_list("first_lower", "last_lower")
        ):
            key = f"{first}|{last}"
            if key in candidate_full_names:
                existing_full_names.add(key)

    return _RefMaps(
        accounts=accounts,
        profiles=profiles,
        teams=teams,
        existing_emails=existing_emails,
        existing_phones=existing_phones,
        existing_full_names=existing_full_names,
    )


def _validate_and_build(
    idx: int,
    record: dict,
    refs: _RefMaps,
    seen_emails: dict[str, int],
    seen_phones: dict[str, int],
    seen_full_names: dict[str, int],
) -> tuple[list[RowError], ValidatedRow | None]:
    """Validate a single row and, on success, return the resolved ValidatedRow.

    All reference resolution uses the prefetched maps, no DB calls here.
    """
    errors: list[RowError] = []

    first_name = record.get("first_name", "")
    last_name = record.get("last_name", "")
    if not first_name:
        errors.append(RowError(idx, "first_name", "First name is required"))
    elif len(first_name) > NAME_MAX_LEN:
        errors.append(
            RowError(idx, "first_name", f"First name exceeds {NAME_MAX_LEN} characters")
        )
    if not last_name:
        errors.append(RowError(idx, "last_name", "Last name is required"))
    elif len(last_name) > NAME_MAX_LEN:
        errors.append(
            RowError(idx, "last_name", f"Last name exceeds {NAME_MAX_LEN} characters")
        )

    email = record.get("email", "")
    if email:
        if len(email) > EMAIL_MAX_LEN:
            errors.append(
                RowError(idx, "email", f"Email exceeds {EMAIL_MAX_LEN} characters")
            )
        elif not EMAIL_RE.match(email):
            errors.append(RowError(idx, "email", f"'{email}' is not a valid email"))
        else:
            email_lower = email.lower()
            prior = seen_emails.get(email_lower)
            if prior:
                errors.append(
                    RowError(
                        idx,
                        "email",
                        f"Duplicate email also used by row {prior} in this file",
                    )
                )
            elif email_lower in refs.existing_emails:
                errors.append(
                    RowError(
                        idx,
                        "email",
                        "A contact with this email already exists in your organization",
                    )
                )

    phone = record.get("phone", "")
    normalized_phone: str | None = None
    if phone:
        if not PHONE_RE.match(phone):
            errors.append(
                RowError(
                    idx,
                    "phone",
                    "Phone must be 7-25 characters of digits and separators (+ - ( ) . space)",
                )
            )
        else:
            normalized_phone = normalize_phone(phone)
            if normalized_phone:
                prior = seen_phones.get(normalized_phone)
                if prior:
                    errors.append(
                        RowError(
                            idx,
                            "phone",
                            f"Duplicate phone number also used by row {prior} in this file",
                        )
                    )
                elif normalized_phone in refs.existing_phones:
                    errors.append(
                        RowError(
                            idx,
                            "phone",
                            "A contact with this phone number already exists in your organization",
                        )
                    )

    # Full-name dedup only kicks in when there's nothing else to tell two
    # same-named people apart. If the row has an email or phone, we trust
    # those as the disambiguator (and have already checked them above).
    if first_name and last_name and not email and not phone:
        key = f"{first_name.lower()}|{last_name.lower()}"
        prior = seen_full_names.get(key)
        if prior:
            errors.append(
                RowError(
                    idx,
                    "first_name",
                    f"Same name as row {prior} in this file; add an email or phone to either row to confirm they're different people",
                )
            )
        elif key in refs.existing_full_names:
            errors.append(
                RowError(
                    idx,
                    "first_name",
                    "A contact with this name already exists; add an email or phone to confirm it's a different person",
                )
            )

    linkedin_url = record.get("linkedin_url", "")
    if len(linkedin_url) > LINKEDIN_URL_MAX_LEN:
        errors.append(
            RowError(
                idx,
                "linkedin_url",
                f"Exceeds {LINKEDIN_URL_MAX_LEN} characters",
            )
        )
    elif linkedin_url:
        try:
            _url_validator(linkedin_url)
        except DjangoValidationError:
            errors.append(
                RowError(
                    idx,
                    "linkedin_url",
                    "Must be a valid URL starting with http:// or https://",
                )
            )

    country_raw = record.get("country", "")
    country: str | None = None
    if country_raw:
        country = country_raw.strip().upper()
        if country not in COUNTRY_CODES:
            errors.append(
                RowError(idx, "country", f"'{country_raw}' is not a known country code")
            )
            country = None

    do_not_call_raw = record.get("do_not_call", "")
    do_not_call = _parse_bool(do_not_call_raw)
    if do_not_call is None:
        errors.append(RowError(idx, "do_not_call", "Use yes/no, true/false, or 1/0"))
        do_not_call = False

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

    assigned_ids: list[str] = []
    for assigned_email in _split_multi(record.get("assigned_emails", "")):
        if not EMAIL_RE.match(assigned_email):
            errors.append(
                RowError(
                    idx,
                    "assigned_emails",
                    f"'{assigned_email}' is not a valid email",
                )
            )
            continue
        resolved = refs.profiles.get(assigned_email.lower())
        if resolved is None:
            errors.append(
                RowError(
                    idx,
                    "assigned_emails",
                    f"No active member with email '{assigned_email}'",
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

    for length_field in (
        "organization",
        "title",
        "department",
        "address_line",
        "city",
        "state",
    ):
        val = record.get(length_field, "")
        if val and len(val) > NAME_MAX_LEN:
            errors.append(
                RowError(idx, length_field, f"Exceeds {NAME_MAX_LEN} characters")
            )

    postcode = record.get("postcode", "")
    if postcode and len(postcode) > 64:
        errors.append(RowError(idx, "postcode", "Exceeds 64 characters"))

    tag_names = _split_multi(record.get("tags", ""))
    for tag_name in tag_names:
        name_error = Tags.name_error(tag_name)
        if name_error:
            errors.append(RowError(idx, "tags", f"Tag '{tag_name}': {name_error}"))

    if errors:
        return errors, None

    return [], ValidatedRow(
        row=idx,
        first_name=first_name,
        last_name=last_name,
        email=email or None,
        phone=phone or None,
        organization=record.get("organization") or None,
        title=record.get("title") or None,
        department=record.get("department") or None,
        do_not_call=do_not_call,
        linkedin_url=linkedin_url or None,
        address_line=record.get("address_line") or None,
        city=record.get("city") or None,
        state=record.get("state") or None,
        postcode=postcode or None,
        country=country,
        description=record.get("description") or None,
        account_id=account_id,
        assigned_ids=assigned_ids,
        team_ids=team_ids,
        tag_names=tag_names,
    )


def commit_rows(file_bytes: bytes, org, profile) -> dict[str, Any]:
    """Re-parse the uploaded CSV and create contacts in a single transaction.

    Returning structured counts lets the UI render a per-row outcome strip.
    Any unexpected error rolls back the whole batch. IntegrityError races
    (e.g. a contact with the same email created between preview and commit
    by another request) are caught and reported as a 400-equivalent payload
    so the user sees an actionable message instead of a generic 500.
    """
    result = parse_and_validate(file_bytes, org, profile)
    if result.header_error:
        return {
            "error": True,
            "header_error": result.header_error,
            "created": 0,
        }
    if result.errors:
        # Refuse to write anything if any row failed; users fix the file first.
        return {
            "error": True,
            "message": "Fix the invalid rows before importing",
            "errors": [e.to_dict() for e in result.errors],
            "created": 0,
        }

    try:
        return _commit_validated(result.valid, org, profile)
    except IntegrityError:
        # The atomic block rolled back; surface a friendly message and ask
        # the user to re-preview so the conflict shows up as a row error.
        # The exception text is not returned: on Postgres it names the
        # constraint and echoes the conflicting key.
        return {
            "error": True,
            "message": (
                "A contact was created concurrently that conflicts with this "
                "import (likely a duplicate email). Re-run preview and try again."
            ),
            "created": 0,
        }


@transaction.atomic
def _commit_validated(rows: list[ValidatedRow], org, profile) -> dict[str, Any]:
    created_ids: list[str] = []
    tag_cache: dict[str, Tags] = {}

    for vr in rows:
        contact = Contact.objects.create(
            first_name=vr.first_name,
            last_name=vr.last_name,
            email=vr.email,
            phone=vr.phone,
            organization=vr.organization,
            title=vr.title,
            department=vr.department,
            do_not_call=vr.do_not_call,
            linkedin_url=vr.linkedin_url,
            address_line=vr.address_line,
            city=vr.city,
            state=vr.state,
            postcode=vr.postcode,
            country=vr.country,
            description=vr.description,
            account_id=vr.account_id,
            org=org,
            created_by=profile.user,
        )
        # A Contact joins an Account two ways: the `account` FK ("primary
        # account this contact belongs to") and membership of `Account.contacts`.
        # Only the M2M drives the account page's people list, so setting the FK
        # alone put the person nowhere visible. The three interactive create
        # paths all call this; the importer never did, so every imported contact
        # was invisible on the account it was imported against.
        link_primary_account(contact)
        if vr.assigned_ids:
            contact.assigned_to.set(vr.assigned_ids)
        if vr.team_ids:
            contact.teams.set(vr.team_ids)
        if vr.tag_names:
            tag_objs = [
                _get_or_create_tag(name, org, tag_cache) for name in vr.tag_names
            ]
            contact.tags.set(tag_objs)
        created_ids.append(str(contact.id))

    return {
        "error": False,
        "created": len(created_ids),
        "ids": created_ids,
    }


def _get_or_create_tag(name: str, org, cache: dict[str, Tags]) -> Tags:
    # slug+org is the unique key on Tags; using it for get_or_create collapses
    # the SELECT-then-INSERT race when concurrent imports reference the same tag.
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
