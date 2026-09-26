/**
 * Estimates: the fifteenth v2 module wired to the real API, and the second of
 * the invoices sub-pages (after products).
 *
 * Same rules as the fourteen before it: this file lives under `$lib/server`
 * because the access token is an httpOnly cookie SvelteKit refuses to bundle
 * into client code. The org is never a parameter; it is a claim in the JWT and
 * the backend reads it from there.
 *
 * WHY THIS MODULE
 * The estimates worklist has one job: surface money the customer has already
 * agreed to and nobody has billed. An estimate that is `Accepted` and not yet
 * converted, and let a rep raise the invoice in one click. The fixture faked
 * both the list and that action. Wiring them turns "Raise invoice" into a real
 * `convert` call that lands on the new draft.
 *
 * WHAT THE WIRING NEEDED FROM THE BACKEND
 * The list serializer omitted the two facts the worklist turns on,
 * `converted_to_invoice` and `opportunity`, so a row could not say whether it
 * had already been billed. Reading fixtures, every accepted estimate looked
 * unbilled, so the page would have offered to raise a *second* invoice for one
 * that already had one. Those two fields were added to `EstimateListSerializer`
 * (read-only, id + one label each) rather than papered over here.
 *
 * TOTALS ARE COMPUTED HERE, NOT ON THE WIRE
 * The estimate list endpoint has no `totals` envelope (unlike invoices). The
 * four header figures are derived from the loaded rows, so they cover the same
 * set the table shows: the requester's, which the API already scopes (an admin
 * sees the org, a member sees their own and assigned). At more than 1000
 * estimates the derived figures would cover only the first page; the list is
 * requested with `limit=1000` and that ceiling is called out where it is set.
 *
 * CREATING ONE
 * `/invoices/estimates/new` is the builder: the same line-item editor and
 * totals preview as the invoice builder (`LineItemsEditor`,
 * `$lib/v2/line-items.js`), plus a deal link and a validity date. Started from
 * a deal (`?opportunity=<id>`) it is prefilled from that deal by
 * `estimateFromDeal` below.
 */
import { apiRequest } from '$lib/api-helpers.js';
import { sumByCurrency } from '$lib/v2/format.js';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Statuses still awaiting the client's decision. The only ones with a live validity. */
const AWAITING = ['Sent', 'Viewed'];

/**
 * Every filter param `listEstimates` will forward. See the note on
 * FILTER_FIELDS in tickets.js: the descriptor in `$lib/v2/filters.js` must not
 * name a key absent from this list, and `filters.test.js` enforces it.
 *
 * `EstimateListView.get` (`backend/invoices/api_views.py:1028-1036`) reads
 * only `status` and `account`. There is no `assigned_to` filter on this
 * endpoint, which is why the descriptor has no Owner field; this list must
 * not grow one on its own either.
 */
export const FILTER_FIELDS = ['status', 'account'];

const num = (/** @type {any} */ value) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
};

/** Whole days from today to an ISO date; negative when the date is in the past. */
function daysUntil(/** @type {string | null} */ iso) {
  if (!iso) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;
  const startOfDay = (/** @type {Date} */ d) =>
    new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  return Math.round((startOfDay(then) - startOfDay(new Date())) / 86_400_000);
}

/**
 * A list row from `EstimateListSerializer`. The serializer splits the account
 * into an `account` uuid and a separate `account_name`; the page wants the
 * nested `{ id, name }` the mock had, so it is rebuilt here once. `contact` is
 * shown as a plain name, and `converted_to_invoice` is renamed to the
 * `converted_invoice` the page reads.
 *
 * @param {any} row
 */
function toRow(row) {
  const opp = row.opportunity;
  const converted = row.converted_to_invoice;
  return {
    id: row.id,
    estimate_number: row.estimate_number ?? '',
    title: row.title || '(untitled estimate)',
    status: row.status,
    account: { id: row.account ?? null, name: row.account_name ?? '(no account)' },
    contact: row.contact_name ?? row.client_name ?? '',
    opportunity: opp ? { id: opp.id, name: opp.name } : null,
    total_amount: num(row.total_amount),
    currency: row.currency ?? 'USD',
    valid_until: row.expiry_date ?? null,
    is_expired: Boolean(row.is_expired),
    converted_invoice: converted
      ? { id: converted.id, invoice_number: converted.invoice_number }
      : null
  };
}

/** Accepted and not yet billed. The row this whole page is built to surface. */
const needsBilling = (/** @type {any} */ e) => e.status === 'Accepted' && !e.converted_invoice;

/**
 * The header figures, derived from the visible rows.
 *
 * - `accepted_unconverted`, agreed money with no invoice yet (the point).
 * - `awaiting_reply`. Value of estimates the client has not answered.
 * - `expiring_within_7d`. Live estimates whose validity runs out inside a week.
 *
 * The two money figures are `{currency, amount}` lists for `moneyEach`: each
 * estimate carries its own currency and there are no exchange rates, so they
 * are never added across currencies.
 *
 * @param {any[]} rows
 */
function computeTotals(rows) {
  const awaiting = rows.filter((r) => AWAITING.includes(r.status));
  const expiring_within_7d = awaiting.filter((r) => {
    const d = daysUntil(r.valid_until);
    return d !== null && d >= 0 && d <= 7;
  }).length;
  return {
    accepted_unconverted: sumByCurrency(rows.filter(needsBilling), 'total_amount'),
    awaiting_reply: sumByCurrency(awaiting, 'total_amount'),
    expiring_within_7d
  };
}

/**
 * The estimates worklist, accepted-but-unbilled first.
 *
 * The rows follow whatever the API returns for the requester (org-wide for an
 * admin, own-and-assigned for a member, the endpoint scopes it), so the header
 * figures and the table always describe the same set.
 *
 * `params` (built from FILTER_FIELDS: `status`, `account`) is applied by
 * `EstimateListView.get` before pagination, and the header figures below are
 * derived from the rows this call actually gets back, so a filter narrows
 * both the table and the totals together, unlike `listInvoices`.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {URLSearchParams} [params]
 */
export async function listEstimates({ cookies }, params) {
  const query = new URLSearchParams(params ?? undefined);
  // One page covers every real org; totals are derived from what comes back,
  // so anything past this ceiling would be uncounted. See the module note.
  if (!query.has('limit')) query.set('limit', '1000');

  const response = await apiRequest(`/invoices/estimates/?${query.toString()}`, {}, { cookies });
  const rows = (response.results ?? []).map(toRow);
  // Bring the one row with an action to the top; the API already sorts the rest
  // newest-first, and Array.prototype.sort is stable, so that order survives.
  rows.sort((a, b) => Number(needsBilling(b)) - Number(needsBilling(a)));

  return {
    estimates: rows,
    totals: {
      count: response.count ?? rows.length,
      ...computeTotals(rows)
    }
  };
}

/**
 * Raise an invoice from an estimate. A bare POST through
 * `get_estimate_or_error`, so the API, not this layer, decides who may
 * convert (creator, an assignee, or an admin) and refuses the rest, and
 * refuses a second conversion of an already-converted estimate. Returns the new
 * invoice so the caller can open it.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function convertEstimate({ cookies }, id) {
  return apiRequest(
    `/invoices/estimates/${id}/convert/`,
    { method: 'POST', body: {} },
    { cookies }
  );
}

/**
 * Mail the estimate to its client (Draft becomes Sent). The API refuses an
 * Accepted, Declined or Expired estimate and one past its validity date, each
 * with a sentence worth showing.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function sendEstimate({ cookies }, id) {
  return apiRequest(`/invoices/estimates/${id}/send/`, { method: 'POST', body: {} }, { cookies });
}

/**
 * Create an estimate from the builder. The body is already the API shape
 * (account_id/contact_id/title required, line_items nested). The server owns
 * org, created_by, the number, the public token, every total and the status
 * (always Draft), and decides whether this caller may bill the account and
 * cite the deal. The 201 wraps the record as `{ estimate }`.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {Record<string, any>} body
 */
export async function createEstimate({ cookies }, body) {
  const response = await apiRequest('/invoices/estimates/', { method: 'POST', body }, { cookies });
  return response.estimate ?? response;
}

/**
 * What an estimate started from a deal begins with: the deal link, its
 * account, currency and name, the first contact on it the caller may open,
 * and its line items as editable lines.
 *
 * The deal detail GET decides whether the caller may open the deal; a 403 or
 * 404 there yields `null`, and the builder opens blank rather than failing.
 * The API checks the deal again on save, so nothing here is a gate.
 *
 * Each line keeps the deal line's discount, which the estimate's total
 * counts (`LineAmounts.net_amount`), so the estimate starts at the price the
 * deal was agreed at rather than the list price.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function estimateFromDeal({ cookies }, id) {
  // From the query string, so it is checked before it becomes part of an API
  // path: anything but a UUID prefills nothing.
  if (!UUID_RE.test(id)) return null;
  let response;
  try {
    response = await apiRequest(`/opportunities/${id}/`, {}, { cookies });
  } catch (/** @type {any} */ err) {
    if (err?.status === 403 || err?.status === 404) return null;
    throw err;
  }
  const deal = response.opportunity_obj ?? {};
  return {
    opportunity_id: deal.id ?? id,
    account_id: deal.account?.id ?? '',
    currency: deal.currency || '',
    title: deal.name ? `Estimate for ${deal.name}` : '',
    // The detail GET lists only contacts the caller may open.
    contact_ids: (response.contacts ?? []).map((/** @type {any} */ c) => c.id),
    items: (deal.line_items ?? []).map((/** @type {any} */ li) => ({
      name: li.name ?? li.product?.name ?? '',
      description: li.description ?? '',
      quantity: num(li.quantity),
      unit_price: num(li.unit_price),
      product: li.product?.id ?? null,
      discount_type: li.discount_type ?? '',
      discount_value: num(li.discount_value)
    }))
  };
}
