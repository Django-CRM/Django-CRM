/**
 * CSV export of the accounts list page: `forwardCsvExport` has the why.
 * The query is rebuilt from the page's own URL by `accountListQuery`, the same
 * function the page's `load` uses.
 *
 * `is_active=true` because the page lists only active accounts: it reads the
 * API's `active_accounts` half and shows the inactive one as a count. The
 * export has no halves, so it is told which one the page shows.
 */
import { forwardCsvExport } from '$lib/server/v2/csv-export.js';
import { accountListQuery } from '$lib/server/v2/list-queries.js';

/** @type {import('./$types').RequestHandler} */
export function GET(event) {
  const params = accountListQuery(event.url);
  params.set('is_active', 'true');
  return forwardCsvExport(event, '/accounts/export/', params);
}
