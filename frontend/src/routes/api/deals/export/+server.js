/**
 * CSV export of the pipeline page, list or board: `forwardCsvExport` has the
 * why. The query is rebuilt from the page's own URL by `dealListQuery`, the
 * same function the page's `load` uses, which needs the org's pipelines to
 * resolve the one the board shows.
 */
import { forwardCsvExport } from '$lib/server/v2/csv-export.js';
import { listPipelines } from '$lib/server/v2/deals.js';
import { dealListQuery } from '$lib/server/v2/list-queries.js';

/** @type {import('./$types').RequestHandler} */
export async function GET(event) {
  const { params } = dealListQuery(event.url, await listPipelines(event.cookies));
  return forwardCsvExport(event, '/opportunities/export/', params);
}
