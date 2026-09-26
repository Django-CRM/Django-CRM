/**
 * Invoice reports: the seventeenth v2 module, fourth of the invoices sub-pages.
 *
 * Server-only, like the sixteen before it. This one fans three endpoints,
 * `/invoices/reports/{dashboard,revenue,aging}/`: into the single shape the
 * "Where the money is" page reads.
 *
 * WHY THIS IS ADMIN-ONLY
 * These are org-wide financial roll-ups: total invoiced and collected, revenue
 * by month, AR aging, and who owes what across every account. The invoice and
 * estimate *lists* scope a non-admin to their own records; a report that showed
 * a rep the whole org's revenue and receivables would leak exactly what those
 * scopes protect. The three endpoints are admin-only on the backend, and this
 * layer surfaces a `can_view` flag so the page can say so plainly rather than
 * rendering a broken dashboard. The backend is still the one enforcing it.
 *
 * WHAT THE PAGE NEEDED THAT THE ENDPOINTS DID NOT HAVE
 * The dashboard gained `average_days_to_pay` and `invoice_count`; the revenue
 * report gained an `invoiced` series alongside `revenue` (paid), the two counted
 * on different dates on purpose; the aging report gained a `by_account` roll-up
 * (the "who owes it" table. The capped per-bucket invoice lists could not give
 * it). All added to the real endpoints, not faked here.
 *
 * ONE CURRENCY AT A TIME
 * Each invoice carries its own currency and there is no exchange-rate data, so
 * the endpoints never add two currencies together: every money figure arrives
 * as a `by_currency` list. This layer turns those lists into one complete view
 * per currency (`byCurrency[code]`), and the page shows one of them, with a
 * switcher only when there is more than one. A single-currency org sees one
 * view, exactly as before.
 */
import { apiRequest } from '$lib/api-helpers.js';

const num = (/** @type {any} */ value) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
};

/** The header window; the revenue call asks for the trailing 12 months. */
const WINDOW_MONTHS = 12;

/** The overdue buckets, in the order the page draws them. */
const BUCKETS = [
  { key: '1_30', label: '1-30 days', field: '1_30_days' },
  { key: '31_60', label: '31-60 days', field: '31_60_days' },
  { key: '61_90', label: '61-90 days', field: '61_90_days' },
  { key: '90_plus', label: 'Over 90 days', field: 'over_90_days' }
];

/**
 * The entry for `code` in a block's `by_currency` list, or `{}` when that
 * currency has nothing in this block.
 *
 * @param {any} block
 * @param {string} code
 */
const entry = (block, code) =>
  (block?.by_currency ?? []).find((/** @type {any} */ r) => r.currency === code) ?? {};

/**
 * Every figure the page draws, in one currency. With no `code` (and no data)
 * it is the all-zero view, so a non-admin or empty-org render never throws.
 *
 * @param {any} dashboard
 * @param {any} revenue
 * @param {any} aging
 * @param {string} code
 */
function view(dashboard, revenue, aging, code) {
  const summary = entry(dashboard?.summary, code);
  const overdue = entry(aging?.overdue, code);
  return {
    dashboard: {
      total_invoiced: num(summary.total_invoiced),
      total_paid: num(summary.total_paid),
      // The overdue stat is sourced from the aging report so the number over the
      // chart is the same number the chart draws.
      overdue_amount: num(overdue.amount)
    },
    // Only the months that had money in this currency, as the endpoint only
    // returns months that had money at all.
    revenue: (revenue?.data ?? [])
      .map((/** @type {any} */ r) => ({ period: r.period, figures: entry(r, code) }))
      .filter((/** @type {any} */ r) => r.figures.currency === code)
      .map((/** @type {any} */ r) => ({
        period: r.period,
        invoiced: num(r.figures.invoiced),
        paid: num(r.figures.revenue)
      })),
    aging: {
      not_yet_due: {
        amount: num(entry(aging?.current, code).amount),
        count: entry(aging?.current, code).count ?? 0
      },
      buckets: BUCKETS.map(({ key, label, field }) => ({
        key,
        label,
        amount: num(entry(aging?.[field], code).amount),
        count: entry(aging?.[field], code).count ?? 0
      })),
      overdue_count: overdue.count ?? 0
    },
    overdueByAccount: (aging?.by_account ?? [])
      .filter((/** @type {any} */ a) => a.currency === code)
      .map((/** @type {any} */ a) => ({
        id: a.id,
        name: a.name,
        count: a.count,
        oldest_days: a.oldest_days,
        amount: num(a.amount)
      }))
  };
}

/** A fully-shaped but empty report, so a non-admin render never throws. */
const EMPTY = {
  window_months: WINDOW_MONTHS,
  invoice_count: 0,
  average_days_to_pay: 0,
  currencies: /** @type {string[]} */ ([]),
  byCurrency: /** @type {Record<string, ReturnType<typeof view>>} */ ({}),
  blank: view(null, null, null, '')
};

/**
 * The invoice reports, shaped for the dashboard page. Admin-only: a non-admin
 * gets `{ can_view: false }` and an empty-but-valid shape, so the page shows the
 * "admins only" state without the deriveds tripping over missing data.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function listReports({ cookies }) {
  let dashboard;
  let revenue;
  let aging;
  try {
    [dashboard, revenue, aging] = await Promise.all([
      apiRequest('/invoices/reports/dashboard/', {}, { cookies }),
      apiRequest('/invoices/reports/revenue/?group_by=month', {}, { cookies }),
      apiRequest('/invoices/reports/aging/', {}, { cookies })
    ]);
  } catch (/** @type {any} */ err) {
    if (err?.status === 403) return { can_view: false, ...EMPTY };
    throw err;
  }

  // The summary covers every invoice in the org, so its currencies include
  // every currency the revenue and aging reports can mention. The backend
  // orders them by code.
  const currencies = (dashboard.summary?.by_currency ?? []).map(
    (/** @type {any} */ r) => r.currency
  );

  return {
    ...EMPTY,
    can_view: true,
    invoice_count: dashboard.invoice_count ?? 0,
    average_days_to_pay: dashboard.average_days_to_pay ?? 0,
    currencies,
    byCurrency: Object.fromEntries(
      currencies.map((code) => [code, view(dashboard, revenue, aging, code)])
    )
  };
}
