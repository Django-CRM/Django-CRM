/**
 * Saved views (G29), the server half: read a person's views for one list, and
 * the three form actions (save, rename, delete) every list page spreads into
 * its own `actions`.
 *
 * It never gates. `/api/saved-views/` answers only the caller's own views
 * (another person's id is a 404) and checks every filter against what the list
 * reads, so a view built here from a hand-edited URL is refused there with a
 * 400 whose sentence is shown in the menu.
 *
 * Every action redirects back to the page URL it was posted from. The action
 * URL (`?/saveView`) replaces the query string, so without the redirect a
 * browser without JavaScript would land on the unfiltered list; with it, both
 * paths end where they started. The query comes back from a hidden input and
 * is rebuilt through `URLSearchParams` onto this page's own path, so it cannot
 * send anyone anywhere else.
 */
import { fail, redirect } from '@sveltejs/kit';
import { apiRequest } from '$lib/api-helpers.js';
import { pickFilters } from '$lib/v2/saved-views.js';
import { readableError } from './form-errors.js';
import { FILTER_FIELDS as LEAD_FIELDS } from './leads.js';
import { FILTER_FIELDS as CONTACT_FIELDS } from './contacts.js';
import { FILTER_FIELDS as ACCOUNT_FIELDS } from './accounts.js';
import { FILTER_FIELDS as DEAL_FIELDS, BOARD_FIELDS } from './deals.js';
import { FILTER_FIELDS as TICKET_FIELDS, OPEN_STATUSES } from './tickets.js';
import { FILTER_FIELDS as INVOICE_FIELDS } from './invoices.js';
import { FILTERS } from '$lib/v2/filters.js';

/**
 * Per API list, the URL parameters its page forwards to the list under the
 * same name: the filter-bar fields plus the plain params `list-queries.js`
 * copies. Paging and the page's own presets (`inactive`, `all`, `view`) are
 * not filters and are not saved. `saved-views.test.js` checks every key here
 * really is forwarded by the page's list query.
 */
export const SAVED_VIEW_KEYS = {
  leads: [...LEAD_FIELDS, 'search', 'rating'],
  contacts: [...CONTACT_FIELDS, 'search', 'name', 'email', 'phone'],
  accounts: [...ACCOUNT_FIELDS, 'search', 'name'],
  opportunities: [...DEAL_FIELDS, 'search', 'open', 'rotten', 'pipeline'],
  cases: [...TICKET_FIELDS, 'search'],
  invoices: [...INVOICE_FIELDS]
};

/** The filter-bar page each API list is drawn on. */
const PAGE = {
  leads: 'leads',
  contacts: 'contacts',
  accounts: 'accounts',
  opportunities: 'pipeline',
  cases: 'tickets',
  invoices: 'invoices'
};

/**
 * How one page's URL maps onto a view (the `Spec` in `$lib/v2/saved-views.js`),
 * for the URL it is showing.
 *
 * `multi` is read off the filter descriptor, the same flag `readFilters` obeys,
 * so the page forwards every value of exactly these keys. The pipeline board
 * runs a narrower query than the list (`BOARD_FIELDS`), so on the board only
 * that query's keys are saved or applied, and `view` is kept so opening a
 * view does not drop the board for the list.
 *
 * @param {keyof typeof SAVED_VIEW_KEYS} module
 * @param {URLSearchParams} params the page's query string
 * @returns {import('$lib/v2/saved-views.js').Spec}
 */
export function savedViewSpec(module, params) {
  const multi = FILTERS[PAGE[module]].fields
    .filter((/** @type {any} */ f) => f.multi)
    .map((/** @type {any} */ f) => f.key);
  if (module === 'opportunities') {
    const board = params.get('view') === 'board';
    return {
      keys: board ? [...BOARD_FIELDS, 'search', 'pipeline'] : SAVED_VIEW_KEYS.opportunities,
      multi,
      keep: ['view']
    };
  }
  if (module === 'cases') {
    return {
      keys: SAVED_VIEW_KEYS.cases,
      multi,
      implicit: { key: 'status', values: OPEN_STATUSES, allParam: 'all' }
    };
  }
  return { keys: SAVED_VIEW_KEYS[module], multi };
}

/**
 * The first message in a DRF rejection. The saved-views API writes each one as
 * a whole sentence (`{"name": ["You already have a view with this name for
 * this list."]}`), so the field name in front of it would only be noise.
 *
 * @param {any} body
 * @returns {string | null}
 */
function firstSentence(body) {
  const first = body && typeof body === 'object' ? Object.values(body)[0] : null;
  const text = Array.isArray(first) ? first[0] : first;
  return typeof text === 'string' && text.trim() ? text : null;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * The caller's views for one list, and the page's spec, for its `load`.
 *
 * A failed fetch costs the saved-views menu, not the list under it: the same
 * degradation the tag picker gets on these pages.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies, url: URL }} event
 * @param {keyof typeof SAVED_VIEW_KEYS} module
 */
export async function loadSavedViews({ cookies, url }, module) {
  const spec = savedViewSpec(module, url.searchParams);
  try {
    const resp = await apiRequest(`/saved-views/?module=${module}`, {}, { cookies });
    return { spec, views: resp.saved_views ?? [], limit: resp.limit ?? null };
  } catch {
    return { spec, views: [], limit: null };
  }
}

/**
 * The page URL a saved-view action returns to.
 *
 * @param {URL} url the action's URL; its path is the list page's
 * @param {FormData} form
 */
function backTo(url, form) {
  const qs = new URLSearchParams(String(form.get('query') ?? '')).toString();
  return qs ? `${url.pathname}?${qs}` : url.pathname;
}

/**
 * `saveView`, `renameView` and `deleteView` for one list page.
 *
 * @param {keyof typeof SAVED_VIEW_KEYS} module
 */
export function savedViewActions(module) {
  /**
   * @param {import('@sveltejs/kit').RequestEvent} event
   * @param {(form: FormData) => { path: string, method: string, body?: any } | string} build
   *   the API call, or a sentence refusing it
   * @param {string} fallback
   */
  async function run({ request, cookies, url }, build, fallback) {
    const form = await request.formData();
    const call = build(form);
    if (typeof call === 'string') return fail(400, { savedViewError: call });
    try {
      await apiRequest(call.path, { method: call.method, body: call.body }, { cookies });
    } catch (err) {
      const status = /** @type {any} */ (err)?.status;
      if (!status) throw err;
      return fail(status === 404 ? 404 : 400, {
        savedViewError:
          status === 404
            ? 'That view no longer exists.'
            : (firstSentence(/** @type {any} */ (err).body) ?? readableError(err, fallback))
      });
    }
    redirect(303, backTo(url, form));
  }

  /** @param {FormData} form */
  const viewId = (form) => {
    const id = String(form.get('id') ?? '');
    return UUID.test(id) ? id : null;
  };

  return {
    /** @param {import('@sveltejs/kit').RequestEvent} event */
    saveView: (event) =>
      run(
        event,
        (form) => {
          const name = String(form.get('name') ?? '').trim();
          if (!name) return 'Give the view a name.';
          const query = new URLSearchParams(String(form.get('query') ?? ''));
          const filters = pickFilters(query, savedViewSpec(module, query));
          return { path: '/saved-views/', method: 'POST', body: { module, name, filters } };
        },
        'Could not save the view.'
      ),

    /** @param {import('@sveltejs/kit').RequestEvent} event */
    renameView: (event) =>
      run(
        event,
        (form) => {
          const id = viewId(form);
          const name = String(form.get('name') ?? '').trim();
          if (!id) return 'Which view?';
          if (!name) return 'Give the view a name.';
          return { path: `/saved-views/${id}/`, method: 'PATCH', body: { name } };
        },
        'Could not rename the view.'
      ),

    /** @param {import('@sveltejs/kit').RequestEvent} event */
    deleteView: (event) =>
      run(
        event,
        (form) => {
          const id = viewId(form);
          if (!id) return 'Which view?';
          return { path: `/saved-views/${id}/`, method: 'DELETE' };
        },
        'Could not delete the view.'
      )
  };
}
