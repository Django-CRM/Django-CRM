import { fail } from '@sveltejs/kit';
import {
  listBoard,
  listDeals,
  moveDeal,
  FILTER_FIELDS,
  BOARD_FIELDS,
  BOARD_PRESETS
} from '$lib/server/v2/deals.js';
import { readableError } from '$lib/server/v2/form-errors.js';
import { readFilters, buildFilterQuery } from '$lib/server/v2/filter-params.js';
import { getOrgPeopleAndTeams, resolveMe } from '$lib/server/v2/org-people.js';
import { getTags } from '$lib/server/v2/tags.js';

/**
 * The list and the board are two different queries, not two renderings of one
 * result: the table is paginated and the board is grouped and capped per
 * column. Only the one being shown is fetched.
 *
 * `view` is read and forwarded untouched. It is the board/list layout toggle,
 * not a filter, and must survive every preset and chip link the same way it
 * did before this page grew a filter bar.
 *
 * `open` and `rotten` are preset-only params, the same shape as `all` on
 * /tickets and /tasks: `?open=true` narrows to the working pipeline and
 * `?rotten=true` to the stalled subset (`opportunity_views.py:183,186`), and
 * neither is one of the descriptor's `fields`, only its `presets`, so
 * `readFilters` does not carry them and this load reads them off the URL
 * itself. Neither is offered on the board (see BOARD_PRESETS below): the
 * kanban endpoint does not read either one.
 *
 * `BOARD_FIELDS` and `BOARD_PRESETS` say what the board can actually run, and
 * live in `deals.js` rather than here. A `+page.server.js` may export only a
 * fixed set of names, and SvelteKit answers 500 for the whole route on any
 * other one.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load(event) {
  const { cookies, url, locals } = event;
  const view = url.searchParams.get('view') === 'board' ? 'board' : 'list';
  const boardMode = view === 'board';

  const filters = readFilters(url, 'pipeline');
  const params = buildFilterQuery(boardMode ? BOARD_FIELDS : FILTER_FIELDS, filters);
  // `search` is the one non-field param the board can also run
  // (`kanban_views.py:123`). `limit` and `rotten` are list-only: the board is
  // not paginated the same way, and `rotten` is never offered while
  // `boardMode` is true (BOARD_PRESETS excludes it). `open` is not forwarded
  // from the URL either, but for the opposite reason: board mode sets it
  // unconditionally below, because the board can only ever show open stages.
  const extraKeys = boardMode ? ['search'] : ['search', 'limit', 'open', 'rotten'];
  for (const key of extraKeys) {
    const value = url.searchParams.get(key);
    if (value) params.set(key, value);
  }

  const [orgPeople, tagList] = await Promise.all([
    getOrgPeopleAndTeams(cookies),
    // A failed tag fetch should cost the Tag dropdown in the filter bar, not
    // the whole pipeline. Same pattern as tickets.js and leads.js.
    getTags({ cookies }).catch(() => ({ tags: [] }))
  ]);
  const shared = {
    people: orgPeople.people,
    tags: tagList.tags ?? [],
    meId: resolveMe(orgPeople.people, /** @type {any} */ (locals).user?.email),
    // Handed to the page even in list mode, so the List<->Board toggle can
    // build a board-safe link without a `.svelte` file importing anything
    // from this `.server.js` module (SvelteKit forbids that, the same
    // protection as `$lib/server/`); the toggle just needs to know which of
    // the CURRENT params survive the switch.
    boardFields: BOARD_FIELDS
  };

  if (boardMode) {
    // The board renders open stages only: `listBoard` drops every `CLOSED_*`
    // column (`deals.js`). So the honest total beside it counts open deals
    // too. Without this, the default preset's header would add up won and
    // lost deals that no lane on screen displays, which is the same
    // number-disagrees-with-rows defect BOARD_FIELDS exists to prevent, just
    // arriving by a different route.
    params.set('open', 'true');
    const { lanes } = await listBoard(event, params);
    // Same param set the board itself just ran (see BOARD_FIELDS above), not
    // the full FILTER_FIELDS set: a header total narrowed by a filter the
    // board ignored would disagree with the cards under it, the exact
    // mismatch this restriction exists to remove.
    const { totals } = await listDeals(event, new URLSearchParams(params));
    return {
      view,
      lanes,
      totals,
      deals: [],
      onlyFields: BOARD_FIELDS,
      onlyPresets: BOARD_PRESETS,
      ...shared
    };
  }

  const { results, totals } = await listDeals(event, params);
  return {
    view,
    deals: results,
    totals,
    lanes: [],
    onlyFields: undefined,
    onlyPresets: undefined,
    ...shared
  };
}

export const actions = {
  /**
   * Move a card to another lane, or to a position inside one.
   *
   * The board reorders optimistically and calls this without waiting, so the
   * only job here is to persist and to report a refusal in words the card can
   * show while it snaps back. A 403 stays a 403: the move endpoint refuses a
   * deal the caller neither created nor is assigned to, and flattening that to
   * 400 would read as bad input rather than as a permission answer.
   */
  move: async ({ request, cookies }) => {
    const form = await request.formData();
    const id = String(form.get('id') || '');
    const columnId = String(form.get('column_id') || '');
    const aboveId = String(form.get('above_id') || '');
    const belowId = String(form.get('below_id') || '');
    if (!id || !columnId) return fail(400, { error: 'Missing card or column.' });
    try {
      await moveDeal({ cookies }, id, { columnId, aboveId, belowId });
      return { success: true };
    } catch (err) {
      const status = /** @type {any} */ (err)?.status === 403 ? 403 : 400;
      return fail(status, { error: readableError(err, 'Could not move the deal.') });
    }
  }
};
