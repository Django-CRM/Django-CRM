/**
 * CSV export of the tickets list page: `forwardCsvExport` has the why.
 * The query is rebuilt from the page's own URL by `ticketListQuery`,
 * the same function the page's `load` uses.
 */
import { forwardCsvExport } from '$lib/server/v2/csv-export.js';
import { ticketListQuery } from '$lib/server/v2/list-queries.js';

/** @type {import('./$types').RequestHandler} */
export function GET(event) {
  return forwardCsvExport(event, '/cases/export/', ticketListQuery(event.url));
}
