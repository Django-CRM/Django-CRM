# Leads

Routes in `backend/leads/urls.py`, views split across `leads/views/lead_views.py` (list and
detail) and `leads/views/lead_interactions.py` (comment and attachment editing). See
[Conventions](conventions.md) for pagination, filtering and response-shape rules this page assumes
rather than repeats, and [Errors](errors.md) for the 404-vs-403 principle referenced below.

## List leads

`GET /api/leads/` (`LeadListView.get`, `backend/leads/views/lead_views.py:246-248`) returns two
separately paginated sections rather than one list, open leads and closed leads, because the view
excludes converted leads from the queryset entirely and then splits what's left on `status`:

```python
queryset = (
    self.model.objects.filter(org=self.request.profile.org)
    .exclude(status="converted")
    ...
)
...
queryset_open = queryset.exclude(status="closed")
...
queryset_close = queryset.filter(status="closed")
```

(`lead_views.py:90-98,166,187`.) A non-admin caller only sees leads assigned to them or created by
them (`lead_views.py:99-103`); an admin or superuser sees every lead in the org. The response follows
the hand-rolled envelope [Conventions](conventions.md#pagination) describes for this exact endpoint:

```json
{
  "per_page": 10,
  "page_number": [1],
  "open_leads": {"leads_count": 42, "open_leads": [ { "...": "LeadSerializer" } ], "offset": 10},
  "totals": {"count": 42, "unworked_over_a_week": 5},
  "close_leads": {"leads_count": 8, "close_leads": [ { "...": "..." } ], "offset": null},
  "contacts": [{"id": "...", "first_name": "..."}],
  "status": [["assigned", "Assigned"], ["in process", "In Process"], ["converted", "Converted"], ["recycled", "Recycled"], ["closed", "Closed"]],
  "source": [["call", "Call"], ["email", "Email"], ["existing customer", "Existing Customer"], ["partner", "Partner"], ["public relations", "Public Relations"], ["compaign", "Campaign"], ["other", "Other"]],
  "tags": [{"...": "..."}],
  "users": [{"id": "...", "user__email": "..."}],
  "countries": [["GB", "United Kingdom"], ["AF", "Afghanistan"]],
  "industries": [["ADVERTISING", "ADVERTISING"]]
}
```

`page_number` is worth calling out: the view builds it as `(int(self.offset / 10) + 1,)`
(`lead_views.py:178`) (a one-element Python tuple, trailing comma and all) which the JSON renderer
turns into a one-element array like `[1]`, not a bare integer. `totals.unworked_over_a_week` counts
leads in the filtered, org-scoped `open` queryset whose `last_contacted` (or, absent that, creation
date) is more than 7 days old (`UNWORKED_AFTER_DAYS`, `lead_views.py:58,60-86`), not a stored status,
just a computed staleness signal.

**`source` accepts only the values in `LEAD_SOURCE`** (`backend/common/utils.py:59-67`): `call`,
`email`, `existing customer`, `partner`, `public relations`, `compaign`, `other`. There is no
`website` value in this enum: that spelling belongs to a completely different, uppercase vocabulary
(`Opportunity.lead_source`, using `SOURCES`; see [Opportunities](opportunities.md#stages)). Note also
that `compaign` is not a typo in this documentation. It is the literal stored value for "Campaign"
leads. A client that sends `source=campaign` (the correctly-spelled word) gets zero rows back, not an
error, because `?source=` is an exact match (`lead_views.py:115-116`), and a client that tries to
*create* a lead with `source: "campaign"` gets a validation error, because `campaign` is not one of
the choices `Lead.source` accepts (`leads/models.py:75-77`).

Filters beyond `source` and `status`, `name`, `salutation`, `assigned_to`, `tags`, `city`, `email`,
`rating`, `search`, `created_at__gte/lte`, `close_date__gte/lte`, `cf_<key>`, are documented in full,
with exact match-vs-contains semantics for each, in
[Conventions: Filtering and search](conventions.md#filtering-and-search), which uses this exact
endpoint as its example.

## Create a lead

!!! tip "Capturing leads from your own website"
    You do not need this endpoint, or any credential in your site's HTML, to collect leads from a
    public page. Build a form in Settings and embed the snippet it gives you: see
    [Web forms](../integrations/web-forms.md). That route is anonymous, origin-restricted,
    rate-limited and captcha-capable, and it deduplicates repeat submissions by email.
    `POST /api/leads/create-from-site/` is the older web-to-lead endpoint, still supported but
    deprecated, and it requires an authenticated caller.

`POST /api/leads/` (`LeadListView.post`, `lead_views.py:273-409`) accepts `multipart/form-data` (for
an optional `lead_attachment` file, read straight off `request.FILES`, `lead_views.py:331`) or JSON.
The body is validated by `LeadCreateSerializer` (`leads/serializer.py:88-224`); see
[Fields](#fields) below for exactly which keys it accepts. On success (`200`, not `201`. See
[Errors: Status codes](errors.md#status-codes)):

```json
{"error": false, "message": "Lead Created Successfully"}
```

Sending `"status": "converted"` on create runs the lead straight through
`leads.services.convert_lead_to_account` and returns a different shape instead:

```json
{
  "error": false,
  "message": "Lead Converted Successfully",
  "account_id": "<uuid>",
  "contact_id": "<uuid or null>",
  "opportunity_id": "<uuid or null>"
}
```

A validation failure returns `400` with `{"error": true, "errors": {...}}`
(`lead_views.py:406-409`), the shape [Errors](errors.md#validation-errors) documents. Two validators
worth knowing about before you integrate:

- **Email uniqueness is case-insensitive per org**, and only checked when an email is actually
  supplied (`validate_email`, `leads/serializer.py:107-134`):
  `{"error": true, "errors": {"email": ["Another lead in this organisation already uses that email address."]}}`.
- **Email becomes required, but only when `status` is `"converted"`**: `LeadCreateSerializer.__init__`
  flips `self.fields["email"].required = True` exactly in that case, having otherwise made
  `first_name`, `last_name` and `salutation` optional regardless of what the `Lead` model itself
  requires (`leads/serializer.py:100-104`). Every other field on this serializer is optional; see
  [Fields](#fields).

`tags`, `contacts`, `teams` and `assigned_to` are **not** part of `LeadCreateSerializer`. The view
reads each straight off the request body after the serializer succeeds, resolved against
`org=request.profile.org` once parsed. They are **not** handled uniformly, and the gap matters because
this same endpoint accepts `multipart/form-data` (above). See [Fields](#fields) for the exact
per-verb, per-field table and the concrete failure a multipart request hits.

## Retrieve, update, delete

`GET /api/leads/{id}/` (`LeadDetailView.get`, `lead_views.py:568-571`) looks the lead up scoped to the
caller's org (`get_object`, `:416-417`. A lead in another org is a `404`, same as one that never
existed, matching the cross-org rule [Errors](errors.md#not-found-versus-forbidden) states generally).
It then calls `assert_lead_access` (`:419-443`): **an admin or superuser sees any lead; anyone else
must be the lead's creator or one of its assignees, or the request is refused with a `403`**, not a
`404`, even though `LeadListView` would have hidden that same lead from that same user entirely.
[Errors: Not found versus forbidden](errors.md#not-found-versus-forbidden) doesn't name leads at
all, its own text says the within-org choice between `403` and `404` "depends on what the resource
is," naming invoices (`403`) and personal macros (`404`) as its two worked examples, so treat this
paragraph, not that page, as the source for what leads actually does: there is no comment or test in
`leads/` recording this as a deliberate disclosure decision the way there is for invoices or macros;
it's simply the behavior as written. The response nests the record under `lead_obj`, matching the
shape [Conventions](conventions.md#response-shape) documents for this exact endpoint, alongside
`attachments`, `comments`, `users_mention`, `assigned_data`, `users`, `users_excluding_team`, `source`,
`status`, `teams`, `countries` and `custom_field_definitions` (`lead_views.py:513-540`).

`PUT /api/leads/{id}/` (`:674-823`) and `PATCH /api/leads/{id}/` (`:847-1064`) both enforce the same
creator-or-assignee-or-admin rule before touching anything, and both can convert the lead when
`status` (or, for `PATCH` only, `is_converted`) is set. `leads/workflow.py` is explicit that this
guard covers less than "any already-closed-off status": it deliberately separates two ideas that used
to be conflated under one name (`leads/workflow.py:11-30`). **`IRREVERSIBLE_STATUSES` is `{"converted"}`
alone** (`:34`), converting an already-`converted` lead a second time is refused with `400` rather
than silently creating a second `Opportunity` against the same `Account`. `CLOSING_STATUSES` is the
separate, deliberately-*reversible* set `{"closed"}` (`:37`; "reopening a closed lead is ordinary
work," per the module docstring at `:24`), and `recycled` is in neither set. **Converting a `closed`
or `recycled` lead succeeds** and
runs the full conversion, same as converting an `assigned` or `in process` one. There is no guard
against it. `PATCH`'s conversion branch returns before `LeadCreateSerializer` (and its
`validate_status`, which enforces the same `IRREVERSIBLE_STATUSES`-only rule) ever runs, so it repeats
the check inline instead (`lead_views.py:875-894`). Both verbs re-attach `tags`/`contacts`/`teams`/
`assigned_to` from the request body when present, but, like `POST`, not identically across the four
fields; see [Fields](#fields) for the exact per-verb table. Success returns
`{"error": false, "message": "Lead updated Successfully"}` (or the conversion shape above); failure
returns `{"error": true, "errors": {...}}`.

`DELETE /api/leads/{id}/` (`:1080-1095`) allows an admin, a superuser, or the lead's own creator
(`request.profile.user == self.object.created_by`), anyone else gets `403`, and additionally
requires the lead's org to match the caller's, even though `get_object` already guarantees that by
construction.

## Comments and attachments

Adding a comment or an attachment to a lead is not a separate endpoint: it's the same
`POST /api/leads/{id}/` route the create endpoint uses, just with an existing `pk`
(`LeadDetailView.post`, `lead_views.py:589-651`). The caller must be an admin/superuser, the lead's
creator, or one of its assignees (`:599-610`). Send `comment` (text) and/or `lead_attachment` (a
multipart file), either or both, and the response echoes the same `lead_obj`/`attachments`/`comments`
shape as `GET`.

Editing or removing an *existing* comment goes through a dedicated route:
`PUT` / `PATCH` / `DELETE /api/leads/comment/{id}/` (`LeadCommentView`,
`backend/leads/views/lead_interactions.py:60-183`), restricted to an admin, a superuser, or the
comment's own author (`request.profile == obj.commented_by`; `commented_by` is a `Profile` foreign
key, so this comparison is correct, unlike some `created_by` comparisons elsewhere in this codebase.
See [Architecture: Permissions and roles](../architecture/permissions-and-roles.md#object-level-checks)).

`DELETE /api/leads/attachment/{id}/` (`LeadAttachmentView.delete`, `lead_interactions.py:203-221`) has
a real gap, documented in
[Architecture: Permissions and roles](../architecture/permissions-and-roles.md#object-level-checks):
the lookup is `self.model.objects.get(pk=pk)` with **no `org=` filter at all**
(`lead_interactions.py:204`), so the id space for attachment deletion is not scoped to the caller's
org by the ORM query, only PostgreSQL RLS, if correctly configured for the deployment, stands between
this endpoint and a cross-tenant delete. The ownership check on the same endpoint
(`request.profile.user == self.object.created_by`, `:208`) is correct: it compares a `User` to a
`User`, so once an attachment is found, only its uploader, an admin, or a superuser can delete it;
the defect is specifically the missing org scope in the lookup, not the permission check.

## Fields

`LeadCreateSerializer.Meta.fields` (`leads/serializer.py:178-213`) is what `POST /api/leads/`,
`PUT /api/leads/{id}/` and `PATCH /api/leads/{id}/` accept. Every field the `Lead` model itself
declares is optional (`blank=True, null=True`, or has a default) except `first_name` and `last_name`,
which have `null=True` but not `blank=True` at the model level and would default to required under
DRF's normal derivation: the serializer's `__init__` explicitly overrides both to `required=False`
(`leads/serializer.py:102-103`, `leads/models.py:49-50`). The practical result: **a lead can be
created with no name at all**, as long as `status` isn't `"converted"` (which requires `email`
instead. See [Create a lead](#create-a-lead)).

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `title` | string | optional | Free text, e.g. "Enterprise Deal" |
| `salutation` | string | optional | |
| `first_name` | string | optional | Forced optional by the serializer; see above |
| `last_name` | string | optional | Forced optional by the serializer; see above |
| `email` | string | **required only when `status: "converted"`** | Must be unique per org, case-insensitive |
| `phone` | string | optional | |
| `job_title` | string | optional | |
| `website` | string | optional | Free text, not validated as a URL |
| `linkedin_url` | string | optional | Validated as a URL |
| `status` | one of `LEAD_STATUS` | optional | `assigned`, `in process`, `converted`, `recycled`, `closed` |
| `source` | one of `LEAD_SOURCE` | optional | `call`, `email`, `existing customer`, `partner`, `public relations`, `compaign`, `other`, no `website` |
| `industry` | one of `INDCHOICES` | optional | e.g. `SOFTWARE` |
| `rating` | `HOT` \| `WARM` \| `COLD` | optional | |
| `opportunity_amount` | decimal | optional | Non-negative (DB constraint) |
| `currency` | one of `CURRENCY_CODES` | optional | Defaults from the org's `default_currency` if omitted and `opportunity_amount` is set |
| `probability` | integer, 0-100 | optional | |
| `close_date` | date | optional | |
| `address_line`, `city`, `state`, `postcode` | string | optional | |
| `country` | one of `COUNTRIES` | optional | |
| `last_contacted`, `next_follow_up` | date | optional | |
| `description` | text | optional | |
| `company_name` | string | optional | |
| `is_active` | boolean | optional | Defaults `true` |

Not part of the serializer, but accepted in the same request body and resolved by the view on
`POST`/`PUT`/`PATCH` (each a list of ids, org-scoped): `tags`, `contacts`, `teams`, `assigned_to`; plus
`lead_attachment` (a multipart file). These four id-list fields are **not parsed the same way on every
verb**, and the difference is a real trap for a client that also uploads a file (multipart fields
arrive as strings, never as JSON arrays):

| Field | `POST /api/leads/` | `PUT /api/leads/{id}/` | `PATCH /api/leads/{id}/` |
| --- | --- | --- | --- |
| `tags` | plain `id__in=data.get("tags")`, no string handling (`lead_views.py:318-323`) | same gap (`:715-721`) | `json.loads`'d if a string, `{"id": …}` unwrapped (`:981-995`) |
| `contacts` | same gap (`:325-329`) | `json.loads`'d if a string (`:732-745`) | `json.loads`'d if a string (`:997-1011`) |
| `teams` | `json.loads`'d if a string (`:340-350`) | same (`:747-758`) | same (`:1013-1027`) |
| `assigned_to` | `json.loads`'d if a string (`:352-364`) | same (`:760-773`) | same (`:1029-1043`) |

Sending `tags` or `contacts` as a JSON-encoded string in the same request as a `multipart/form-data`
`lead_attachment` upload (on `POST`, or on `PUT` for `tags` specifically) hands Django's `id__in`
lookup a raw string. Django iterates it character by character; every character fails `UUIDField`
parsing, and the request dies as an unhandled `500` rather than a clean `400`, the same failure mode
already documented for a different field at `contacts/views.py:105-108`. Sending those two fields as a
real JSON body (no file) works fine, because DRF parses a JSON request body into an actual list before
the view ever reads it.

`GET /api/leads/{id}/` and `GET /api/leads/` additionally return, but never accept as input: `id`,
`created_by`, `created_at`, `updated_at`, `is_sample` (server-set only. See
`leads/serializer.py:80-85`), `lead_attachment` and `lead_comments` (nested read-only serializations of
this lead's attachments and comments: `leads/serializer.py:64-65`), `stage` and `kanban_order`
(managed by the Kanban endpoints, not this page), and `custom_fields` (validated separately against the
org's `CustomFieldDefinition` rows).
