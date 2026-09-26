/**
 * The API query each list page sends, built from the page's own URL.
 *
 * One function per list, called by that page's `load` and by its CSV export
 * proxy (`routes/api/<module>/export/+server.js`). Two callers, one function:
 * a filter added to the page reaches the downloaded file without anyone
 * remembering to add it twice, and a parameter the page would drop is dropped
 * from the file too. Only what the page itself forwards ever reaches Django.
 *
 * Server-only, like `filter-params.js` which it builds on.
 */
import { readFilters, buildFilterQuery } from '$lib/server/v2/filter-params.js';
import { FILTER_FIELDS as LEAD_FIELDS } from '$lib/server/v2/leads.js';
import { FILTER_FIELDS as CONTACT_FIELDS } from '$lib/server/v2/contacts.js';
import { FILTER_FIELDS as ACCOUNT_FIELDS } from '$lib/server/v2/accounts.js';
import { FILTER_FIELDS as DEAL_FIELDS, BOARD_FIELDS } from '$lib/server/v2/deals.js';
import { FILTER_FIELDS as TICKET_FIELDS, OPEN_STATUSES } from '$lib/server/v2/tickets.js';
import { FILTER_FIELDS as INVOICE_FIELDS } from '$lib/server/v2/invoices.js';

/**
 * @param {URLSearchParams} params
 * @param {URL} url
 * @param {string[]} keys plain params copied from the URL when present
 */
function copyKeys(params, url, keys) {
  for (const key of keys) {
    const value = url.searchParams.get(key);
    if (value) params.set(key, value);
  }
  return params;
}

/** @param {URL} url */
export function leadListQuery(url) {
  return copyKeys(buildFilterQuery(LEAD_FIELDS, readFilters(url, 'leads')), url, [
    'search',
    'rating',
    'limit'
  ]);
}

/**
 * Active contacts unless the page was asked for inactive ones too, which is
 * the `?inactive=1` preset rather than a filter field.
 *
 * @param {URL} url
 */
export function contactListQuery(url) {
  const params = copyKeys(buildFilterQuery(CONTACT_FIELDS, readFilters(url, 'contacts')), url, [
    'search',
    'name',
    'email',
    'phone',
    'limit'
  ]);
  if (url.searchParams.get('inactive') !== '1') params.set('is_active', 'true');
  return params;
}

/** @param {URL} url */
export function accountListQuery(url) {
  return copyKeys(buildFilterQuery(ACCOUNT_FIELDS, readFilters(url, 'accounts')), url, [
    'search',
    'name',
    'limit'
  ]);
}

/**
 * The open queue unless a status was picked or `?all=1` asked for everything.
 *
 * @param {URL} url
 */
export function ticketListQuery(url) {
  const params = copyKeys(buildFilterQuery(TICKET_FIELDS, readFilters(url, 'tickets')), url, [
    'search',
    'limit'
  ]);
  const status = url.searchParams.get('status') ?? '';
  if (status) {
    params.set('status', status);
  } else if (url.searchParams.get('all') !== '1') {
    for (const open of OPEN_STATUSES) params.append('status', open);
  }
  return params;
}

/**
 * Most overdue first, the order `listInvoices` asks for, stated here so the
 * export comes out in the order the page shows.
 *
 * @param {URL} url
 */
export function invoiceListQuery(url) {
  const params = buildFilterQuery(INVOICE_FIELDS, readFilters(url, 'invoices'));
  params.set('sort', 'due_date');
  return params;
}

/**
 * The deal list or board query. The board and the list read different
 * filters, and the board always shows one pipeline (the default when none is
 * picked) and open stages only, so the pipelines are needed to build it.
 *
 * `search` is the one non-field param the board can also run
 * (`kanban_views.py`). `limit`, `open` and `rotten` are list-only presets that
 * `readFilters` does not carry. `?pipeline=` is forwarded only when the org
 * has that pipeline.
 *
 * @param {URL} url
 * @param {Array<{ id: string, is_default?: boolean }>} pipelines
 */
export function dealListQuery(url, pipelines) {
  const boardMode = url.searchParams.get('view') === 'board';
  const params = copyKeys(
    buildFilterQuery(boardMode ? BOARD_FIELDS : DEAL_FIELDS, readFilters(url, 'pipeline')),
    url,
    boardMode ? ['search'] : ['search', 'limit', 'open', 'rotten']
  );

  const wanted = url.searchParams.get('pipeline');
  const picked = pipelines.find((p) => p.id === wanted) ?? null;
  const boardPipeline = picked ?? pipelines.find((p) => p.is_default) ?? pipelines[0] ?? null;
  if (picked) params.set('pipeline', picked.id);
  else if (boardMode && boardPipeline) params.set('pipeline', boardPipeline.id);
  if (boardMode) params.set('open', 'true');

  return { params, boardMode, picked, boardPipeline };
}
