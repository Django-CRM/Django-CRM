# Accounts

Routes in `backend/accounts/urls.py`, all served by `backend/accounts/views.py`. See
[Conventions](conventions.md) for pagination, filtering and response-shape rules assumed rather than
repeated here, and [Errors](errors.md) for the 404-vs-403 principle.

## List accounts

`GET /api/accounts/` (`AccountsListView.get`, `backend/accounts/views.py:312-314`) splits the result
into active and inactive accounts rather than open/closed; `Account` has no status field, only
`is_active` (`accounts/views.py:237-238`):

```json
{
  "per_page": 10,
  "page_number": [1],
  "active_accounts": {"offset": 10, "open_accounts": [ { "...": "AccountSerializer" } ], "open_accounts_count": 37},
  "closed_accounts": {"offset": null, "close_accounts": [ { "...": "..." } ], "close_accounts_count": 4},
  "contacts": [{"id": "...", "first_name": "..."}],
  "teams": [{"...": "..."}],
  "countries": [["GB", "United Kingdom"]],
  "industries": [["SOFTWARE", "SOFTWARE"]],
  "tags": [{"...": "..."}],
  "users": [{"id": "...", "user__email": "..."}],
  "leads": [{"...": "LeadSerializer"}],
  "status": ["active", "inactive"]
}
```

A non-admin caller only sees accounts they created or are assigned to
(`accounts/views.py:195-199`). Filters: `name`, `city`, `industry` (all `icontains`); `tags`,
`assigned_to` (repeatable, `__id__in`); `search` (matches `name` only, unlike leads' `search`, which
ORs across four fields); `created_at__gte`/`created_at__lte`; `cf_<key>` (`accounts/views.py:201-233`).

Every account in the list (and the detail view below) carries a **`rollups`** object;
`won_amount`, `won_count`, `open_pipeline`, `open_deal_count`, `overdue_amount`, `open_tickets`,
`first_won_on`: computed as correlated subqueries over that account's `Opportunity`, `Invoice` and
`Case` rows, never stored (`annotate_rollups` for the counts, `attach_money_rollups` for the money).
Deals and invoices each carry a currency and there are no exchange rates, so the three money fields
are never summed across currencies: each is the plain figure when at most one currency is present and
`null` when several are, and `rollups.by_currency` lists `{currency, won_amount, open_pipeline,
overdue_amount}` per currency either way (a deal with no currency counts in the org's
`default_currency`). `rollups` is `null`,
not zero-filled, on any serialization that skips the annotation (`AccountSerializer.get_rollups`,
`accounts/serializer.py:41-54`), both the list and detail endpoints here apply it, so in practice you
will always see it populated through this page's endpoints.

## Create an account

`POST /api/accounts/` (`AccountsListView.post`, `accounts/views.py:322-400`) is validated by
`AccountCreateSerializer` (`accounts/serializer.py:189-257`); see [Fields](#fields). On success
(`200`):

```json
{"error": false, "message": "Account Created Successfully", "id": "<uuid>"}
```

Two validators worth knowing: **`name` must be unique per org, case-insensitively**
(`validate_name`, `accounts/serializer.py:199-210`); `{"error": true, "errors": {"name": ["Account already exists with this name"]}}`
on a duplicate; and **`annual_revenue` cannot be negative**
(`validate_annual_revenue`, `:212-224`), rejected as `400` rather than reaching the database's own
`account_revenue_non_negative` check constraint as an unhandled `500`.

`contacts`, `tags`, `teams` and `assigned_to` are not part of the serializer. Each is a list of ids
(a JSON-encoded list in a multipart body), the same shape `PUT` and `PATCH` take, and the view
resolves them inside `org=request.profile.org`: an id from another org, or one that does not exist,
is ignored, and a malformed id is a `400` naming the field. `tags` is parsed before the account is
saved; the other three are resolved by `handle_m2m_assignment` in `common/utils.py`. An existing
tag's id attaches that tag and creates none. An `account_attachment` multipart file is accepted the
same way.

## Retrieve, update, delete

`GET /api/accounts/{id}/` (`AccountDetailView.get`, `accounts/views.py:604-704`) scopes the lookup to
the caller's org and treats a malformed id the same as a missing one. Both `404`
(`get_object`, `:407-421`, catching `UUIDField`'s `ValidationError` that `get_object_or_404` alone does
not). `assert_account_access` (`:423-445`) then requires the caller to be an admin, the account's
creator, or one of its assignees, or the request is refused with `403`. The response nests the record
under `account_obj`, and also carries `contacts` (`Account.contacts`, not the `Contact.account`
foreign key. See [Contacts: linked accounts](contacts.md#fields)), `opportunity_list`, `cases`,
`tasks`, `invoices`, `emails`, `comments`, `attachments`, `teams`, `users`, `users_mention`,
`comment_permission`, `leads`, `countries`, `currencies`, `case_types`, `case_priority`,
`case_status`, `custom_field_definitions`, a fixed, two-value `status: ["open", "close"]`
(`accounts/views.py:692`: a static list, unrelated to `Account.is_active`, which is what actually
drives [List accounts](#list-accounts)'s active/inactive split), and two keys worth flagging
explicitly:

```json
{
  "stages": [["PROSPECTING", "Prospecting"], ["QUALIFICATION", "Qualification"], ["CLOSED_WON", "Closed Won"]],
  "sources": [["NONE", "NONE"], ["CALL", "CALL"], ["WEBSITE", "WEBSITE"], ["OTHER", "OTHER"]]
}
```

(`accounts/views.py`.) `stages` is the org's default deal pipeline's stages as `[code, label]`
pairs, and `sources` is `Opportunity`'s `SOURCES` enum, both surfaced here
because the account detail page is where an "add opportunity against this account" form lives, not
because an account has stages or sources of its own. `sources` on this endpoint is the *uppercase*
vocabulary that includes `WEBSITE`; it is not `Lead.source`'s `LEAD_SOURCE`. See
[Opportunities: Stages](opportunities.md#stages) for the distinction, and
[Leads](leads.md#list-leads) for why conflating the two is a real failure mode with this API.

`PUT /api/accounts/{id}/` (`:453-575`) and `PATCH /api/accounts/{id}/` (`:763-871`) both call
`assert_account_access` **before** validating the request body, deliberately, so an unauthorized
caller learns they're forbidden before learning whether their payload was well-formed
(`accounts/views.py:456-459`). Both accept the same `contacts`/`tags`/`teams`/`assigned_to` M2M
handling and `account_attachment` upload as create. Success:
`{"error": false, "message": "Account Updated Successfully"}`.

`DELETE /api/accounts/{id}/` (`:582-597`) allows an admin or the account's own creator
(`request.profile.user_id != self.object.created_by_id` is the rejection condition, `:584-591`).
Anyone else gets `403`.

## Comments and attachments

`POST /api/accounts/{id}/` (`AccountDetailView.post`, `accounts/views.py:712-755`) adds a comment
and/or an `account_attachment` file to an existing account, gated by the same
`assert_account_access` rule as `GET`/`PUT`/`PATCH` (`:719`). The response echoes `account_obj`,
`attachments` and `comments`.

`PUT` / `PATCH` / `DELETE /api/accounts/comment/{id}/` (`AccountCommentView`,
`accounts/views.py:874-968`) is restricted to an admin or the comment's own author
(`request.profile == obj.commented_by`. Correct, since `commented_by` is a `Profile` foreign key).

`DELETE /api/accounts/attachment/{id}/` (`AccountAttachmentView.delete`, `:971-1010`) has its lookup
correctly scoped to the caller's org
(`get_object_or_404(self.model, pk=pk, org=self.request.profile.org)`, `:989-991`, with malformed ids
also mapped to `404`) and its ownership check compares the right types
(`request.profile.user_id == self.object.created_by_id`, `:997`). That makes accounts one of five apps
in this API whose attachment-delete lookup is org-scoped. The same is true of contacts
(`contacts/views.py:1046`), cases (`cases/views.py:1119-1121`), tasks
(`tasks/views/task_views.py:975-977`) and invoices (`invoices/api_views.py:1821`). Only **leads and
opportunity** still have the unscoped version. See
[Leads: Comments and attachments](leads.md#comments-and-attachments) and
[Architecture: Permissions and roles](../architecture/permissions-and-roles.md#object-level-checks).
(One stale comment in `cases/views.py` claims tasks is still broken. It isn't; the fix landed and the
comment didn't get updated. Verified against `tasks/views/task_views.py:975-987` directly rather than
taken on the comment's word.)

## Fields

`AccountCreateSerializer.Meta.fields` (`accounts/serializer.py:226-249`) is what
`POST /api/accounts/`, `PUT /api/accounts/{id}/` and `PATCH /api/accounts/{id}/` accept. Only `name`
is required at the model level (`accounts/models.py:26`, no `blank=True`); every other field on this
serializer has `blank=True, null=True` or a default.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string | **required** | Unique per org, case-insensitive |
| `email` | string | optional | |
| `phone` | string | optional | |
| `website` | string | optional | `URLField`, validated as a URL, unlike `Lead.website` (see [Leads](leads.md#fields)) |
| `industry` | one of `INDCHOICES` | optional | e.g. `SOFTWARE` |
| `number_of_employees` | integer | optional | Non-negative (`PositiveIntegerField`) |
| `annual_revenue` | decimal | optional | Non-negative, checked in the serializer, not just the DB |
| `currency` | one of `CURRENCY_CODES` | optional | Defaults from the org's `default_currency` if omitted and `annual_revenue` is set |
| `address_line`, `city`, `state`, `postcode` | string | optional | |
| `country` | one of `COUNTRIES` | optional | |
| `description` | text | optional | |
| `is_active` | boolean | optional | Defaults `true` |

Not part of the serializer, but accepted in the same request and resolved by the view (see
[Create an account](#create-an-account)): `contacts`, `tags`, `teams`, `assigned_to` (each a list of
ids, org-scoped), and `account_attachment` (a multipart file).

`GET /api/accounts/` and `GET /api/accounts/{id}/` additionally return, but never accept as input:
`id`, `created_by`, `created_at`, `org` (nested), `country_display`, `account_attachment`, `cases`,
`tasks`, `opportunities`, `rollups` (see [List accounts](#list-accounts)), and `custom_fields`
(validated separately against the org's `CustomFieldDefinition` rows).
