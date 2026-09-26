# Contacts

Routes in `backend/contacts/urls.py`: `backend/contacts/views.py` for CRUD and comments/attachments,
`backend/contacts/import_views.py` and `backend/contacts/services/csv_import.py` for CSV import. See
[Conventions](conventions.md) for pagination, filtering and response-shape rules assumed rather than
repeated here.

## Two links to an account, not one

Before anything else: **a contact can be joined to an account two different ways**, and they are
independent. `Contact.account` is a foreign key the model itself calls "Primary account this contact
belongs to" (`contacts/models.py:71-78`); `Account.contacts` is a separate many-to-many
(`accounts/models.py:63-65`). Nothing in the database keeps them in sync. Setting one does not set
the other by itself. The view layer bridges them on write: `link_primary_account(contact)`
(`contacts/views.py:48-65`) adds the contact to `contact.account.contacts` whenever `account` is set
via `POST /api/contacts/`, `PUT /api/contacts/{id}/` or `PATCH /api/contacts/{id}/`, but clearing the
FK later does **not** remove the M2M membership, on purpose (a person can be "no longer the primary
contact" without having "left the company"; `contacts/views.py:59-62`).

Both links are exposed on every `ContactSerializer` response, deliberately, because reading only one
tells the wrong story depending on the data:

```json
{
  "account": "<uuid or null>",
  "account_detail": {"id": "<uuid>", "name": "Acme Inc"},
  "linked_accounts": [{"id": "<uuid>", "name": "Acme Inc"}]
}
```

`account_detail` resolves the FK (`contacts/serializer.py:28-39`); `linked_accounts` is the M2M
membership (`:41-56`). The account detail page's contact list is built from `Account.contacts` (the
M2M), not the FK, so a contact whose `account` FK is set but was never added to that account's
`contacts` (see [CSV import](#csv-import) below for exactly this case) will not appear there even
though `account_detail` on the contact itself looks correct.

## List contacts

`GET /api/contacts/` (`ContactsListView.get`, `contacts/views.py:199-201`) returns a single
paginated list. Unlike leads and accounts, it is not split into two sections. It still doesn't call
DRF's `get_paginated_response()` though. [Conventions](conventions.md#pagination) names
`GET /api/cases/solutions/` as the only endpoint in this API that does. The view hand-builds `count`
and `results` itself (`contacts/views.py:166-167`), with no `next`/`previous`, alongside a legacy set
of keys kept for backwards compatibility (`:168-171`):

```json
{
  "count": 41,
  "results": [ { "...": "ContactSerializer" } ],
  "per_page": 10,
  "page_number": [1],
  "contacts_count": 41,
  "offset": 10,
  "contact_obj_list": [ { "...": "same rows as results" } ],
  "countries": [["GB", "United Kingdom"]],
  "users": [{"id": "...", "user__email": "..."}]
}
```

`active_count` and `inactive_count` (`contacts/views.py:147-148`) are computed over the full filtered
queryset *before* any `is_active` filter is applied, so a client can show "12 active, 3 inactive" while
also asking for only one of the two via `?is_active=true`/`?is_active=false`
(`:149-150`), the only exact-match boolean filter on this endpoint; every other filter here is a
substring match: `name` (`first_name` or `last_name`), `city`, `phone`, `email`, `search`
(`first_name`, `last_name`, `email` or `phone`), plus `assigned_to`/`tags` (repeatable id lists),
`created_at__gte/lte`, and `cf_<key>` (`contacts/views.py:90-139`). A non-admin caller only sees
contacts assigned to them or created by them (`:83-87`).

## Create a contact

`POST /api/contacts/` (`ContactsListView.post`, `contacts/views.py:218-311`) is validated by
`CreateContactSerializer` (`contacts/serializer.py:102-174`); see [Fields](#fields). On success
(`200`):

```json
{"error": false, "message": "Contact created Successfuly", "id": "<uuid>"}
```

(The message string is spelled exactly that way in the response, `Successfuly`, not `Successfully`;
document your integration tests against the real string, not the expected one.) Two validators worth
knowing: **`email` must be unique per org, case-insensitive**, checked only when supplied
(`validate_email`, `contacts/serializer.py:129-145`); and **`account`, if given, must belong to the
caller's org**; `validate_account` rejects a UUID from any other org's `Account` table with
`"No such account."` rather than silently attaching a cross-tenant record
(`contacts/serializer.py:114-127`). `teams`, `assigned_to` and `tags` are not part of the serializer
either, the view resolves each from the request body, tolerating either a JSON array or a
JSON-encoded string of ids (or of `{"id": "..."}` objects), consistently across all three fields and
across `POST` (`contacts/views.py:247-284`), `PUT` (`:478-518`) and `PATCH` (`:832-876`) alike.
Contacts does not have the per-field, per-verb inconsistency [Leads](leads.md#fields) documents for
the equivalent fields there. `contact_attachment` is accepted as a multipart file.

## Retrieve, update, delete

`GET /api/contacts/{id}/` (`ContactDetailView.get`, `contacts/views.py:565-654`) org-scopes the
lookup and maps a malformed id to `404` the same way accounts does
(`get_object`, `:318-332`). `assert_contact_access` (`:334-361`) is the broadest access rule in this
group of four pages: an admin, the contact's creator, one of its assignees, **or anyone assigned to
an account the contact is linked to by either route** (FK or M2M; `account_ids`, `:363-369`) may
read, update or comment on the contact; anyone else gets `403`. The response nests the record under
`contact_obj` and also returns `address_obj` (the flat address fields, echoed as their own object),
`countries` (`contacts/views.py:608`), `comments`, `attachments`, `assigned_data`, `tasks`,
`users_mention`, `custom_field_definitions`, and three computed, capped lists: `opportunities`
(up to 10, via `Opportunity.contacts`),
`cases` (up to 10, via `Case.contacts`), and `colleagues` (up to 8: other contacts sharing either
account link, matched on the account relationship rather than the free-text `organization` field,
which frequently names a different company). `tasks`, `opportunities`, `cases` and `colleagues`
hold only records the caller may open under each module's own read rule, so a non-admin sees
fewer rows than an admin on the same contact.

`open_deals` summarises every open deal naming the contact that the caller may open, uncapped:
`count`, `amount` (the total when every deal is in one currency, `null` when there are several)
and `by_currency` (`[{currency, count, amount}]`, ordered by code). A deal with no currency counts
in the org's default. Use it instead of totalling `opportunities`, which stops at 10. `opportunity_count` is
the uncapped number of deals naming the contact that the caller may open, open or closed.

`PUT /api/contacts/{id}/` (`:441-544`) and `PATCH /api/contacts/{id}/` (`:793-881`) both call
`link_primary_account` after saving (`:477`, `:830`), so changing `account` through either verb keeps
`Account.contacts` in step the same way create does. Success:
`{"error": false, "message": "Contact Updated Successfully"}`.

`DELETE /api/contacts/{id}/` (`:670-691`) is narrower than read/write access on purpose, no assignee
exception, only an admin or the contact's own creator may delete
(`self.request.profile.user_id != self.object.created_by_id`, `:676-679`).

## CSV import

Two endpoints, both gated to org admins or members with `has_sales_access`
(`_can_import`, `contacts/import_views.py:23-31`). An ordinary member gets `403`:

`POST /api/contacts/import/preview/` (`ContactImportPreviewView`, `import_views.py:64-96`) reads a
`multipart/form-data` upload in a field named `file` (must end in `.csv`, capped at 5 MB, checked
against the actual bytes read rather than the client-supplied `Content-Length`,
`import_views.py:34-61`), validates every row without writing anything, and returns:

```json
{
  "header_error": null,
  "valid": [
    {
      "row": 2, "first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com",
      "phone": "2025551234", "organization": null, "title": null, "department": null,
      "do_not_call": false, "linkedin_url": null, "address_line": null, "city": null,
      "state": null, "postcode": null, "country": null, "description": null,
      "account_id": "<uuid or null>", "assigned_ids": [], "team_ids": [], "tag_names": []
    }
  ],
  "errors": [{"row": 3, "field": "email", "message": "'not-an-email' is not a valid email"}],
  "summary": {"total": 2, "valid": 1, "invalid": 1}
}
```

`POST /api/contacts/import/commit/` (`ContactImportCommitView`, `:99-133`) re-validates the same file
and writes inside one transaction (`commit_rows` / `_commit_validated`,
`contacts/services/csv_import.py:634-714`), if any row fails validation, **nothing is written**
(`:650-657`); a concurrent conflict (e.g. a duplicate email created by another request between preview
and commit) rolls the whole batch back and reports `400` rather than partially importing
(`:659-672`). On success (`200`):

```json
{"error": false, "created": 12, "ids": ["<uuid>", "..."]}
```

The CSV must have a header row with `first_name` and `last_name` at minimum; the full list of
recognized optional headers is `email`, `phone`, `organization`, `title`, `department`,
`do_not_call`, `linkedin_url`, `address_line`, `city`, `state`, `postcode`, `country`,
`description`, `account_name`, `assigned_emails`, `team_names`, `tags`
(`REQUIRED_HEADERS`/`OPTIONAL_HEADERS`, `csv_import.py:46-66`); an unrecognized header or a missing
required one fails the whole file with `header_error` before any row is checked. `assigned_emails`
and `team_names` accept `;`-separated multiple values per cell. Duplicates are rejected per org: email
and phone (normalized) are always hard errors; a matching first+last name is only a hard error when
the row supplies **neither** an email nor a phone to disambiguate it from an existing contact
(`csv_import.py:8-19`, `408-514`). The file is capped at 5,000 data rows (`MAX_ROWS`, `:68`).

**A row with `account_name` sets only the `account` foreign key, not the `Account.contacts`
membership.** `_commit_validated` creates each `Contact` with `account_id=vr.account_id` directly
(`csv_import.py:681-700`) and never calls `link_primary_account`, unlike `POST /api/contacts/` and
the `PUT`/`PATCH` update endpoints, which always do (see [Two links to an account](#two-links-to-an-account-not-one)).
A contact imported this way resolves correctly under `account_detail`, but will not show up on that
account's contact list (`Account.contacts`, the M2M) until something else adds it there. This looks
like an oversight rather than a documented design choice. It is reported here, not fixed, per this
documentation task's scope.

## Fields

`CreateContactSerializer.Meta.fields` (`contacts/serializer.py:147-174`) is what
`POST /api/contacts/`, `PUT /api/contacts/{id}/` and `PATCH /api/contacts/{id}/` accept. Unlike
`Lead.first_name`/`last_name`, `Contact.first_name` and `Contact.last_name` have neither
`blank=True` nor a serializer override (`contacts/models.py:19-20`). **both are genuinely
required**.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `first_name` | string | **required** | |
| `last_name` | string | **required** | |
| `email` | string | optional | Unique per org, case-insensitive, when supplied |
| `phone` | string | optional | |
| `organization` | string | optional | Free text, may name a different company than `account` links to |
| `title` | string | optional | |
| `department` | string | optional | |
| `do_not_call` | boolean | optional | Defaults `false` |
| `linkedin_url` | string | optional | Validated as a URL |
| `address_line`, `city`, `state`, `postcode` | string | optional | |
| `country` | one of `COUNTRIES` | optional | |
| `description` | text | optional | |
| `account` | uuid | optional | Must belong to the caller's org; see [Two links to an account](#two-links-to-an-account-not-one) |
| `is_active` | boolean | optional | Defaults `true` |

Not part of the serializer, but accepted in the same request and resolved by the view (see
[Create a contact](#create-a-contact)): `teams`, `assigned_to`, `tags` (each a list of ids,
org-scoped), and `contact_attachment` (a multipart file).

`GET /api/contacts/` and `GET /api/contacts/{id}/` additionally return, but never accept as input:
`id`, `created_by`, `created_at`, `updated_at`, `org` (nested), `account_detail`, `linked_accounts`
(both described above), `contact_attachment`, and `custom_fields` (validated separately against the
org's `CustomFieldDefinition` rows).
