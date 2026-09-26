/**
 * Recurring invoices: the sixteenth v2 module, third of the invoices sub-pages.
 *
 * Same rules as the fifteen before it: server-only, because the access token is
 * an httpOnly cookie; the org is a JWT claim the backend reads, never a param.
 *
 * WHY THIS MODULE, AND THE FINDING IT CARRIED
 * A recurring invoice is an owned billing record, `AssignableMixin` +
 * `created_by`, the same shape as Invoice and Estimate, but its endpoints
 * filtered on `org` ONLY: no object-level check on detail/update/delete/toggle
 * and no non-admin list scoping. Any member could read, edit, delete or
 * pause/resume every schedule in the org, while Invoice and Estimate beside it
 * scoped non-admins to their own and assigned. That was fixed on the backend
 * (a `get_recurring_or_error` mirroring the estimate one, plus list scoping);
 * this page is the client that finally draws the writes those endpoints guard.
 *
 * THE ONE WRITE, THEN THE SECOND
 * The page's thesis is "does anyone have to do anything"; `auto_send`, and
 * whether a schedule is live or paused. Pause/resume was the first write, next
 * to the state it changes (`toggle`). Creating a schedule is the second: a
 * builder with line items and three FK pickers, the same shape as the invoice
 * builder (#48), and it lives here as `createRecurringInvoice`.
 *
 * TOTALS ARE COMPUTED HERE
 * The list endpoint has no `totals` envelope. The four header figures are
 * derived from the rows the API returns for this requester (org-wide for an
 * admin, own-and-assigned for a member), so the pills and the table describe the
 * same set. `monthly_run_rate` normalises every ACTIVE schedule to a month by
 * its cadence. The one figure nobody can eyeball off the rows, because each is
 * on a different frequency. `limit=1000`; past that the derived figures would
 * undercount, and that ceiling is called out where it is set.
 */
import { apiRequest } from '$lib/api-helpers.js';

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

/** Times a fixed cadence generates per year; CUSTOM is derived from custom_days. */
const YEARLY_OCCURRENCES = {
  WEEKLY: 52,
  BIWEEKLY: 26,
  MONTHLY: 12,
  QUARTERLY: 4,
  SEMI_ANNUALLY: 2,
  YEARLY: 1
};

/** One schedule's contribution to the monthly run rate: amount × runs-per-month. */
function monthlyRunRate(/** @type {any} */ s) {
  let perYear;
  if (s.frequency === 'CUSTOM') {
    perYear = s.custom_days ? 365.25 / s.custom_days : 0;
  } else {
    perYear = YEARLY_OCCURRENCES[s.frequency] ?? 0;
  }
  return (s.total_amount * perYear) / 12;
}

/**
 * A list row from `RecurringInvoiceListSerializer`. The account is split into an
 * `account` uuid and `account_name`; the page wants the nested `{ id, name }`,
 * and reads `contact` as a plain name.
 *
 * @param {any} row
 */
function toRow(row) {
  return {
    id: row.id,
    title: row.title || '(untitled schedule)',
    is_active: Boolean(row.is_active),
    account: { id: row.account ?? null, name: row.account_name ?? '(no account)' },
    contact: row.contact_name ?? row.client_name ?? '',
    frequency: row.frequency,
    custom_days: row.custom_days ?? null,
    payment_terms: row.payment_terms,
    start_date: row.start_date ?? null,
    end_date: row.end_date ?? null,
    next_generation_date: row.next_generation_date ?? null,
    auto_send: Boolean(row.auto_send),
    total_amount: num(row.total_amount),
    currency: row.currency ?? 'USD',
    invoices_generated: row.invoices_generated ?? 0
  };
}

/**
 * The four header figures, derived from the visible rows.
 *
 * - `active`. Schedules currently generating.
 * - `monthly_run_rate`, every active schedule normalised to a month.
 * - `due_within_7d`, active schedules generating within a week (or overdue):
 *   drafts to check before they send.
 *
 * @param {any[]} rows
 */
function computeTotals(rows) {
  let active = 0;
  let runRate = 0;
  let due_within_7d = 0;
  for (const s of rows) {
    if (!s.is_active) continue;
    active += 1;
    runRate += monthlyRunRate(s);
    const d = daysUntil(s.next_generation_date);
    if (d !== null && d <= 7) due_within_7d += 1;
  }
  return { active, monthly_run_rate: Math.round(runRate), due_within_7d };
}

/** Active schedules first, then soonest to next generate. */
function bySchedulePriority(/** @type {any} */ a, /** @type {any} */ b) {
  if (a.is_active !== b.is_active) return Number(b.is_active) - Number(a.is_active);
  const da = daysUntil(a.next_generation_date);
  const db = daysUntil(b.next_generation_date);
  if (da === null) return 1;
  if (db === null) return -1;
  return da - db;
}

/**
 * Every filter param `listRecurringInvoices` will forward. The descriptor in
 * `$lib/v2/filters.js` must not name a key absent from this list; a test in
 * `filters.test.js` enforces that.
 */
export const FILTER_FIELDS = ['is_active'];

/**
 * The schedules worklist. Rows follow whatever the API returns for the requester
 * (the endpoint scopes non-admins to their own and assigned), so the pills and
 * the table cover the same set. An `is_active` filter narrows the same rows the
 * totals are derived from, so the header figures and the table always agree.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {URLSearchParams} [params]
 */
export async function listRecurringInvoices({ cookies }, params) {
  const query = new URLSearchParams(params ?? undefined);
  // One page covers every real org; totals derive from what comes back.
  if (!query.has('limit')) query.set('limit', '1000');

  const response = await apiRequest(`/invoices/recurring/?${query.toString()}`, {}, { cookies });
  const rows = (response.results ?? []).map(toRow);
  rows.sort(bySchedulePriority);

  return {
    schedules: rows,
    totals: {
      count: response.count ?? rows.length,
      ...computeTotals(rows)
    }
  };
}

/**
 * Pause or resume a schedule. A bare POST through `get_recurring_or_error`, so
 * the API, not this layer, decides who may toggle (creator, an assignee, or
 * an admin) and refuses the rest. The endpoint flips `is_active` and returns the
 * updated row.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function toggleRecurring({ cookies }, id) {
  return apiRequest(`/invoices/recurring/${id}/toggle/`, { method: 'POST', body: {} }, { cookies });
}

/**
 * Every field `RecurringInvoiceCreateSerializer` accepts. Anything not on this
 * list never reaches the API, which is what keeps a forged `org`, `created_by`,
 * or a hand-set `total_amount` out of the body. Totals are recalculated
 * server-side on every create and update, and `org` comes from the JWT.
 */
export const CREATE_FIELDS = [
  'account_id',
  'contact_id',
  'opportunity_id',
  'title',
  'is_active',
  'client_name',
  'client_email',
  'frequency',
  'custom_days',
  'start_date',
  'end_date',
  'next_generation_date',
  'payment_terms',
  'auto_send',
  'currency',
  'discount_type',
  'discount_value',
  'tax_rate',
  'notes',
  'terms',
  'line_items'
];

/**
 * Copy only allow-listed keys. `undefined` is skipped rather than anything
 * falsy, so an explicit empty string still travels and can clear a field, while
 * a key the form never rendered stays absent. An empty `line_items` array is
 * dropped: the serializer treats the field as optional and sending `[]` would
 * be an explicit "no lines" write rather than "not specified".
 *
 * The allow-list applies to TOP-LEVEL keys only. `line_items` is copied through
 * as-is, so an extra key inside a line-item object is not stripped here. That is
 * safe because `RecurringInvoiceLineItemCreateSerializer` declares its own
 * explicit field list and DRF drops anything else during nested
 * `to_internal_value`, which is the check that actually matters. Stated plainly
 * so nobody reads this function as a deeper guarantee than it gives.
 *
 * @param {string[]} allowed
 * @param {Record<string, any>} values
 * @returns {Record<string, any>}
 */
export function buildBody(allowed, values) {
  /** @type {Record<string, any>} */
  const body = {};
  for (const key of allowed) {
    const value = values[key];
    if (value === undefined) continue;
    if (key === 'line_items') {
      if (Array.isArray(value) && value.length > 0) body[key] = value;
      continue;
    }
    body[key] = value;
  }
  return body;
}

/**
 * Create a recurring schedule.
 *
 * `POST /invoices/recurring/` has `permission_classes = (IsAuthenticated,
 * HasOrgContext)` and no role gate, so any org member may create one. Do not
 * hide the control behind an admin check: that would be a UX regression, not a
 * security improvement, and the backend is the boundary either way.
 *
 * The five guards below are fast fails for obvious mistakes, and each mirrors a
 * server rule: `account_id` and `contact_id` are required by
 * `RecurringInvoiceCreateSerializer` (and it also cross-checks that the
 * contact belongs to the account), `title` is required because
 * `RecurringInvoice.title` is a `CharField(max_length=100)` with no
 * `blank=True`, a CUSTOM frequency needs `custom_days` (the serializer refuses
 * one without, which used to bill monthly), and auto-send needs at least one
 * line (the serializer refuses a schedule that would mail a blank invoice).
 *
 * `RecurringInvoiceListView.post` (`backend/invoices/api_views.py`) wraps its
 * result in an envelope, `{ error, message, recurring_invoice }`, exactly the
 * trap `createTag` documents in `tags.js`: a `create*` function that resolves
 * to the envelope instead of the record is the one the next caller reaches
 * for `.id` on and gets `undefined`. This unwraps to `.recurring_invoice`
 * deliberately, with the same `?? resp` fallback as `createTag`, so a change
 * in the backend's response shape degrades to the raw envelope rather than
 * throwing.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {Record<string, any>} values
 * @returns {Promise<any>}
 */
export async function createRecurringInvoice({ cookies }, values) {
  if (!values.account_id) throw new Error('Choose an account.');
  if (!values.contact_id) throw new Error('Choose a contact.');
  if (!(values.title ?? '').toString().trim()) throw new Error('Give the schedule a title.');
  if (values.frequency === 'CUSTOM' && !values.custom_days) {
    throw new Error('A custom frequency needs an interval in days.');
  }
  // The server's rule: a schedule that mails each invoice needs a line.
  if (values.auto_send && !values.line_items?.length) {
    throw new Error('Add at least one line before turning on auto-send.');
  }

  const body = buildBody(CREATE_FIELDS, values);
  const resp = await apiRequest('/invoices/recurring/', { method: 'POST', body }, { cookies });
  return resp.recurring_invoice ?? resp;
}
