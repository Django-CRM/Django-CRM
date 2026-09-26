/**
 * CSV export of the leads list page: `forwardCsvExport` has the why.
 * The query is rebuilt from the page's own URL by `leadListQuery`, the same
 * function the page's `load` uses.
 *
 * `open=true` because the page lists only open leads: it reads the API's
 * `open_leads` half and never `close_leads`. The export has no halves, so it
 * is told which one the page shows.
 */
import { forwardCsvExport } from '$lib/server/v2/csv-export.js';
import { leadListQuery } from '$lib/server/v2/list-queries.js';

/** @type {import('./$types').RequestHandler} */
export function GET(event) {
  const params = leadListQuery(event.url);
  params.set('open', 'true');
  return forwardCsvExport(event, '/leads/export/', params);
}
