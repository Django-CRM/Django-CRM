/**
 * Invoices: the eighth v2 module wired to the real API.
 *
 * Same rules as the seven before it: this file lives under `$lib/server`
 * because SvelteKit refuses to bundle that directory into client code and the
 * access token is an httpOnly cookie. The org is never a parameter; it is a
 * claim inside the JWT and the backend reads it from there.
 *
 * WHY THIS MODULE, AND WHY NOW
 * The account page has rendered a real Invoices rail since accounts were wired
 *: actual rows, with a "View all" that pointed at the fixture list, and rows
 * that were deliberately *not* links because there was no invoice page to open.
 * Same quiet dead end the Tasks rail was last session. Wiring the list and the
 * detail turns both into real links.
 *
 * WHAT CHANGED WHEN THE FIXTURES CAME OFF
 * - **The reminders log is not a thing.** The mock detail carried a per-reminder
 *   history, "First reminder · opened · 8d ago", with read receipts. The
 *   model records no such log and tracks no opens: it has `reminder_count`,
 *   `last_reminder_sent` and the on/off settings, nothing per-send and nothing
 *   about the client opening anything. The rail now shows those real settings;
 *   the invented "they opened it" line is gone.
 * - **Line items carry discount and tax the mock summary ignored.** The mock
 *   detail recomputed subtotal as `Σ qty×rate` and called the remainder "tax",
 *   which silently folds any discount into the tax line. Real invoices have
 *   `subtotal`, `discount_amount` and `tax_amount` computed server-side, so the
 *   page shows those figures instead of re-deriving them wrong.
 * - **Status "Overdue" is a real status, not a UI flourish.** A daily task
 *   (`mark_overdue_invoices`) flips unpaid past-due invoices to it, so the
 *   detail page's Overdue branch stays. It is describing a column, not
 *   guessing from the due date.
 *
 * WHAT STAYED FIXTURES, ON PURPOSE
 * Only the core invoice entity is wired here: list, detail, and the lifecycle
 * writes on the detail page. Estimates, recurring, products, templates and the
 * reports dashboard are separate entities on separate endpoints; they keep
 * their fixtures and their preview banner (see `migrated.js`). Bringing the
 * invoice list to the real API did not bring them, exactly as wiring the task
 * list did not wire the board.
 */
import { error } from '@sveltejs/kit';
import { apiRequest } from '$lib/api-helpers.js';

/** `invoices.models.INVOICE_STATUS`. Underscore on the wire; never shown raw. */
export const INVOICE_STATUSES = [
  'Draft',
  'Sent',
  'Viewed',
  'Paid',
  'Partially_Paid',
  'Overdue',
  'Pending',
  'Cancelled'
];

/** Statuses a settled invoice can be in: the due date stops meaning anything. */
const SETTLED = ['Paid', 'Cancelled'];

/**
 * Every filter param `listInvoices` will forward. See the note on
 * FILTER_FIELDS in tickets.js: the descriptor in `$lib/v2/filters.js` must not
 * name a key absent from this list, and `filters.test.js` enforces it.
 *
 * `due_date_gte` / `due_date_lte`: SINGLE underscore.
 * `InvoiceListView.filter_queryset` (`backend/invoices/api_views.py:149-152`)
 * reads exactly these two spellings. Every other v2 module's date-range field
 * uses a DOUBLE underscore (`due_date__gte`); forwarding that spelling here
 * would silently return the unfiltered list rather than erroring, which is
 * why `filter-params.test.js` pins this one explicitly.
 */
export const FILTER_FIELDS = ['status', 'account', 'assigned_to', 'due_date_gte', 'due_date_lte'];

const num = (/** @type {any} */ value) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
};

/**
 * A list row, from `InvoiceListSerializer`.
 *
 * The serializer splits the account into a `account` uuid and a separate
 * `account_name`; the pages want the nested `{ id, name }` the mock had, so it
 * is rebuilt here once rather than in every template.
 *
 * @param {any} row
 */
function toRow(row) {
  return {
    id: row.id,
    invoice_number: row.invoice_number ?? '',
    invoice_title: row.invoice_title ?? '',
    status: row.status,
    account: { id: row.account ?? null, name: row.account_name ?? '(no account)' },
    total_amount: num(row.total_amount),
    amount_due: num(row.amount_due),
    amount_paid: num(row.amount_paid),
    currency: row.currency ?? 'USD',
    issued_date: row.issue_date ?? null,
    due_date: row.due_date ?? null,
    is_overdue: Boolean(row.is_overdue),
    line_items_count: row.line_items_count ?? 0
  };
}

/**
 * A line item as the detail table reads it. `amount` is the server's
 * `net_amount`: quantity x unit price less the line's own discount, which is
 * what the lines add up to in the subtotal. The page does not recompute it.
 *
 * @param {any} item
 */
function toLineItem(item) {
  return {
    id: item.id,
    name: item.name || item.product_name || 'Item',
    detail: item.description ?? '',
    quantity: num(item.quantity),
    rate: num(item.unit_price),
    tax_rate: num(item.tax_rate),
    amount: num(item.net_amount)
  };
}

/**
 * The full invoice the detail page reads. Financial figures come straight from
 * the serializer (server-computed) rather than being re-derived in the browser.
 *
 * @param {any} inv
 */
function toDetail(inv) {
  const account = inv.account ?? {};
  return {
    id: inv.id,
    invoice_number: inv.invoice_number ?? '',
    invoice_title: inv.invoice_title ?? '',
    status: inv.status,
    account: { id: account.id ?? null, name: account.name ?? '(no account)' },
    currency: inv.currency ?? 'USD',
    issued_date: inv.issue_date ?? null,
    due_date: inv.due_date ?? null,
    payment_terms: inv.payment_terms ?? '',
    subtotal: num(inv.subtotal),
    discount_amount: num(inv.discount_amount),
    tax_amount: num(inv.tax_amount),
    shipping_amount: num(inv.shipping_amount),
    total_amount: num(inv.total_amount),
    amount_paid: num(inv.amount_paid),
    amount_due: num(inv.amount_due),
    is_overdue: Boolean(inv.is_overdue),
    is_settled: SETTLED.includes(inv.status),
    // Real reminder state, settings and counters, no fabricated log.
    reminder_enabled: Boolean(inv.reminder_enabled),
    reminder_frequency: inv.reminder_frequency ?? null,
    reminder_count: inv.reminder_count ?? 0,
    last_reminder_sent: inv.last_reminder_sent ?? null,
    notes: inv.notes ?? '',
    terms: inv.terms ?? ''
  };
}

/**
 * One currency's header amounts, as numbers.
 *
 * @param {any} row
 */
const moneyTotals = (row) => ({
  outstanding: num(row.outstanding),
  overdue: num(row.overdue),
  due_this_month: num(row.due_this_month),
  paid_this_quarter: num(row.paid_this_quarter),
  draft: num(row.draft)
});

/**
 * The invoice list, most-overdue first.
 *
 * `totals` is aggregated by the API over the whole visible queryset. The
 * requester's, so an admin's pills cover the org and a member's cover the
 * invoices they made or were assigned. The rows follow the same rule, which is
 * the point: v1 summed the loaded page and the pills disagreed with the list.
 *
 * The money arrives per currency (`totals.by_currency`): every invoice carries
 * its own currency and there are no exchange rates, so the API never adds two
 * currencies together and neither does this. `byCurrency[code]` holds one
 * currency's figures, `currencies` lists the codes, and `blank` is the
 * all-zero view for a list with nothing in it. The counts have no currency.
 *
 * `params` (built from FILTER_FIELDS) narrows `invoices` but NOT `totals`.
 * `InvoiceListView.get` (`backend/invoices/api_views.py:177-193`) computes
 * `_totals` over `base`, the role-scoped queryset, deliberately BEFORE
 * `filter_queryset` runs, with a comment reading "not just this page or the
 * current filter". So applying a Status or Account filter changes the rows
 * this function returns and leaves every number in `totals` exactly as it
 * was. `invoices/+page.svelte` does not claim otherwise: unlike
 * tasks/tickets/pipeline/accounts/estimates, it has no "these numbers
 * describe the filtered list" line, because for this endpoint that sentence
 * would be false.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {URLSearchParams} [params]
 */
export async function listInvoices({ cookies }, params) {
  const query = new URLSearchParams(params ?? undefined);
  if (!query.has('limit')) query.set('limit', '100');
  // Most overdue first; the backend understands due_date sort.
  if (!query.has('sort')) query.set('sort', 'due_date');

  const response = await apiRequest(`/invoices/?${query.toString()}`, {}, { cookies });
  const totals = response.totals ?? {};
  const rows = totals.by_currency ?? [];
  return {
    invoices: (response.results ?? []).map(toRow),
    totals: {
      count: totals.count ?? response.count ?? 0,
      action_needed: totals.action_needed ?? 0,
      currencies: rows.map((/** @type {any} */ r) => r.currency),
      byCurrency: Object.fromEntries(
        rows.map((/** @type {any} */ r) => [r.currency, moneyTotals(r)])
      ),
      blank: moneyTotals({})
    }
  };
}

/**
 * One invoice, with its line items.
 *
 * A 404 from the API (the record does not exist, or belongs to another org)
 * becomes a 404 here. A 403 (a member who is neither creator nor assignee)
 * also surfaces as "not yours" rather than leaking that the invoice exists.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function getInvoice({ cookies }, id) {
  let response;
  try {
    response = await apiRequest(`/invoices/${id}/`, {}, { cookies });
  } catch (/** @type {any} */ err) {
    if (err?.status === 404 || err?.status === 403) {
      error(404, 'That invoice does not exist, or it belongs to another team.');
    }
    throw err;
  }
  const invoice = response.invoice ?? {};
  return {
    invoice: toDetail(invoice),
    lineItems: (invoice.line_items ?? []).map(toLineItem)
  };
}

/**
 * The lifecycle writes. Each is a bare POST through `get_invoice_or_error`, so
 * the API, not this layer, decides who may act and refuses the rest.
 */

/** Draft -> Sent (and mails the client). 400 on a Paid/Cancelled invoice. */
export async function sendInvoice({ cookies }, id) {
  return apiRequest(`/invoices/${id}/send/`, { method: 'POST', body: {} }, { cookies });
}

/**
 * Record a payment. With no amount the API settles the whole balance; an
 * explicit amount takes a partial payment (validated server-side against the
 * outstanding total).
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {{ amount?: string, payment_method?: string, reference_number?: string }} [payment]
 */
export async function markInvoicePaid({ cookies }, id, payment = {}) {
  /** @type {Record<string, unknown>} */
  const body = { payment_method: payment.payment_method || 'OTHER' };
  if (payment.amount) body.amount = payment.amount;
  if (payment.reference_number) body.reference_number = payment.reference_number;
  return apiRequest(`/invoices/${id}/mark-paid/`, { method: 'POST', body }, { cookies });
}

/** Cancel it. 400 if already Cancelled, or if Paid (settled money stays). */
export async function cancelInvoice({ cookies }, id) {
  return apiRequest(`/invoices/${id}/cancel/`, { method: 'POST', body: {} }, { cookies });
}

/** Copy to a fresh Draft; returns the new invoice so the caller can open it. */
export async function duplicateInvoice({ cookies }, id) {
  return apiRequest(`/invoices/${id}/duplicate/`, { method: 'POST', body: {} }, { cookies });
}

/**
 * Create an invoice from the builder. The body is already the API shape
 * (account_id/contact_id required, line_items nested). The server owns the parts
 * that must not be forged: org and created_by (from the JWT/profile), the
 * invoice number, the public token, and every total (recomputed from the line
 * items). `status` is server-forced to Draft; sending it is ignored. The 201
 * wraps the record as `{ invoice }`, so unwrap it for the caller's redirect.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {Record<string, any>} body
 */
export async function createInvoice({ cookies }, body) {
  const response = await apiRequest('/invoices/', { method: 'POST', body }, { cookies });
  return response.invoice ?? response;
}

/**
 * Raise a Draft invoice from a won deal, copying its line items. The API
 * decides everything: a deal the caller may not open answers 404, one not in
 * a won stage or with no line items answers 400 with a sentence worth
 * showing. Returns the new invoice so the caller can open it.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} dealId
 */
export async function invoiceFromDeal({ cookies }, dealId) {
  const response = await apiRequest(
    `/invoices/from-opportunity/${dealId}/`,
    { method: 'POST', body: {} },
    { cookies }
  );
  return response.invoice ?? response;
}
