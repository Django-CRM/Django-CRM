/**
 * Pipeline: the second v2 module wired to the real API.
 *
 * Same rules as `leads.js`, which has the long version of the reasoning:
 * this file lives under `$lib/server` because SvelteKit refuses to bundle
 * that directory into client code and the access token is an httpOnly
 * cookie; the org is never a parameter, only ever a claim inside the JWT.
 *
 * WHAT CHANGED WHEN THE FIXTURES CAME OFF
 * Three things the mock showed do not exist on `Opportunity` and are gone
 * rather than faked: `next_action` (there is no such field, and nothing
 * derives one), `last_activity_at` (aging is measured from `stage_changed_at`,
 * which is a different claim), and a contact's `relationship` (Contact has
 * `title` and `department`). The related invoices and tickets rail is also
 * gone. Those are real models, but attaching them to a deal means querying
 * two more modules per page load for a panel nobody asked for; add it back
 * deliberately if it earns the round trips.
 *
 * NUMBERS ARRIVE AS STRINGS
 * DRF renders `DecimalField` as a string, so `amount`, `unit_price` and the
 * line-item totals come back as `"42000.00"`. The pages sum them. `"1" + "2"`
 * is `"12"`, so every one of them is coerced here, at the boundary, rather
 * than in whichever component happens to add them up first.
 */
import { error } from '@sveltejs/kit';
import { apiRequest } from '$lib/api-helpers.js';

/** DRF decimals are strings. `null` stays `null`. It means "not priced". */
function num(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Django serialises `assigned_to` as a list of Profiles; the board card and
 * the list row show one owner. Falls back to the creator, which is what the
 * column means by "Owner". Whoever is answerable for this deal.
 *
 * @param {any} deal
 */
function ownerName(deal) {
  const assigned = deal.assigned_to?.[0];
  const email = assigned?.user_details?.email || assigned?.user?.email;
  return email || deal.created_by?.email || '';
}

/**
 * The subset of a Django opportunity the v2 pages read.
 *
 * `days_in_current_stage` is `days_in_stage` on the wire, the serializer
 * renames the model property. Keeping the model's name here means the pages
 * and `opportunity/models.py` say the same word for the same number.
 *
 * @param {any} deal
 */
function toRow(deal) {
  return {
    id: deal.id,
    name: deal.name ?? '',
    account: {
      id: deal.account?.id ?? null,
      name: deal.account?.name ?? 'No account'
    },
    stage: deal.stage,
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
 * params, see `$lib/v2/filters.js`) could never show a closed deal no matter
 * what the URL said, a chip and a preset that cannot change the result set is
 * the exact failure this filter bar exists to remove. Callers that want the
 * open pipeline specifically (the sidebar badge, the task-parent pickers) now
 * set `open: 'true'` themselves.
 *
 * `totals` is computed server-side over the whole filtered queryset. Do not
 * recompute it from `results`: `results` is one page.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {URLSearchParams} [params]
 */
export async function listDeals({ cookies }, params) {
  const query = new URLSearchParams(params ?? undefined);
  if (!query.has('limit')) query.set('limit', '25');

  const response = await apiRequest(`/opportunities/?${query}`, {}, { cookies });
  const rows = (response.opportunities ?? []).map(toRow);
  rows.sort((a, b) => b.amount - a.amount);

  return {
    results: rows,
    totals: normaliseTotals(response.totals, rows)
  };
}

/**
 * @param {any} totals
 * @param {any[]} rows
 */
function normaliseTotals(totals, rows) {
  if (!totals) {
    // An older API without the aggregate. Say what is true of the rows we
    // hold rather than inventing a pipeline-wide figure.
    return {
      count: rows.length,
      amount_sum: rows.reduce((sum, row) => sum + row.amount, 0),
      weighted_sum: 0,
      stalled_count: rows.filter((row) => row.aging_status === 'red').length
    };
  }
  return {
    count: totals.count,
    amount_sum: num(totals.amount_sum) ?? 0,
    // Rounded for the header. The API returns the exact figure, but eleven
    // cents on a two-million-dollar forecast is precision the number does not
    // have, and beside a whole-dollar `amount_sum` it reads as though the two
    // were measured differently.
    weighted_sum: Math.round(num(totals.weighted_sum) ?? 0),
    stalled_count: totals.stalled_count
  };
}

/**
 * Every filter param `listDeals` will forward. See the note on FILTER_FIELDS
 * in tickets.js. `open` and `rotten` are deliberately absent: they are
 * preset-only params `pipeline/+page.server.js` reads off the URL itself, the
 * same way `all` works for tickets and tasks, not fields a person picks a
 * value for.
 */
/**
 * The filter fields and presets the BOARD can actually run.
 *
 * `kanban_views.py:122-137` reads only `search`, `account`, `assigned_to`,
 * `tags` and the `closed_on` range, a narrower vocabulary than the list
 * endpoint's. A chip for anything else would sit above cards it did not
 * filter, while the header totals (which come from the list endpoint) DID
 * apply it, so a number and the cards under it would describe different deals.
 *
 * These live here rather than in `pipeline/+page.server.js` because SvelteKit
 * allows only a fixed set of named exports from a `+page.server.js` (`load`,
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
 * The board.
 *
 * This uses `/opportunities/kanban/` rather than grouping the list response,
 * because the list is paginated and lanes built from one page are wrong in a
 * way that looks right: every column renders, each is just missing rows. The
 * kanban endpoint returns each column with its true `item_count` and applies
 * the same RBAC scoping as the table.
 *
 * It caps `items` at 100 per column. `truncated` carries that up so the page
 * can say so instead of quietly showing a short lane.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {URLSearchParams} [params]
 */
export async function listBoard({ cookies }, params) {
  const query = new URLSearchParams(params ?? undefined);
  const response = await apiRequest(`/opportunities/kanban/?${query}`, {}, { cookies });

  const lanes = (response.columns ?? [])
    .filter((/** @type {any} */ column) => !column.id.startsWith('CLOSED_'))
    .map((/** @type {any} */ column) => {
      const rows = (column.items ?? []).map(toRow);
      return {
        stage: column.id,
        rows,
        // The lane header counts every deal in the stage; the sum can only
        // describe the ones actually returned, so a truncated lane says so.
        count: column.item_count,
        sum: rows.reduce((total, row) => total + row.amount, 0),
        truncated: column.item_count > rows.length
      };
    });

  return { lanes };
}

/**
 * One deal, its activity, its line items and the people on it.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function getDeal({ cookies }, id) {
  const response = await fetchDetail(cookies, id);
  const raw = response.opportunity_obj;

  return {
    deal: toRow(raw),
    activity: buildActivity(response),
    lineItems: (raw.line_items ?? []).map((/** @type {any} */ item) => ({
      id: item.id,
      name: item.name,
      quantity: num(item.quantity) ?? 0,
      unit_price: num(item.unit_price) ?? 0,
      discount_amount: num(item.discount_amount) ?? 0,
      subtotal: num(item.subtotal) ?? 0,
      total: num(item.total) ?? 0
    })),
    contacts: (response.contacts ?? []).map((/** @type {any} */ contact) => ({
      id: contact.id,
      first_name: contact.first_name ?? '',
      last_name: contact.last_name ?? '',
      title: contact.title ?? '',
      // The mock had `relationship` here; "Champion", "Blocker". Contact has
      // no such field and nothing infers one.
      department: contact.department ?? ''
    }))
  };
}

/**
 * Comments, plus the two events the record can actually reconstruct.
 *
 * The mock timeline had a run of stage changes. `Opportunity` keeps only
 * `stage_changed_at`, the *last* one, and there is no history table, so one
 * stage event is the honest maximum. It is emitted only when it is meaningfully
 * later than creation, because `save()` also stamps `stage_changed_at` on a new
 * record and "moved to Prospecting" the second a deal is created is noise.
 *
 * @param {any} response
 */
function buildActivity(response) {
  const deal = response.opportunity_obj;

  const events = (response.comments ?? []).map((/** @type {any} */ comment) => ({
    id: comment.id,
    type: 'note',
    at: comment.commented_on,
    by: comment.commented_by_user?.email || comment.commented_by?.user_details?.email || null,
    body: comment.comment
  }));

  const created = new Date(deal.created_at).getTime();
  const moved = deal.stage_changed_at ? new Date(deal.stage_changed_at).getTime() : 0;
  if (moved && moved - created > 60_000) {
    events.push({
      id: `stage-${deal.id}`,
      type: 'stage',
      at: deal.stage_changed_at,
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

  return events.sort(
    (/** @type {any} */ a, /** @type {any} */ b) =>
      new Date(b.at).getTime() - new Date(a.at).getTime()
  );
}

/** Scalar fields the deal forms own. Everything else is server-derived. */
export const EDITABLE_FIELDS = [
  'name',
  'account',
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
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 */
async function listAccounts(cookies) {
  try {
    const response = await apiRequest('/opportunities/?limit=1', {}, { cookies });
    return (response.accounts_list ?? []).map((/** @type {any} */ account) => ({
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
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 */
async function listOwners(cookies) {
  try {
    const response = await apiRequest('/users/get-teams-and-users/', {}, { cookies });
    return (response.profiles ?? []).map((/** @type {any} */ profile) => ({
      id: profile.id,
      name: profile.user_details?.email || profile.user?.email || 'Unknown'
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
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 */
async function myProfileId(cookies) {
  try {
    const response = await apiRequest('/profile/', {}, { cookies });
    return response.user_obj?.id ?? '';
  } catch {
    return '';
  }
}

/**
 * Everything the create form needs to render its selects, in one call, five
 * round trips to populate one form is how v1's create page took two seconds to
 * become usable.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function getDealFormOptions({ cookies }) {
  const [accounts, owners, mine] = await Promise.all([
    listAccounts(cookies),
    listOwners(cookies),
    myProfileId(cookies)
  ]);
  return {
    accounts,
    owners,
    defaults: { assigned_to: mine, currency: 'USD', stage: 'PROSPECTING' }
  };
}

/**
 * The deal plus what the edit form needs in order to explain itself.
 *
 * `form` holds only `EDITABLE_FIELDS`, so no input can bind to a
 * server-derived field by accident. The read-only context the page needs in
 * order to *describe* those fields travels separately in `server`.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function getDealForEdit({ cookies }, id) {
  const response = await fetchDetail(cookies, id);
  const raw = response.opportunity_obj;
  const deal = toRow(raw);

  const lineItems = raw.line_items ?? [];
  const [accounts, owners] = await Promise.all([listAccounts(cookies), listOwners(cookies)]);

  return {
    deal,
    accounts,
    owners,
    form: {
      name: deal.name,
      account: deal.account.id ?? '',
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
      line_item_total: lineItems.reduce(
        (/** @type {number} */ total, /** @type {any} */ item) => total + (num(item.total) ?? 0),
        0
      ),
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
 *
 * @param {Record<string, any>} values
 */
function toBody(values) {
  /** @type {Record<string, any>} */
  const body = {};
  for (const field of EDITABLE_FIELDS) {
    if (!(field in values)) continue;
    const value = values[field];
    body[field] = value === '' ? null : value;
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
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {Record<string, any>} values
 */
export async function updateDeal({ cookies }, id, values) {
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
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {{ columnId: string, aboveId?: string, belowId?: string }} position
 */
export async function moveDeal({ cookies }, id, { columnId, aboveId, belowId }) {
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
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {Record<string, any>} values
 */
export async function createDeal({ cookies }, values) {
  return await apiRequest('/opportunities/', { method: 'POST', body: toBody(values) }, { cookies });
}

/**
 * `OpportunityDetailView.get` answers 404 for another org's deal. Deliberately
 * not 403, which would confirm the id exists, and 403 for a deal inside the
 * org that this profile neither created nor is assigned to. Both are the
 * caller's answer, not a server fault, so neither becomes a 500.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @param {string} id
 */
async function fetchDetail(cookies, id) {
  try {
    return await apiRequest(`/opportunities/${id}/`, {}, { cookies });
  } catch (/** @type {any} */ err) {
    // On the status, not on the wording. See `api-helpers.js`.
    if (err?.status === 404) {
      error(404, 'That deal does not exist, or it belongs to another team.');
    }
    if (err?.status === 403) {
      error(403, 'This deal belongs to somebody else. Ask an admin if you need it.');
    }
    throw err;
  }
}
