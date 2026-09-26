# CSV import

## What can be imported

**Leads, contacts and cases (tickets) share one two-phase preview/commit importer design.** Accounts
have none, confirmed by grepping the denied concept, not just one filename, across the whole app:
`grep -rniE "\bupload\b|csv_import|import_views|ImportPreview|ImportCommit" --include="*.py"
backend/accounts/` (excluding migrations/tests) returns nothing. If you need to bulk-create
accounts, use `POST /api/accounts/` directly (see [Accounts](../api/accounts.md)), the
[Python](python-sample.md) / [JavaScript](javascript-sample.md) samples on this site call the
equivalent lead endpoint, but the same obtain-token/list/create pattern applies unchanged.

Leads also keep an older single-step endpoint, `POST /api/leads/upload/`, which is **deprecated**
in favour of the lead preview/commit pair below. It used to queue rows to a Celery task and answer
before anything was written. It now runs the same validator and all-or-nothing write as
`/api/leads/import/commit/`, synchronously, so a `200` means every row was created. It keeps its
old request and response shapes: the file goes under the field name `leads_file`, success is
`{"error": false, "message": "Leads created Successfully"}`, and a refusal is
`{"error": true, "errors": ...}` (`403` with `"Admin access required"`; `400` with the size or
header message, or with the list of row errors).

It still accepts the files 1.9.2 accepted. Headers are matched case-insensitively and trimmed, and
the old names map onto today's fields: `first name` to `first_name`, `last name` to `last_name`,
`address` to `address_line`; `title`, `website`, `email`, `phone`, `city`, `state`, `postcode`,
`country`, `description` and `status` are unchanged. No header is required, a column it does not
recognise is ignored rather than refused, any file name is accepted (the content must still parse
as CSV), and a file that is not UTF-8 is read as ISO-8859-1, as 1.9.2 read every file. Each row
needs a first name, last name or title, and is then validated like any other import row.
`account_name` is accepted and ignored: a lead has no account link, and the 1.9.2 endpoint never
stored that column either.

What changed is how bad rows are handled. 1.9.2 silently skipped any row without a valid `email`,
any row whose `title` was already used by a lead in the org, and any row the database refused, and
never reported them. It also cut over-long values short and stored an unknown `status` or
`country` as it was; those are now row errors too. Every row is now checked, a bad row fails the whole file with a
`400` that names the row and field, and nothing is created until the file is clean. `email` is
optional now, and a repeated title is no longer skipped.

All three importers share the same design: a stateless two-phase flow (`parse_and_validate` then
`commit_rows`, in `backend/leads/csv_import.py`, `backend/contacts/services/csv_import.py` and
`backend/cases/services/csv_import.py`), a 5 MB / 5,000-row cap, UTF-8-only decoding (with or
without a BOM. Anything else returns a `header_error` asking you to re-save the file, rather than
silently producing mojibake that then passes validation), and gating to admins or users with sales
access:

```python
def _can_import(profile) -> bool:
    if profile is None:
        return False
    if getattr(profile, "role", None) == "ADMIN":
        return True
    if getattr(profile, "is_admin", False):
        return True
    return bool(getattr(profile, "has_sales_access", False))
```

Anyone else gets `403 {"error": true, "message": "Permission denied"}` from either endpoint of
any importer. This mass-create surface is deliberately not open to every org member the way a
single `POST` is.

## Leads

`POST /api/leads/import/preview/` and `POST /api/leads/import/commit/`
(`LeadImportPreviewView` / `LeadImportCommitView`, `backend/leads/views/import_views.py`), both
`multipart/form-data` with the file under the field name `file`.

Required headers: `first_name`, `last_name`, and each row needs at least one of the two filled in.
Optional: every other field `POST /api/leads/` accepts except `is_active` (`title`, `salutation`,
`email`, `phone`, `job_title`, `website`, `linkedin_url`, `status`, `source`, `industry`,
`rating`, `opportunity_amount`, `currency`, `probability`, `close_date`, `address_line`, `city`,
`state`, `postcode`, `country`, `last_contacted`, `next_follow_up`, `description`,
`company_name`), plus `assigned_emails`, `team_names` and `tags`. Headers are case-insensitive,
and the 1.9.2 names `first name`, `last name` and `address` are read as `first_name`, `last_name`
and `address_line`. An unknown header, including `org` or `created_by`, fails the whole file with a
`header_error`, as does naming one field twice (`first name` and `first_name`, say).

Each row is validated by `LeadCreateSerializer`, the serializer behind `POST /api/leads/`, so an
imported lead passes exactly the checks a hand-made one does. A blank cell means "not supplied".
Choice columns (`status`, `source`, `industry`, `rating`, `currency`, `country`) accept the stored
value or its label, case-insensitively. A row cannot be imported as `converted`: import it, then
convert it, so the account, contact and opportunity are created. `assigned_emails` and
`team_names` must resolve to an active profile or team in your org; `tags` are auto-created. If
your org has a required lead custom field, the whole file is refused, because a CSV cannot set
one and the create API would refuse the lead.


`POST /api/contacts/import/preview/` and `POST /api/contacts/import/commit/`
(`ContactImportPreviewView` / `ContactImportCommitView`, `backend/contacts/import_views.py`), both
`multipart/form-data` with the file under the field name `file`.

Required headers: `first_name`, `last_name`. Optional: `email`, `phone`, `organization`, `title`,
`department`, `do_not_call`, `linkedin_url`, `address_line`, `city`, `state`, `postcode`,
`country`, `description`, `account_name`, `assigned_emails`, `team_names`, `tags`. Any header not
in that list, including a misspelling, fails the whole file with a `header_error` before any row
is read; there's no partial-header tolerance.

`assigned_emails`, `team_names` and `tags` accept multiple values separated by `;` in one cell.
`account_name`, `assigned_emails` and `team_names` must resolve to an existing account, active
profile or team in your org. An unresolved reference is a row error, not a silent skip or an
auto-create (tags are the one exception: an unrecognized tag name is auto-created at commit).
`country`, if present, must be one of the codes documented on the [Leads](../api/leads.md#fields)
page's `country` field (same `COUNTRIES` list, shared across the codebase).

## Tickets

`POST /api/cases/import/preview/` and `POST /api/cases/import/commit/`
(`CaseImportPreviewView` / `CaseImportCommitView`, `backend/cases/import_views.py`), same
`multipart/form-data` shape.

Required headers: `name`, `status`, `priority`. Optional: `description`, `case_type`,
`account_name`, `contact_emails`, `assigned_emails`, `team_names`, `tags`, `closed_on`.
`status` and `priority` are matched case-insensitively against the same `STATUS_CHOICE` /
`PRIORITY_CHOICE` enums the API uses (`New`, `Assigned`, `Pending`, `Closed`, `Rejected`,
`Duplicate`; `Low`, `Normal`, `High`, `Urgent`). A value outside those is a row error, not a
free-text fallback. `case_type`, if present, must be one of `Question`, `Incident`, `Problem`.
`closed_on` must be `YYYY-MM-DD`. `contact_emails` (plural, `;`-separated) must resolve to
existing contacts in your org. This importer does not auto-create contacts the way inbound email
does (see [Inbound email](inbound-email.md#how-inbound-email-becomes-a-ticket)).

## Matching

Every reference field: `account_name`, `contact_emails`/`assigned_emails`/`team_names`, and each
importer's own duplicate check, is resolved against your org only. All three importers bulk-prefetch
every distinct reference value in the file once (one query per reference type, not one per row), so
a 5,000-row file with several reference columns runs on the order of ten queries during validation,
not tens of thousands, and, just as importantly, the lookups can't reach across tenants: a CSV
that names another org's account or a team that doesn't exist in your org fails to resolve the same
way a typo would, because the prefetch never looks outside `org=`.

Duplicate detection differs by importer, because "duplicate" means something different for a
person than for a ticket:

- **Leads**: `email` is a hard error against both the file and the org's existing leads, mirroring
  the per-org, case-insensitive `unique_lead_email_per_org` constraint. There is no phone or name
  check, the same as creating a lead by hand.
- **Contacts**: `email` is a hard error against both the file (another row with the same address)
  and the org's existing contacts. The database enforces a per-org, case-insensitive unique
  constraint, and the importer mirrors it so the failure surfaces as a row error instead of an
  `IntegrityError` at commit. `phone` is a hard error too, normalized with the same digits-only,
  last-10-characters comparison the rest of the codebase uses (`common.validators.normalize_phone`),
  even though there's no database constraint backing it. A full-name collision (`first_name` +
  `last_name`) is **only** an error when the row has neither an email nor a phone to disambiguate
  it from an existing same-named contact. Two people can legitimately share a name, so the check
  only fires when there's nothing else to tell them apart.
- **Tickets**: `name` is a hard error against both the file and the org's existing case names.
  There is no email/phone-style disambiguation exception, because a ticket name isn't expected to
  double as a person's identity the way a contact's is.

## Response shape

Both endpoints of all three importers return the same shape. `preview` never writes to the database.
It re-runs the identical validation `commit` does and returns:

```json
{
  "header_error": null,
  "valid": [{"row": 2, "first_name": "Ada", "last_name": "Lovelace", "...": "..."}],
  "errors": [{"row": 3, "field": "email", "message": "'not-an-email' is not a valid email"}],
  "summary": {"total": 2, "valid": 1, "invalid": 1}
}
```

`row` is 1-based against the data rows (the header is not row 1, so the first data row is `row: 1`).
`header_error` is non-null instead of `valid`/`errors` for a whole-file problem, a missing
required header, an unrecognized header, non-UTF-8 content, an empty file, or more than 5,000 data
rows, and when it's set, `valid` and `errors` are always empty; there's no partial parse of a file
that fails at the header stage.

`commit` re-parses and re-validates the file from scratch. It does not trust a client-side
"these rows already passed preview" claim, and refuses to write anything if *any* row is invalid:

```json
{"error": false, "created": 42, "ids": ["<uuid>", "..."]}
```

```json
{
  "error": true,
  "message": "Fix the invalid rows before importing",
  "errors": [{"row": 3, "field": "email", "message": "..."}],
  "created": 0
}
```

A `header_error` at commit time returns the same field, `created: 0`, and no `errors` array.

The importers handle one edge case differently: a conflicting row created by someone else
between your preview and your commit call, most plausibly the same email landing twice from two
concurrent imports. The **leads** importer does the same as contacts, below. The **contacts**
importer's `commit_rows` wraps the write in a
`try`/`except IntegrityError` and turns that race into a clean response instead of a `500`:

```json
{
  "error": true,
  "message": "A contact was created concurrently that conflicts with this import (likely a duplicate email). Re-run preview and try again.",
  "created": 0
}
```

The **cases (tickets)** importer's `commit_rows` has no equivalent `try`/`except`, because it
doesn't need one the same way: `Case` has no database-level uniqueness constraint on `name` (unlike
`Contact.email`, which the database itself enforces per org), so the same race there wouldn't raise
an `IntegrityError` at all. It would instead create two same-named cases, since the importer's
"duplicate name" check only ever looked at what existed *at validation time*. This is a narrow
window, you'd need two concurrent commits importing the same ticket name at once, but it's worth
knowing the two importers aren't symmetric here.
