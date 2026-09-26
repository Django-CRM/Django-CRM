# Opportunities

Routes in `backend/opportunity/urls.py`, views split across `opportunity/views/opportunity_views.py`
(list and detail) and `opportunity/views/kanban_views.py` (stage board and move). See
[Conventions](conventions.md) for pagination, filtering and response-shape rules assumed rather than
repeated here.

## List opportunities

`GET /api/opportunities/` (`OpportunityListView.get`, `backend/opportunity/views/opportunity_views.py:272-274`)
returns a single paginated list plus org-wide `totals` computed over the whole filtered queryset, not
just the current page (`get_totals`, `:73-110`):

```json
{
  "opportunities_count": 23,
  "totals": {
    "count": 23,
    "amount_sum": "184500.00",
    "weighted_sum": "97250.00",
    "by_currency": [{"currency": "USD", "amount_sum": "184500.00", "weighted_sum": "97250.00"}],
    "stalled_count": 2
  },
  "offset": 10,
  "per_page": 10,
  "page_number": [1],
  "opportunities": [ { "...": "OpportunitySerializer" } ],
  "accounts_list": [ { "...": "AccountSerializer" } ],
  "contacts_list": [ { "...": "ContactSerializer" } ],
  "tags": [ { "...": "..." } ],
  "stage": [["PROSPECTING", "Prospecting"], ["QUALIFICATION", "Qualification"]],
  "lead_source": [["NONE", "NONE"], ["CALL", "CALL"], ["WEBSITE", "WEBSITE"]],
  "currency": [["USD", "USD, Dollar"]]
}
```

`weighted_sum` is the forecast, `SUM(amount * probability / 100)`, as opposed to `amount_sum`,
which is what every open and closed deal in the filtered set is worth at face value.
Deals in different currencies are never added together, since there are no exchange rates: both
figures are the plain amount when the filtered deals use at most one currency and `null` when they
use several, and `by_currency` lists `{currency, amount_sum, weighted_sum}` per currency, ordered
by code. A deal with no currency counts in the org's default currency. `stalled_count` uses the same per-stage aging thresholds as `?rotten=true` below and as
`Opportunity.get_aging_status()`; see [Stages](#stages). A non-admin caller only sees deals they
created or are assigned to (`:119-131`).

Filters: `name`, `search` (both `icontains` against `name` only; `search` here does **not** OR
across multiple fields the way it does on [Leads](leads.md#list-leads)); `account` (exact); `stage`,
`lead_source` (**`__contains`, not exact**. See [Stages](#stages) for why this matters); `tags`,
`assigned_to` (repeatable, id lists); `created_at__gte/lte`, `closed_on__gte/lte`,
`amount__gte/lte`; `cf_<key>`; `pipeline` (a pipeline id, exact); and two boolean flags,
`open=true` (excludes won and lost stages) and `rotten=true` (stalled deals only). See
[Stages](#stages). The `stage` key in the response lists the default pipeline's stages as
`[code, label]` pairs.

## Create an opportunity

`POST /api/opportunities/` (`OpportunityListView.post`, `opportunity_views.py:292-413`) is validated
by `OpportunityCreateSerializer` (`opportunity/serializer.py:199-297`); see [Fields](#fields). On
success (`200`):

```json
{"error": false, "message": "Opportunity Created Successfully", "id": "<uuid>"}
```

**`name` must be unique per org, case-insensitive** (`validate_name`,
`opportunity/serializer.py:213-228`). The serializer also enforces, at the API layer, the two rules
`Opportunity.clean()` states at the model layer. Deals in a won or lost stage need a close date,
and deals in a won stage need an amount (`validate()`; see [Stages](#stages)), because DRF's
`ModelSerializer` never calls `Model.clean()`, so without this the model's own rules would only be
enforced by unit tests against the model, never through the API.
`contacts`, `tags`, `teams` and `assigned_to` are resolved from the request body the same
list-of-ids way as [Leads](leads.md#create-a-lead) and [Accounts](accounts.md#create-an-account)
(`opportunity_views.py:317-376`); `opportunity_attachment` is accepted as a multipart file.

## Retrieve, update, delete

`GET /api/opportunities/{id}/` (`OpportunityDetailView.get`, `opportunity_views.py:661-736`) looks the
deal up scoped to the caller's org. Unlike leads/accounts/contacts, this lookup returns `None` rather
than raising, so a missing or cross-org id comes back as an explicit `404` response
(`"error": true, "errors": "Opportunity not found."`, `:663-667`) instead of Django's
`get_object_or_404`. `assert_deal_access` (`:423-447`) then requires the caller to be an admin, the
deal's creator, or one of its assignees, or the request is refused with `403`. The response nests the
record under `opportunity_obj`, and also returns `comments`, `attachments`, `contacts`,
`users`, `stage` (the deal's own pipeline's stages as `[code, label]` pairs), `lead_source`,
`currency`, `comment_permission`, `users_mention` and `custom_field_definitions`.

`PUT /api/opportunities/{id}/` (`:464-596`) and `PATCH /api/opportunities/{id}/` (`:831-953`) both
enforce `assert_deal_access`, and both stamp `closed_by = request.profile` and persist it with an
explicit `.save()` whenever the request names a stage whose kind is won or lost, so a deal moved
into a closed stage always records who closed it. Both clear
and re-add `contacts`/`tags`/`teams`/`assigned_to` from the request body the same way create does.
Success: `{"error": false, "message": "Opportunity Updated Successfully"}`.

`DELETE /api/opportunities/{id}/` (`:612-637`) allows an admin, a superuser, or the deal's own creator
(`request.profile.user != self.object.created_by`, a `User`-to-`User` comparison, `:624-632`).
Anyone else gets `403`.

## Stages

**Stages belong to a pipeline, and every org configures its own.** `DealPipeline` and `DealStage`
(`opportunity/models.py`) are org-scoped and RLS-protected. A stage has a `code` (the string stored
on `Opportunity.stage`, unique within its pipeline, derived from the label when the stage is created
and never changed afterwards), a `label`, an `order`, a `kind` (`open`, `won` or `lost`) and its
rotting thresholds, `expected_days` and `warning_days` (both empty for a stage that never rots, and
always empty for won and lost stages). Each deal has a `pipeline` and a `stage` code of that pipeline.

Every org has one default pipeline, created by migration `opportunity/0020` for existing orgs and
lazily on first use for new ones, holding the six stages the app has always had:
`PROSPECTING`, `QUALIFICATION`, `PROPOSAL`, `NEGOTIATION` (open), `CLOSED_WON` (won) and
`CLOSED_LOST` (lost). A deal saved without a pipeline lands in it, which is how clients that predate
pipelines keep working.

**Everything that counts wins reads the kind, not the code.** Goals, the dashboard, account and
contact rollups, `?open=true` and the invoice-from-deal check all treat a deal as won when its stage's
kind is `won`, whatever the stage is called (`stage_kind_q`, `opportunity/models.py`). Entering a won
or lost stage requires `closed_on`; entering a won stage also requires `amount`. `probability` is
auto-filled when it is `0` or unset: 100 for a won stage, 0 for a lost one, and a per-code default
for the seeded open stages (`stage_probability`, `opportunity/workflow.py`).

**Filtering by stage is a substring match, not an exact one**: `?stage=` and `?lead_source=` both
compile to `__contains`, unlike `Lead.status`/`Lead.source`, which are exact matches (see
[Leads](leads.md#list-leads)). `?stage=CLOSED` therefore matches both `CLOSED_WON` and
`CLOSED_LOST`; `?stage=closed` (lowercase) matches neither, because `__contains` on a `CharField` is
case-sensitive.

`aging_status` is `yellow` once a deal has sat `warning_days` (or else `expected_days`) whole days in
an open stage, and `red` (stalled, rotting) at `ceil(expected_days * 1.5)` (`aging_thresholds`,
`workflow.py`). `?rotten=true` and the `stalled_count` in `totals` use the same thresholds as
`Opportunity.get_aging_status()`, as queryset cutoffs (`opportunity/stages.py`, `aging_q`).

### Pipeline administration

Reads are open to every member of the org; every write is admin-only (`403` otherwise). An id from
another org is a `404`. All live in `opportunity/views/pipeline_views.py`.

| Endpoint | Method | Body | Answer |
| --- | --- | --- | --- |
| `/api/opportunities/pipelines/` | GET | | `{"pipelines": [pipeline, ...]}`, default first |
| `/api/opportunities/pipelines/` | POST | `{"name"}` | `201`, the pipeline, seeded with the six default stages |
| `/api/opportunities/pipelines/{id}/` | GET, PATCH, DELETE | PATCH `{"name"}` | DELETE is `204`, or `400` for the default pipeline or while any deal is in it |
| `/api/opportunities/pipelines/{id}/stages/` | POST | `{"label", "kind", "expected_days"?, "warning_days"?}` | `201`, the whole pipeline; the stage is appended last |
| `/api/opportunities/pipelines/{id}/stages/reorder/` | POST | `{"stage_ids": [...]}` | the pipeline; `400` unless the list is every stage of the pipeline exactly once |
| `/api/opportunities/stages/{id}/` | PATCH, DELETE | PATCH any of the POST fields | PATCH answers the pipeline; DELETE is `204` |

A pipeline is `{"id", "name", "is_default", "stages": [{"id", "code", "label", "order", "kind",
"expected_days", "warning_days"}]}`. Every pipeline keeps at least one open, one won and one lost
stage: deleting, or changing the kind of, its last stage of a kind is a `400`. So is deleting a stage
or changing its kind while deals are in it, a label another stage of the pipeline already has, and
days outside 1 to 3650.

`GET /api/opportunities/aging-config/` and `PUT` predate pipelines and still work: they read and write
the rotting days of the default pipeline's open stages, keyed by `stage` code.

### Board and move

`GET /api/opportunities/kanban/?pipeline={id}` (`OpportunityKanbanView.get`) returns one column per
stage of that pipeline (the default pipeline when `pipeline` is absent), applying the same org/role
scoping and a subset of the list filters (`search`, `account`, `assigned_to`, `tags`,
`closed_on__gte/lte`). Another org's pipeline id is a `404`.

```json
{
  "mode": "status",
  "pipeline": {"id": "<uuid>", "name": "Sales", "is_default": true},
  "columns": [
    {"id": "PROSPECTING", "name": "Prospecting", "order": 1, "kind": "open", "color": "#3B82F6", "stage_type": "open", "expected_days": 14, "warning_days": null, "is_status_column": true, "wip_limit": null, "item_count": 4, "items": [ { "...": "OpportunityKanbanCardSerializer" } ]}
  ],
  "total_items": 23
}
```

`PATCH /api/opportunities/{id}/move/` (`OpportunityMoveView.patch`) requires `column_id`, a stage
code of the deal's own pipeline (anything else is a `400`; the board never moves a deal between
pipelines, the edit form does). `above_id`/`below_id` or an explicit `kanban_order` place the card
within the column. Access is the same rule as the detail view (admin, creator or assignee). Dragging
into a won or lost stage stamps `closed_by` (and `closed_on` if the deal had none); dragging a closed
deal back to an open stage clears `closed_by`. Dragging into a won stage without an amount is a
`400`.

## Fields

`OpportunityCreateSerializer.Meta.fields` (`opportunity/serializer.py:270-289`) is what
`POST /api/opportunities/`, `PUT /api/opportunities/{id}/` and `PATCH /api/opportunities/{id}/`
accept. Only `name` is required at the model level (`opportunity/models.py:46`, no `blank=True`).

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string | **required** | Unique per org, case-insensitive |
| `account` | uuid | optional | |
| `pipeline` | uuid | optional | One of the org's pipelines. Defaults to the deal's current pipeline, or the org's default on create. Changing it requires `stage` too |
| `stage` | a stage code of the deal's pipeline | optional | Defaults to the pipeline's first open stage on create; see [Stages](#stages) |
| `opportunity_type` | one of `OPPORTUNITY_TYPES` | optional | `NEW_BUSINESS`, `EXISTING_BUSINESS`, `RENEWAL`, `UPSELL`, `CROSS_SELL` |
| `currency` | one of `CURRENCY_CODES` | optional | Defaults from the org's `default_currency` if omitted |
| `amount` | decimal | **required for a won stage** | Otherwise optional; non-negative (DB constraint) |
| `probability` | integer, 0-100 | optional | Auto-filled from `stage` if `0`/unset. See [Stages](#stages) |
| `closed_on` | date | **required for a won or lost stage** | Otherwise optional |
| `lead_source` | one of `SOURCES` | optional | Uppercase, `NONE`, `CALL`, `EMAIL`, `EXISTING CUSTOMER`, `PARTNER`, `PUBLIC RELATIONS`, `CAMPAIGN`, `WEBSITE`, `OTHER`. **Not** `Lead.source`'s `LEAD_SOURCE`. See [Leads](leads.md#list-leads) |
| `description` | text | optional | |
| `is_active` | boolean | optional | Defaults `true` |

Not part of the serializer, but accepted in the same request and resolved by the view (see
[Create an opportunity](#create-an-opportunity)): `contacts`, `tags`, `teams`, `assigned_to` (each a
list of ids, org-scoped), and `opportunity_attachment` (a multipart file).

`GET /api/opportunities/` and `GET /api/opportunities/{id}/` additionally return, but never accept as
input: `id`, `closed_by`, `created_by`, `created_at`, `org` (nested), `created_on_arrow`,
`amount_source` (`MANUAL` or `CALCULATED`, server-derived by `recalculate_amount()` from whether the
deal has line items, `opportunity/models.py:68-73,185-202`; not writable through this serializer),
`stage_label`, `stage_kind`, `stage_changed_at`, `days_in_stage`, `aging_status` (`green`/`yellow`/`red`, from
`Opportunity.get_aging_status()`), `line_items`, `line_items_total`, and `custom_fields` (validated
separately against the org's `CustomFieldDefinition` rows).
