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

**`source` is a key from the organisation's lead sources** (see [Lead sources](#lead-sources)).
Every org starts with 15 (`call`, `email`, `existing customer`, `partner`, `public relations`,
`campaign`, `other`, `web form`, `meta ads`, `google ads`, `linkedin ads`,
`directory or marketplace`, `whatsapp`, `referral`, `walk-in`) and admins add more. The old
misspelt key `compaign` is now `campaign`. The list response's `source` pairs above still carry the
original seven; read `GET /api/leads/sources/` for the org's own list. `?source=` is an exact match
on the key, so a key no lead holds matches nothing rather than erroring.

Filters beyond `source` and `status`, `name`, `salutation`, `assigned_to`, `tags`, `city`, `email`,
`rating`, `search`, `created_at__gte/lte`, `close_date__gte/lte`, `cf_<key>`, are documented in full,
with exact match-vs-contains semantics for each, in
[Conventions: Filtering and search](conventions.md#filtering-and-search), which uses this exact
endpoint as its example. `?unanswered=true` keeps the leads waiting for an answer, longest wait
first; see [Unanswered leads](#unanswered-leads).

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
{"error": false, "message": "Lead Created Successfully", "id": "<uuid>"}
```

`id` is the new lead's. Sending `"status": "converted"` on create runs the lead straight through
`leads.services.convert_lead_to_account` and returns a different shape instead:

```json
{
  "error": false,
  "message": "Lead Converted Successfully",
  "id": "<uuid>",
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
the check inline instead (`lead_views.py:875-894`). Every converting write (`POST`, `PUT` or `PATCH`)
runs in one transaction with the lead row locked (`_atomic_when_converting`): a failure at any step
leaves no account, contact or deal and the lead unconverted, and a second conversion arriving at
the same moment waits for the first, then reads the lead as converted and is refused. The emails to
assignees are sent only once it commits. Both verbs re-attach `tags`/`contacts`/`teams`/
`assigned_to` from the request body when present, but, like `POST`, not identically across the four
fields; see [Fields](#fields) for the exact per-verb table. Success returns
`{"error": false, "message": "Lead updated Successfully"}` (or the conversion shape above); failure
returns `{"error": true, "errors": {...}}`.

The detail response also carries `can_delete`: whether this caller's `DELETE` would be accepted, so a
client offers the action only then.

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

## Duplicates and merging

`POST /api/leads/duplicates/` takes the fields a create form has typed so far as a JSON body
(`email`, `phone`, `first_name`, `last_name`, `company_name`) and answers `{"duplicates": [...]}`: at most 10 active leads, newest first.
`GET /api/leads/{id}/duplicates/` answers the same list for a saved lead, leaving the lead itself out,
plus `can_delete` for that lead. Each hit carries only `id`, `name`, `email`, `phone`, `matched_on`
(the rules it matched) and `can_delete` (whether the caller could merge it away), never the record.
Both search only the leads the caller may open, so a hidden one is neither listed nor counted, and
a hidden or missing `{id}` is the same `404`. A value longer than the model allows is a `400`. The
create-form check is a `POST` that writes nothing, so that an email address and a phone number never
land in a URL and the access logs that record it. From django-crm 1.13.0 a token needs only the
module's `read` scope (`leads:read`) for it, because it writes nothing; before that it needed
`leads:write`. It is throttled at 120 requests a minute per user, and the
clients debounce well under that.

Matching: email, case-insensitive; first and last name together; the company name exactly, but only when no person name is given, because two people at one company are two leads. Converted leads are never matched. Phone numbers match on their last ten digits (or every digit of a shorter number,
seven at least), whatever the separators.

`POST /api/leads/{id}/merge/` with `{"merge_id": "<uuid>"}` merges the lead named in the body into the
one in the URL. The caller must be able to open both (either one hidden or missing is the same `404`),
must be allowed to edit the one kept (for leads, anyone who can open it), and must hold the delete rule on the one merged away
(admin, superuser or its creator), else `403`. Merging a lead into itself, or a missing or
malformed `merge_id`, is a `400`. A converted lead, on either side, is a `400`: it already became an account, a contact and a deal. Both rows are locked for the merge, which runs in one
transaction:

- Every link to the merged lead moves to the kept one: tasks, web form submissions and linked contacts, comments, attachments and
  activity. A link the kept lead already has is not doubled.
- The kept lead's values win. Its blank fields, including custom fields key by key, take the other's
  values; owners (`assigned_to`, `teams`) are taken only when the kept lead has none; tags and linked contacts
  are the union of both. The kept lead keeps its own status, pipeline stage and board position.
- The merged lead is then deleted, which sends the usual `lead.deleted` webhook, and a
  `RECORD_MERGED` row naming both ids and both names is written to the audit log. There is no undo. From
  django-crm 1.13.0 that webhook's `assigned_to` lists the owners the merged lead had before the
  merge, even when they moved to the kept one; before, it was built after the move and could list
  nobody.

The response is `{"error": false, "message": "...", "id": "<kept id>"}`.

## Unanswered leads

`GET /api/leads/?unanswered=true` keeps only the leads waiting for an answer, ordered by
`waiting_since` (longest wait first, then by id), and combines with every other filter, such as
`assigned_to` or `open`. The CSV export (`GET /api/leads/export/?unanswered=true`) and saved views
take it too, so an exported or saved Unanswered list holds the same leads in the same order. Any
value but `true` leaves the list unfiltered. The rule lives in `backend/leads/response.py`.

With `arrival_at` the lead's `last_enquiry_at`, or its `created_at` when it has none, a lead is
unanswered when all three hold:

- its status is `assigned`, `in process` or `recycled` (never `converted` or `closed`);
- `last_outbound_at` is null or earlier than `arrival_at`;
- `arrival_at` is at or after the org's `lead_response_since`, the moment the org started measuring
  (stamped when it upgraded to this release, and at creation for a newer org), so years of old open
  leads do not flood the list.

`waiting_since` is null unless the lead is unanswered. Then it is the lead's `created_at` when
nothing outbound was ever logged on it and it was created at or after `lead_response_since`, and
`arrival_at` otherwise. A new lead that enquires twice waits from its creation; a lead that was
answered and then enquired again waits from that enquiry; and an old lead from before the stamp
that enquires again waits from the new enquiry, not from the day it was created.
`last_outbound_at`, `first_response_at` and `first_response_by` are kept from the lead's logged
interactions ([Interactions](interactions.md#a-leads-response-fields)).

`GET /api/dashboard/` and `GET /api/dashboard/today/` both carry the count:

```json
"unanswered_leads": {"count": 3, "oldest_since": "2026-10-24T09:46:00Z"}
```

over the leads the caller can see (an admin's whole org; a member's own and assigned leads), where
`oldest_since` is the earliest `waiting_since`, or null when nothing is waiting. While the Leads
module is off it is `{"count": 0, "oldest_since": null}`.

## Response times

`GET /api/leads/response-times/?days=30` answers how fast each person gives a lead its first
response:

```json
{
  "since": "2026-09-25",
  "rows": [
    {
      "profile": "8d6c...",
      "name": "Maria Lopez",
      "answered": 12,
      "median_seconds": 340,
      "within_5_minutes": 50,
      "within_1_hour": 83,
      "unanswered_now": 2
    }
  ]
}
```

- **Who gets which rows.** An org admin gets a row for every active member, themselves included;
  anyone else gets exactly one row, their own. Comparing colleagues is an org aggregate.
- **`days`** is a whole number from 7 to 90, default 30; anything else is a `400` naming `days`.
  The window is the last `days` days of the org's calendar, today included. `since` is the first
  of them, and the window opens at the org's midnight on that date.
- **`answered`** counts the leads the person responded to first (`first_response_by`) with
  `first_response_at` inside the window. A lead created before the org's `lead_response_since` is
  left out: its response time would count the years before anything was logged.
- A lead's response time is `first_response_at` minus `created_at`, on the clock, so nights and
  weekends count. **`median_seconds`** is the median over the row's leads (the middle one, or the
  mean of the middle two) in whole seconds rounded down, and null when there are none.
- **`within_5_minutes`** and **`within_1_hour`** are the percentage of those leads answered in at
  most five minutes and at most an hour, rounded down so that 100 means every one; 0 when there
  are none.
- **`unanswered_now`** counts the person's assigned leads that are unanswered right now, whatever
  the window.
- **`name`** is the member's name, or their email when the name is empty. Rows are sorted by it.

Like every `/api/leads/` route it is refused while the Leads module is off
([Modules](modules.md)).

## Outcomes

New in 1.41.0. `GET /api/leads/outcomes/?days=30` answers how many leads each person wins and loses,
and how long winning takes:

```json
{
  "since": "2026-09-29",
  "measured_since": "2026-10-28",
  "rows": [
    {
      "profile": "8d6c...",
      "name": "Maria Lopez",
      "won": 9,
      "lost": 3,
      "win_rate": 75,
      "median_seconds_to_win": 432000,
      "new_leads": 20
    }
  ],
  "totals": {
    "won": 21,
    "lost": 8,
    "win_rate": 72,
    "median_seconds_to_win": 388800,
    "new_leads": 64
  }
}
```

- **Won and lost.** A lead is won when its status is `converted` or its stage's `stage_type` is
  `won`, lost when its status is `closed` or its stage's `stage_type` is `lost` (won wins when both
  apply), and open otherwise, `recycled` included. The lead keeps `won_at` and `lost_at`, set in
  `Lead.save()` when a save changes its status or stage and that changes its outcome: the one that
  now applies takes the time and the other is cleared, and a lead that reopens has both cleared. A
  save that changes neither status nor stage stamps nothing, and a lead created already won or lost
  is not stamped, since nobody knows when it closed. The board, the edit form, conversion and
  automation all save through it; a queryset `update()` does not. Changing a stage's `stage_type`,
  or deleting a stage, restamps none of the leads in it: each keeps the dates it has until a save
  changes its own status or stage. A merge keeps the kept lead's dates and never copies the other
  lead's. Neither field is on the lead payload or accepted by any write.
- **`measured_since`** is the org's date of `lead_outcomes_since`, the moment it started stamping
  (when it upgraded to this release, or its creation for a newer org). Nothing earlier was
  recorded, so an empty first week reads as "measuring since", not as nobody winning anything. No
  write accepts it.
- **Who gets which rows.** An org admin gets a row for every active member, themselves included,
  and `totals`; anyone else gets exactly one row, their own, and `totals` is null.
- **`days`** is a whole number from 7 to 90, default 30; anything else is a `400` naming `days`.
  The window is the last `days` days of the org's calendar, today included; `since` is the first of
  them, and the window opens at the org's midnight on that date.
- **`won`** and **`lost`** count the leads assigned to the person now whose `won_at` or `lost_at`
  is inside the window. Credit goes to whoever is assigned now, not whoever was at the time, and a
  lead with two assignees counts for both. **`new_leads`** counts the leads assigned to the person
  now that were created inside the window.
- **`win_rate`** is `won` out of `won + lost` as a whole percent, rounded to the nearest (a half
  goes to the even neighbour, so 12.5 is 12), and null when both are 0.
- **`median_seconds_to_win`** is the median of `won_at` minus `created_at` over the row's leads won
  inside the window (the middle one, or the mean of the middle two) in whole seconds rounded down,
  and null when there are none.
- **`totals`** counts each of the org's leads once, whether or not anyone is assigned to it.
- **`name`** is the member's name, or their email when the name is empty. Rows are sorted by it.

Like every `/api/leads/` route it is refused while the Leads module is off
([Modules](modules.md)).

## Lead alerts

New in 1.39.0; what is sent and when is in [Lead alerts](../features/lead-alerts.md).

**Notifications.** The four alerts are rows in `GET /api/notifications/` with `verb`
`lead.assigned`, `lead.created`, `lead.enquired` or `lead.waiting`, `entity_type` `Lead`,
`entity_id` and `link` (`/leads/<id>`) naming the lead, `entity_name` its name, and `data`
`{"source": "<source label, or empty>"}`. A `lead.assigned` row's `actor` is the member who
assigned, when one did. From 1.42.0, a `lead.enquired` row for a converted lead names the contact
it became instead: `entity_type` `Contact`, `entity_id` and `link` (`/contacts/<id>`) the contact,
`entity_name` the contact's name, and `data.source` still the lead's source.

**Your switches.** `GET /api/profile/` (in `user_obj`) and `GET /api/auth/profile/` carry
`lead_alerts_email` and `lead_alerts_push`, both `true` by default. `PATCH /api/profile/` with
either one sets it on the caller's own profile in the token's org, and nobody else's; anything
but a boolean is a `400`.

**The reminder.** `GET /api/org/settings/` carries `lead_reminder_minutes`: `null` (off), `5`,
`15`, `30` or `60`. An admin sets it with `PATCH /api/org/settings/`; any other value is a `400`
and a member's `PATCH` is a `403`. Changing it from `null` to a value records the moment, and
only leads whose latest enquiry (or creation) is at or after that moment are reminded about. The
moment is not in the API.

**Phones.** The phone app registers and removes its push token:

| Endpoint | Body | Answer |
| --- | --- | --- |
| `POST /api/devices/` | `{"token", "platform": "android" or "ios", "app_version"?}` | `204`. Creates or refreshes the row for this token in the caller's org, owned by the caller (a phone another member signed in on moves to the caller), with `last_seen_at` now. The caller keeps their five rows in the org with the latest `last_seen_at`; older ones are deleted |
| `DELETE /api/devices/` | `{"token"}` | `204`, whether or not the caller had that token in this org; another member's row is left alone |

A token is at most 2,048 characters, each an ASCII letter or digit, `_`, `:` or `-` (FCM's
alphabet; anything else is a `400`), and `app_version` at most 32. Pushes go to a member's five
newest phones, and a row whose `last_seen_at` is older than the refresh token lifetime (14 days)
is deleted unsent. Neither method ever answers with a
token. Both are for a signed-in session only: `/api/devices/` is on the credential deny-list
(`backend/common/scopes.py`), so a personal access token or the org API key gets `403`, because a
registered phone keeps receiving the member's alerts after the token that registered it is
revoked. They are limited to 60 requests a minute per user.

## Lead sources

Where a lead came from is a key from a per-org list. The endpoints are not tied to the Leads
module switch, because contacts and deals read the same list.

| Endpoint | Who | Body | Answer |
| --- | --- | --- | --- |
| `GET /api/leads/sources/` | any member | | `{"sources": [{"id", "key", "label", "is_active", "order"}]}`, by `order`, retired ones included. The defaults are created on first use |
| `POST /api/leads/sources/` | admin | `{"label"}` | `201` with the new source. The key is the label lowercased with spaces collapsed; a label whose key already exists in the org (retired or not) is `400`. At most 64 characters |
| `PATCH /api/leads/sources/{id}/` | admin | `{"label"?, "is_active"?}` | The source. The key never changes, so existing records keep pointing at it. Another org's id is `404` |
| `POST /api/leads/sources/order/` | admin | `{"keys": [...]}` | `{"sources": [...]}` in the new order. Each key at most once and all must be the org's; keys left out follow the listed ones |

There is no delete: retire a source instead. A retired source stays on the records that hold it,
and cannot be chosen for a new one. A non-admin write is `403`.

**Phone builds before 1.13.0** send no `X-BottleCRM-App` header (they identify as Dart's HTTP
client) and cannot show a source they were never shipped. For those requests only, a blank value or
`other` sent over a stored key the old build cannot read back is ignored (the stored key stays).
A lead's readable keys are `call`, `email`, `existing customer`, `partner`, `public relations`
and `other` (it read `campaign` as Other); a deal's are the same plus `campaign` (it read `web form`
as None and sent `NONE`). `compaign` is read as `campaign`, and a deal's uppercase values as their
keys.

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
| `source` | a key of the org's [lead sources](#lead-sources) | optional | An unknown, other-org or retired key is `400` ("Choose one of your lead sources."). The key a lead already holds is always accepted, even once retired, so editing other fields never trips on it |
| `source_detail` | string, at most 100 | optional | The sub-source, e.g. a campaign name |
| `source_ref` | string, at most 255 | optional | A reference from the source, e.g. an ad click id |
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

Read-only on the lead payload: `phone_e164` (the phone normalised for matching), `external_source`,
`external_id` and `last_enquiry_at` (set when an intake delivers the lead), and the response fields:
`first_response_at`, `first_response_by` (a profile id) with `first_response_by_details`
(`{"id", "name"}`, or null), `last_outbound_at`, `waiting_since` and `is_unanswered` (see
[Unanswered leads](#unanswered-leads)). The list and the detail both carry them; a write that sends
them is answered as usual and changes none of them.

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
