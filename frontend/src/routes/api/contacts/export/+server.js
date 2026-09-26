/**
 * CSV export of the contacts list page: `forwardCsvExport` has the why.
 * The query is rebuilt from the page's own URL by `contactListQuery`,
 * the same function the page's `load` uses.
 */
import { forwardCsvExport } from '$lib/server/v2/csv-export.js';
import { contactListQuery } from '$lib/server/v2/list-queries.js';

/** @type {import('./$types').RequestHandler} */
export function GET(event) {
  return forwardCsvExport(event, '/contacts/export/', contactListQuery(event.url));
}
