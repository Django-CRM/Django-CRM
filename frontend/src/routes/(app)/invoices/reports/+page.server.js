import { listReports } from '$lib/server/v2/reports.js';

/**
 * The invoice reports dashboard. `load` returns `{ can_view, invoice_count,
 * average_days_to_pay, window_months, currencies, byCurrency, blank }`, where
 * each `byCurrency[code]` holds `{ dashboard, revenue, aging, overdueByAccount }`
 * in that one currency. The exact fields the page reads. Reports are
 * admin-only; for a non-admin `can_view` is false and the figures are an empty,
 * valid shape, so the page shows its "admins only" state instead of numbers.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies }) {
  return await listReports({ cookies });
}
