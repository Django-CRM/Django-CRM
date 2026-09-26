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
`amount__gte/lte`; `cf_<key>`; and two boolean flags, `open=true` (excludes closed stages) and
`rotten=true` (stalled deals only). See [Stages](#stages) (`opportunity_views.py:133-192`).

## Create an opportunity

`POST /api/opportunities/` (`OpportunityListView.post`, `opportunity_views.py:292-413`) is validated
by `OpportunityCreateSerializer` (`opportunity/serializer.py:199-297`); see [Fields](#fields). On
success (`200`):

```json
{"error": false, "message": "Opportunity Created Successfully", "id": "<uuid>"}
```

**`name` must be unique per org, case-insensitive** (`validate_name`,
`opportunity/serializer.py:213-228`). The serializer also enforces, at the API layer, the two rules
`Opportunity.clean()` states at the model layer. Closed deals need a close date, and `CLOSED_WON`
deals need an amount (`validate()`, `:242-268`; see [Stages](#stages)), because DRF's
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
`users`, `stage`, `lead_source`, `currency`, `comment_permission`, `users_mention` and
`custom_field_definitions`.

`PUT /api/opportunities/{id}/` (`:464-596`) and `PATCH /api/opportunities/{id}/` (`:831-953`) both
enforce `assert_deal_access`, and both stamp `closed_by = request.profile` and persist it with an
explicit `.save()` whenever the incoming `stage` is `CLOSED_WON` or `CLOSED_LOST`
(`:537-539`, `:942-944`), a deal moved into a closed stage always records who closed it. Both clear
and re-add `contacts`/`tags`/`teams`/`assigned_to` from the request body the same way create does.
Success: `{"error": false, "message": "Opportunity Updated Successfully"}`.

`DELETE /api/opportunities/{id}/` (`:612-637`) allows an admin, a superuser, or the deal's own creator
(`request.profile.user != self.object.created_by`, a `User`-to-`User` comparison, `:624-632`).
Anyone else gets `403`.

## Stages

**`stage` is a fixed, org-wide enum, not a configurable pipeline.** `Opportunity.stage` is a
`CharField` with `choices=STAGES` and `default="PROSPECTING"`
(`opportunity/models.py:54-56`; `STAGES` in `backend/common/utils.py:87-94`):
`PROSPECTING`, `QUALIFICATION`, `PROPOSAL`, `NEGOTIATION`, `CLOSED_WON`, `CLOSED_LOST`. There is no
`Pipeline`/`Stage` model behind it the way there is for the other three record types that have a
Kanban board in this API: `LeadPipeline`/`LeadStage` (`leads/models.py:229-330`),
`CasePipeline`/`CaseStage` (`cases/models.py:490,527`), and `Board`/`BoardColumn` for tasks
(`tasks/models.py:16,83`) are all per-org, admin-configurable. Opportunity's Kanban view says this
about itself in its own module docstring: "Status-based only (Opportunity has no Pipeline/Stage model.
It groups by the flat `stage` CharField)" (`opportunity/views/kanban_views.py:1-6`). Every org gets
the same six stages; there is no endpoint to add, rename or reorder them.

Two consequences of moving a deal between stages, both enforced server-side (see
[Create an opportunity](#create-an-opportunity)): entering `CLOSED_WON` or `CLOSED_LOST` requires
`closed_on`; entering `CLOSED_WON` specifically also requires `amount`
(`CLOSED_STAGES`, `AMOUNT_REQUIRED_STAGES`, `opportunity/workflow.py:17-21`). `probability` is
auto-filled from a fixed per-stage default (`STAGE_PROBABILITIES`, `workflow.py:8-15`) whenever the
value on the record is `0` or unset (`Opportunity.save()`, `opportunity/models.py:262-264`). Sending
your own `probability` still works, this only fills the gap when you don't.

**Filtering by stage is a substring match, not an exact one**: `?stage=` and `?lead_source=` both
compile to `__contains` (`opportunity_views.py:138-143`), unlike `Lead.status`/`Lead.source`, which
are exact matches (see [Leads](leads.md#list-leads)). `?stage=CLOSED` therefore matches both
`CLOSED_WON` and `CLOSED_LOST`; `?stage=closed` (lowercase) matches neither, because `__contains` on a
`CharField` is case-sensitive.

`?rotten=true` on `GET /api/opportunities/` and the `stalled_count` in its `totals` both use the same
definition as `Opportunity.get_aging_status()`: a deal is stalled once it has sat in its current,
non-closed stage for at least `expected_days * 1.5` (`ROTTEN_MULTIPLIER`, `workflow.py:32`), where
`expected_days` comes from the org's `StageAgingConfig` for that stage if one exists, else a built-in
default (`DEFAULT_STAGE_EXPECTED_DAYS`, `workflow.py:24-29`; `stalled_filter`,
`opportunity_views.py:43-66`).

`GET /api/opportunities/kanban/` (`OpportunityKanbanView.get`, `kanban_views.py:39-120`) returns one
column per `STAGES` value, applying the same org/role scoping and a subset of the list filters
(`search`, `account`, `assigned_to`, `tags`, `closed_on__gte/lte`):

```json
{
  "mode": "status",
  "pipeline": null,
  "columns": [
    {"id": "PROSPECTING", "name": "Prospecting", "order": 1, "color": "#3B82F6", "stage_type": "open", "is_status_column": true, "wip_limit": null, "item_count": 4, "items": [ { "...": "OpportunityKanbanCardSerializer" } ]}
  ],
  "total_items": 23
}
```

`"pipeline": null` is not a placeholder for missing data. It is always `null` here, precisely because
there is no pipeline object behind this board.

`PATCH /api/opportunities/{id}/move/` (`OpportunityMoveView.patch`, `kanban_views.py:140-204`)
**requires `stage`**: `OpportunityMoveSerializer.stage` is a `ChoiceField` with no `required=False`
(`opportunity/serializer.py:362-367`, whose own docstring says so explicitly), and the view reads
`data["stage"]` unconditionally (`kanban_views.py:178`), a reorder-only body with no `stage` key gets
a `400`, not a silent no-op reorder. When `stage` is sent, `kanban_order` can optionally come with it
to reorder within the (possibly new) column at the same time (fractional indexing, same convention as
tasks, averaging two neighbors' `kanban_order`, or offsetting by ±1000 with only one neighbor given).
Its own access check
intends to mirror `assert_deal_access` (admin, creator, or assignee) but the creator half is broken:
`is_owner = request.profile == opportunity.created_by` compares a `Profile` to `Opportunity.created_by`,
which is a `User` foreign key. The two are never equal, so a non-admin who created a deal but did not
also assign it to themselves cannot move it and gets `403` (`kanban_views.py:161-168`). This is one of
the two open `created_by` type-mismatch call sites in this codebase's `opportunity` app; see
[Architecture: Permissions and roles](../architecture/permissions-and-roles.md#object-level-checks)
for the other and for why it hasn't been fixed as part of this documentation task.

## Fields

`OpportunityCreateSerializer.Meta.fields` (`opportunity/serializer.py:270-289`) is what
`POST /api/opportunities/`, `PUT /api/opportunities/{id}/` and `PATCH /api/opportunities/{id}/`
accept. Only `name` is required at the model level (`opportunity/models.py:46`, no `blank=True`).

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string | **required** | Unique per org, case-insensitive |
| `account` | uuid | optional | |
| `stage` | one of `STAGES` | optional | Defaults `PROSPECTING`; see [Stages](#stages) |
| `opportunity_type` | one of `OPPORTUNITY_TYPES` | optional | `NEW_BUSINESS`, `EXISTING_BUSINESS`, `RENEWAL`, `UPSELL`, `CROSS_SELL` |
| `currency` | one of `CURRENCY_CODES` | optional | Defaults from the org's `default_currency` if omitted |
| `amount` | decimal | **required if `stage: "CLOSED_WON"`** | Otherwise optional; non-negative (DB constraint) |
| `probability` | integer, 0-100 | optional | Auto-filled from `stage` if `0`/unset. See [Stages](#stages) |
| `closed_on` | date | **required if `stage` is `CLOSED_WON` or `CLOSED_LOST`** | Otherwise optional |
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
`stage_changed_at`, `days_in_stage`, `aging_status` (`green`/`yellow`/`red`, from
`Opportunity.get_aging_status()`), `line_items`, `line_items_total`, and `custom_fields` (validated
separately against the org's `CustomFieldDefinition` rows).
