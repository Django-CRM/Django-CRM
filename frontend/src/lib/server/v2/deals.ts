/**
 * Pipeline: the second v2 module wired to the real API.
 *
 * Same rules as `leads.js`, which has the long version of the reasoning:
 * this file lives under `#lib/server` because SvelteKit refuses to bundle
 * that directory into client code and the access token is an httpOnly
 * cookie; the org is never a parameter, only ever a claim inside the JWT.
 *
 * WHAT CHANGED WHEN THE FIXTURES CAME OFF
 * Two things the mock showed do not exist on `Opportunity` and are gone
 * rather than faked: `last_activity_at` (aging is measured from
 * `stage_changed_at`, which is a different claim), and a contact's
 * `relationship` (Contact has `title` and `department`). The mock's
 * `next_action` is now real as `next_activity` (see `toNextActivity`). The
 * related invoices and tickets rail is also gone. Those are real models, but
 * attaching them to a deal means querying two more modules per page load for
 * a panel nobody asked for; add it back deliberately if it earns the round
 * trips.
 *
 * NUMBERS ARRIVE AS STRINGS
 * DRF renders `DecimalField` as a string, so `amount`, `unit_price` and the
 * line-item totals come back as `"42000.00"`. The pages sum them. `"1" + "2"`
 * is `"12"`, so every one of them is coerced here, at the boundary, rather
 * than in whichever component happens to add them up first.
 */
import { authorText } from '#lib/v2/author.js';
import { error, type Cookies } from '@sveltejs/kit';
import { apiRequest, type FormBody } from '#lib/api-helpers.js';
import { apiStatus } from '#lib/api-errors.js';
import { fieldsForDetail, fieldsForEdit } from '#lib/server/v2/record-custom-fields.js';
import { rethrowModuleOff } from '#lib/server/v2/modules.js';
import { attachmentHref } from '#lib/server/v2/files.js';
import { sumByCurrency } from '#lib/v2/format.js';
import { canMoveCard } from '#lib/v2/board-drag.js';
import type { components } from '#lib/api-schema.js';

type Schemas = components['schemas'];
type CustomFieldValues = Record<string, string | boolean | null>;
/**
 * What the create and edit actions collect: the submitted fields as strings,
 * plus the typed custom values.
 */
export type DealFormValues = {
  [field: string]: string | CustomFieldValues | undefined;
  custom_fields?: CustomFieldValues;
};

/**
 * What `toRow` reads: the fields a list row, a board card and the detail
 * record share, plus the ones only some of them carry.
 */
type DealFields = Pick<
  Schemas['Opportunity'],
  | 'id'
  | 'name'
  | 'account'
  | 'pipeline'
  | 'stage'
  | 'stage_label'
  | 'stage_kind'
  | 'amount'
  | 'currency'
  | 'probability'
  | 'closed_on'
  | 'assigned_to'
  | 'stage_changed_at'
  | 'days_in_stage'
  | 'aging_status'
  | 'created_at'
> &
  Partial<
    Pick<Schemas['Opportunity'], 'opportunity_type' | 'lead_source' | 'description' | 'created_by'>
  > & { next_activity?: Schemas['NextActivity'] | null };

/** DRF decimals are strings. `null` stays `null`. It means "not priced". */
function num(value: unknown) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Django serialises `assigned_to` as a list of Profiles; the board card and
 * the list row show one owner. Falls back to the creator, which is what the
 * column means by "Owner". Whoever is answerable for this deal.
 */
function ownerName(deal: DealFields) {
  const email = deal.assigned_to?.[0]?.user_details?.email;
  return email || deal.created_by?.email || '';
}

/**
 * The deal's earliest open task, as the list and the board get it from the
 * API: `{ id, title, due_date }`, or `null` when the caller has no open task
 * on the deal. The server counts only tasks this caller can open, so a hidden
 * task never reaches here. The deal detail does not carry the field, so there
 * it stays `undefined`, which the pages never read as "nothing scheduled".
 */
function toNextActivity(value: Schemas['NextActivity'] | null | undefined) {
  if (value === undefined) return undefined;
  if (!value) return null;
  return { id: value.id, title: value.title ?? '', due_date: value.due_date ?? null };
}

/**
 * The subset of a Django opportunity the v2 pages read.
 *
 * `days_in_current_stage` is `days_in_stage` on the wire, the serializer
 * renames the model property. Keeping the model's name here means the pages
 * and `opportunity/models.py` say the same word for the same number.
 */
function toRow(deal: DealFields) {
  return {
    id: deal.id,
    name: deal.name ?? '',
    account: {
      id: deal.account?.id ?? null,
      name: deal.account?.name ?? 'No account'
    },
    pipeline: deal.pipeline ?? null,
    stage: deal.stage,
    // What the stage is called and what it means, from the deal's own
    // pipeline. Closed is `stage_kind !== 'open'`, never a code prefix: an
    // admin can name a won stage anything.
    stage_label: deal.stage_label ?? deal.stage,
    stage_kind: deal.stage_kind ?? null,
    amount: num(deal.amount) ?? 0,
    currency: deal.currency || 'USD',
    probability: deal.probability ?? 0,
    closed_on: deal.closed_on ?? null,
    opportunity_type: deal.opportunity_type ?? 'NEW_BUSINESS',
    lead_source: deal.lead_source ?? '',
    description: deal.description ?? '',
    assigned_to: ownerName(deal),
    stage_changed_at: deal.stage_changed_at ?? null,
    days_in_current_stage: deal.days_in_stage ?? 0,
    aging_status: deal.aging_status ?? 'green',
    next_activity: toNextActivity(deal.next_activity),
    created_at: deal.created_at
  };
}

/**
 * The pipeline, largest first.
 *
 * `open` is NOT forced here. `OpportunityListView`'s base queryset does not
 * exclude closed stages (`opportunity_views.py:112-183`), so whether this call
 * is scoped to the working pipeline is entirely up to whether the caller's
 * `params` carries `?open=true`. That used to be hardcoded on every call,
 * which meant the pipeline's own "All deals" preset (deliberately empty
 * params, see `#lib/v2/filters.js`) could never show a closed deal no matter
 * what the URL said, a chip and a preset that cannot change the result set is
 * the exact failure this filter bar exists to remove. Callers that want the
 * open pipeline specifically (the sidebar badge, the task-parent pickers) now
 * set `open: 'true'` themselves.
 *
 * `totals` is computed server-side over the whole filtered queryset. Do not
 * recompute it from `results`: `results` is one page.
 */
export async function listDeals({ cookies }: { cookies: Cookies }, params?: URLSearchParams) {
  const query = new URLSearchParams(params ?? undefined);
  if (!query.has('limit')) query.set('limit', '25');

  const response: Schemas['OpportunityListResponse'] = await apiRequest(
    `/opportunities/?${query}`,
    {},
    { cookies }
  );
  const rows: ReturnType<typeof toRow>[] = (response.opportunities ?? []).map(toRow);
  rows.sort((a, b) => b.amount - a.amount);

  return {
    results: rows,
    totals: normaliseTotals(response.totals, rows)
  };
}

/**
 * The header figures, money per currency.
 *
 * Deals carry their own currency and there are no exchange rates, so the API
 * never adds two currencies together: `amount_sum` and `weighted_sum` are
 * `null` once there are several, and `by_currency` splits them. They become
 * `amount_by_currency` and `weighted_by_currency` here, each the
 * `{currency, amount}` list `moneyEach` prints, holding only the currencies
 * with something in them, so an empty list means nothing is priced.
 */
function normaliseTotals(
  totals: Schemas['OpportunityListTotals'] | null | undefined,
  rows: ReturnType<typeof toRow>[]
) {
  if (!totals) {
    // An older API without the aggregate. Say what is true of the rows we
    // hold rather than inventing a pipeline-wide figure.
    return {
      count: rows.length,
      amount_by_currency: sumByCurrency(rows),
      weighted_by_currency: [],
      stalled_count: rows.filter((row) => row.aging_status === 'red').length
    };
  }
  const perCurrency = (
    field: 'amount_sum' | 'weighted_sum',
    shape: (n: number) => number = (n) => n
  ) =>
    (totals.by_currency ?? [])
      .map((r) => ({ currency: r.currency, amount: shape(num(r[field]) ?? 0) }))
      .filter((m) => m.amount !== 0);
  return {
    count: totals.count,
    amount_by_currency: perCurrency('amount_sum'),
    // Rounded for the header. The API returns the exact figure, but eleven
    // cents on a two-million forecast is precision the number does not have,
    // and beside a whole `amount_sum` it reads as though the two were
    // measured differently.
    weighted_by_currency: perCurrency('weighted_sum', Math.round),
    stalled_count: totals.stalled_count
  };
}

/**
 * Every filter param `listDeals` will forward. See the note on FILTER_FIELDS
 * in tickets.js. `open` and `rotten` are deliberately absent: they are
 * preset-only params `pipeline/+page.server.ts` reads off the URL itself, the
 * same way `all` works for tickets and tasks, not fields a person picks a
 * value for.
 */
/**
 * The filter fields and presets the BOARD offers.
 *
 * `GET /opportunities/kanban/` runs the deal list's own filters
 * (`deal_list_queryset`): `search`, `name`, `account`, `assigned_to`, `tags`,
 * `stage`, `lead_source`, the `closed_on`, `created_at` and `amount` ranges,
 * `open` and `rotten`. The board still offers only these: it was built when
 * the endpoint read a narrower set, and no board UI has been added for the
 * rest. Only a param listed here is carried from the list to the board and
 * sent to both the board and its header totals, so the cards and the number
 * above them always describe the same deals.
 *
 * These live here rather than in `pipeline/+page.server.ts` because SvelteKit
 * allows only a fixed set of named exports from a `+page.server.ts` (`load`,
 * `actions`, `prerender`, and a few more) and answers 500 for the whole route
 * on any other one. Exporting them from there took the entire pipeline page
 * down, and neither `pnpm run check` nor the unit tests noticed, because the
 * tests import the module directly and never go through SvelteKit's router.
 */
export const BOARD_FIELDS = ['assigned_to', 'tags'];
export const BOARD_PRESETS = ['mine', 'all'];

export const FILTER_FIELDS = [
  'assigned_to',
  'stage',
  'lead_source',
  'amount__gte',
  'amount__lte',
  'tags'
];

/**
 * Every deal pipeline in the org, stages in board order.
 *
 * Read by every page that shows or edits a stage: the board and its pipeline
 * switcher, the stage meter, the deal forms. Members may read it; only admins
 * may change it (see `settings/deal-pipelines`).
 */
export async function listPipelines(cookies: Cookies) {
  const response: Schemas['DealPipelineListResponse'] = await apiRequest(
    '/opportunities/pipelines/',
    {},
    { cookies }
  );
  return (response.pipelines ?? []).map(toPipeline);
}

export function toPipeline(pipeline: Schemas['DealPipeline']) {
  return {
    id: pipeline.id,
    name: pipeline.name ?? '',
    is_default: Boolean(pipeline.is_default),
    stages: (pipeline.stages ?? []).map((stage) => ({
      id: stage.id,
      code: stage.code,
      label: stage.label ?? stage.code,
      order: stage.order,
      kind: stage.kind,
      expected_days: stage.expected_days ?? null,
      warning_days: stage.warning_days ?? null
    }))
  };
}

/**
 * The board for one pipeline, or the default one when `params` names none.
 *
 * This uses `/opportunities/kanban/` rather than grouping the list response,
 * because the list is paginated and lanes built from one page are wrong in a
 * way that looks right: every column renders, each is just missing rows. The
 * kanban endpoint returns each column with its true `item_count` and applies
 * the same RBAC scoping as the table.
 *
 * Only open stages become lanes: won and lost end a deal's path rather than
 * sit on it, whatever an admin has called them.
 *
 * It caps `items` at 100 per column. `truncated` carries that up so the page
 * can say so instead of quietly showing a short lane.
 */
export async function listBoard({ cookies }: { cookies: Cookies }, params?: URLSearchParams) {
  const query = new URLSearchParams(params ?? undefined);
  const response: Schemas['OpportunityKanbanResponse'] = await apiRequest(
    `/opportunities/kanban/?${query}`,
    {},
    { cookies }
  );

  const lanes = (response.columns ?? [])
    .filter((column) => column.kind === 'open')
    .map((column) => {
      // `canMove` is the kanban card's own flag; the list has none, so it is
      // added here rather than in `toRow`.
      const rows = (column.items ?? []).map((item) => ({
        ...toRow(item),
        canMove: canMoveCard(item)
      }));
      return {
        stage: column.id,
        label: column.name ?? column.id,
        rows,
        // The lane header counts every deal in the stage; its money can only
        // describe the ones actually returned, so a truncated lane says so.
        count: column.item_count,
        truncated: column.item_count > rows.length
      };
    });

  return { lanes, pipelineId: response.pipeline?.id ?? null };
}

/** One deal, its activity, its line items and the people on it. */
export async function getDeal({ cookies }: { cookies: Cookies }, id: string) {
  const [response, pipelines] = await Promise.all([
    fetchDetail(cookies, id),
    // The stepper is the only reader, and a deal page without its stepper is
    // still a deal page.
    listPipelines(cookies).catch(() => [])
  ]);
  const raw = response.opportunity_obj;
  const deal = toRow(raw);

  return {
    deal,
    // The deal's own pipeline, for the stage stepper.
    pipeline: pipelines.find((p) => p.id === deal.pipeline) ?? null,
    activity: buildActivity(response),
    lineItems: (raw.line_items ?? []).map((item) => ({
      id: item.id,
      name: item.name,
      quantity: num(item.quantity) ?? 0,
      unit_price: num(item.unit_price) ?? 0,
      discount_amount: num(item.discount_amount) ?? 0,
      subtotal: num(item.subtotal) ?? 0,
      total: num(item.total) ?? 0
    })),
    contacts: (response.contacts ?? []).map((contact) => ({
      id: contact.id,
      first_name: contact.first_name ?? '',
      last_name: contact.last_name ?? '',
      title: contact.title ?? '',
      // The mock had `relationship` here; "Champion", "Blocker". Contact has
      // no such field and nothing infers one.
      department: contact.department ?? ''
    })),
    customFields: fieldsForDetail(response, raw.custom_fields),
    // Newest first, through the download proxy. Comments are in `activity`.
    files: (response.attachments ?? []).map((a) => ({
      id: String(a.id),
      name: a.file_name || 'Attachment',
      at: a.created_at ?? null,
      href: attachmentHref(a.id)
    })),
    // The API's delete rule for this caller (an admin or the deal's creator),
    // so Delete is offered only when it would work.
    canDelete: Boolean(response.can_delete)
  };
}

/**
 * A comment, a file, or both, on a deal. Anyone who may open the deal may
 * add one; the API attributes it to the caller. The file field is
 * `opportunity_attachment`, and it may come without a comment.
 */
export async function addDealNote(
  { cookies }: { cookies: Cookies },
  id: string,
  comment: string,
  file: File | null = null
): Promise<Schemas['OpportunityCommentAttachmentResponse']> {
  if (file) {
    const form = new FormData();
    if (comment) form.set('comment', comment);
    form.set('opportunity_attachment', file);
    return await apiRequest(`/opportunities/${id}/`, { method: 'POST', body: form }, { cookies });
  }
  return await apiRequest(
    `/opportunities/${id}/`,
    { method: 'POST', body: { comment } },
    { cookies }
  );
}

/**
 * Delete this deal for good. The API applies the delete rule and answers 404
 * for a deal the caller cannot open; the page offers the action only when the
 * detail response said `can_delete`.
 */
export async function deleteDeal(
  { cookies }: { cookies: Cookies },
  id: string
): Promise<Schemas['OpportunityDetailDeleteResponse']> {
  return await apiRequest(`/opportunities/${id}/`, { method: 'DELETE' }, { cookies });
}

/**
 * Comments, plus the two events the record can actually reconstruct.
 *
 * The mock timeline had a run of stage changes. `Opportunity` keeps only
 * `stage_changed_at`, the *last* one, and there is no history table, so one
 * stage event is the honest maximum. It is emitted only when it is meaningfully
 * later than creation, because `save()` also stamps `stage_changed_at` on a new
 * record and "moved to Prospecting" the second a deal is created is noise.
 */
function buildActivity(response: Schemas['OpportunityDetailResponse']) {
  const deal = response.opportunity_obj;

  const events: Array<{
    id: string;
    type: 'note' | 'stage';
    at: string;
    by: string | null;
    body: string;
  }> = (response.comments ?? []).map((comment) => ({
    id: comment.id,
    type: 'note',
    at: comment.commented_on,
    by: authorText(comment.commented_by?.user_details) || null,
    body: comment.comment
  }));

  const created = new Date(deal.created_at).getTime();
  const changedAt = deal.stage_changed_at;
  const moved = changedAt ? new Date(changedAt).getTime() : 0;
  if (changedAt && moved && moved - created > 60_000) {
    events.push({
      id: `stage-${deal.id}`,
      type: 'stage',
      at: changedAt,
      by: null,
      // Deliberately not "moved from X to Y". The previous stage is not
      // recorded anywhere, and inventing it would be inventing history.
      body: 'Moved into this stage'
    });
  }

  events.push({
    id: `created-${deal.id}`,
    type: 'stage',
    at: deal.created_at,
    by: deal.created_by?.email ?? null,
    body: 'Deal created'
  });

  return events.sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());
}

/** Scalar fields the deal forms own. Everything else is server-derived. */
export const EDITABLE_FIELDS: Exclude<
  keyof Schemas['OpportunityCreateSwaggerRequest'],
  'custom_fields'
>[] = [
  'name',
  'account',
  'pipeline',
  'stage',
  'opportunity_type',
  'amount',
  'probability',
  'closed_on',
  'lead_source',
  'description'
];

/**
 * Accounts in this org, for the account select.
 *
 * Taken from the opportunities list response, which already returns
 * `accounts_list` scoped to the org and narrowed for non-admins the same way
 * the deals are, so the select cannot offer an account the person could not
 * otherwise see. `limit=1` because only the sidecar list is wanted.
 */
async function listAccounts(cookies: Cookies) {
  try {
    const response: Schemas['OpportunityListResponse'] = await apiRequest(
      '/opportunities/?limit=1',
      {},
      { cookies }
    );
    return (response.accounts_list ?? []).map((account) => ({
      id: account.id,
      name: account.name
    }));
  } catch {
    return [];
  }
}

/**
 * Profiles in this org, for the owner select.
 *
 * The API filters `assigned_to` ids through
 * `Profile.objects.filter(id__in=..., org=request.profile.org)` on the way in,
 * so a forged id cannot assign a deal into another tenant. It simply would
 * not match.
 */
async function listOwners(cookies: Cookies) {
  try {
    const response: Schemas['TeamsAndUsersResponse'] = await apiRequest(
      '/users/get-teams-and-users/',
      {},
      { cookies }
    );
    return (response.profiles ?? []).map((profile) => ({
      id: profile.id,
      name: profile.user_details?.email || 'Unknown'
    }));
  } catch {
    return [];
  }
}

/**
 * The caller's own Profile id, for defaulting the owner select to them.
 *
 * `GET /profile/` returns `request.profile`, derived from the JWT server-side,
 * so this is who the session actually is rather than who a form claims to be.
 * Empty on failure: an unassigned deal is a worse outcome than a wrong one is
 * dangerous, and the select is right there.
 */
async function myProfileId(cookies: Cookies) {
  try {
    const response: Schemas['ProfileViewResponse'] = await apiRequest('/profile/', {}, { cookies });
    return response.user_obj?.id ?? '';
  } catch {
    return '';
  }
}

/**
 * Everything the create form needs to render its selects, in one call, five
 * round trips to populate one form is how v1's create page took two seconds to
 * become usable.
 */
export async function getDealFormOptions({ cookies }: { cookies: Cookies }) {
  const [accounts, owners, mine, pipelines] = await Promise.all([
    listAccounts(cookies),
    listOwners(cookies),
    myProfileId(cookies),
    listPipelines(cookies)
  ]);
  // The API lists the default pipeline first and creates it if missing.
  const pipeline = pipelines.find((p) => p.is_default) ?? pipelines[0];
  const firstOpen = pipeline?.stages.find((s) => s.kind === 'open');
  return {
    accounts,
    owners,
    pipelines,
    defaults: {
      assigned_to: mine,
      currency: 'USD',
      pipeline: pipeline?.id ?? '',
      stage: firstOpen?.code ?? ''
    }
  };
}

/**
 * The deal plus what the edit form needs in order to explain itself.
 *
 * `form` holds only `EDITABLE_FIELDS`, so no input can bind to a
 * server-derived field by accident. The read-only context the page needs in
 * order to *describe* those fields travels separately in `server`.
 */
export async function getDealForEdit({ cookies }: { cookies: Cookies }, id: string) {
  const response = await fetchDetail(cookies, id);
  const raw = response.opportunity_obj;
  const deal = toRow(raw);

  const lineItems = raw.line_items ?? [];
  const [accounts, owners, pipelines] = await Promise.all([
    listAccounts(cookies),
    listOwners(cookies),
    listPipelines(cookies)
  ]);

  return {
    deal,
    accounts,
    owners,
    pipelines,
    customFields: fieldsForEdit(response, raw.custom_fields),
    form: {
      name: deal.name,
      account: deal.account.id ?? '',
      pipeline: deal.pipeline ?? '',
      stage: deal.stage,
      opportunity_type: deal.opportunity_type,
      amount: deal.amount ?? '',
      probability: deal.probability ?? '',
      closed_on: deal.closed_on ?? '',
      lead_source: deal.lead_source ?? '',
      description: deal.description ?? '',
      // Binds to a Profile id, not a display name. The mock bound the owner
      // select to a name, which reads identically on screen and cannot be saved.
      assigned_to: raw.assigned_to?.[0]?.id ?? ''
    },
    server: {
      // `recalculate_amount()` sets amount from the line items and flips
      // amount_source to CALCULATED whenever any exist, so a number typed into
      // the form would not survive the next save. Read from the record rather
      // than inferred, but the line-item count is what the form explains with.
      amount_source: lineItems.length ? 'CALCULATED' : raw.amount_source || 'MANUAL',
      line_item_count: lineItems.length,
      line_item_total: lineItems.reduce((total, item) => total + (num(item.total) ?? 0), 0),
      // Drives the aging chain the board renders. Changing stage resets it.
      stage_changed_at: deal.stage_changed_at,
      days_in_current_stage: deal.days_in_current_stage,
      aging_status: deal.aging_status
    }
  };
}

/**
 * Turn form values into a request body.
 *
 * Absent stays absent, a disabled control submits nothing, and on a deal with
 * line items the amount input is disabled precisely because the server owns
 * that number. Sending back the value the field was showing would be sending
 * a value nobody typed.
 */
function toBody(values: Record<string, unknown>) {
  const body: FormBody<Schemas['OpportunityCreateSwaggerRequest']> = {};
  for (const field of EDITABLE_FIELDS) {
    if (!(field in values)) continue;
    const value = values[field];
    // A source key is a blank-only string column, so "none" is '', not null.
    body[field] = value === '' && field !== 'lead_source' ? null : value;
  }
  if ('amount' in body && body.amount !== null) body.amount = Number(body.amount);
  if ('probability' in body && body.probability !== null) {
    body.probability = Number(body.probability);
  }
  // Single-select owner, so the list is empty or one long. Empty is
  // meaningful, it is how a deal is left unassigned, so it is sent.
  if ('assigned_to' in values) {
    body.assigned_to = values.assigned_to ? [values.assigned_to] : [];
  }
  if (values.custom_fields) body.custom_fields = values.custom_fields;
  return body;
}

/**
 * Save the edit form.
 *
 * PATCH, not PUT, for the same reason leads uses PATCH:
 * `OpportunityDetailView.put` runs `contacts.clear()`, `tags.clear()`,
 * `teams.clear()` and `assigned_to.clear()` unconditionally and re-adds only
 * what the body carried. This form owns nine scalar fields and the owner, so
 * every save through PUT would silently drop the deal's contacts, its tags and
 * its teams, the people and the context, kept by somebody else. `patch`
 * guards each of those with `if "<field>" in params`.
 */
export async function updateDeal(
  { cookies }: { cookies: Cookies },
  id: string,
  values: Record<string, unknown>
): Promise<Schemas['OpportunityPatchResponse']> {
  return await apiRequest(
    `/opportunities/${id}/`,
    { method: 'PATCH', body: toBody(values) },
    { cookies }
  );
}

/**
 * Move a deal to another board column, and optionally to a position in it.
 *
 * `columnId` is the `id` the board GET gave for the destination lane, handed
 * straight back. `aboveId`/`belowId` name the cards the drop landed between;
 * the server resolves them inside the destination column, so a stale id from a
 * board another user has since rearranged is ignored rather than honoured.
 * Sending neither appends to the lane.
 */
export async function moveDeal(
  { cookies }: { cookies: Cookies },
  id: string,
  { columnId, aboveId, belowId }: { columnId: string; aboveId?: string; belowId?: string }
): Promise<Schemas['OpportunityMoveResponse']> {
  return await apiRequest(
    `/opportunities/${id}/move/`,
    {
      method: 'PATCH',
      body: {
        column_id: columnId,
        ...(aboveId ? { above_id: aboveId } : {}),
        ...(belowId ? { below_id: belowId } : {})
      }
    },
    { cookies }
  );
}

/**
 * Create a deal.
 *
 * No `org` and no `created_by` in the body: `OpportunityListView.post` sets
 * both from `request.profile`, and `OpportunityCreateSerializer` does not list
 * either as a field, so they would be dropped anyway. A client should not be
 * sending fields whose absence is what makes them safe.
 */
export async function createDeal(
  { cookies }: { cookies: Cookies },
  values: Record<string, unknown>
): Promise<Schemas['OpportunityCreateResponse']> {
  return await apiRequest('/opportunities/', { method: 'POST', body: toBody(values) }, { cookies });
}

/**
 * `OpportunityDetailView.get` answers 404, with one body, both for a deal that
 * does not exist and for one this profile may not open. Deliberately not 403,
 * which would confirm the id exists. It is the caller's answer, not a server
 * fault, so it never becomes a 500, and the page cannot tell the two apart
 * either, so the copy covers both.
 */
async function fetchDetail(
  cookies: Cookies,
  id: string
): Promise<Schemas['OpportunityDetailResponse']> {
  try {
    return await apiRequest(`/opportunities/${id}/`, {}, { cookies });
  } catch (err) {
    rethrowModuleOff(err);
    // On the status, not on the wording. See `api-helpers.js`.
    if (apiStatus(err) === 404) {
      error(404, 'That deal does not exist, or you do not have access to it.');
    }
    throw err;
  }
}
