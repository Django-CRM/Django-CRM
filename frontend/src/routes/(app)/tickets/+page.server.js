import { fail } from '@sveltejs/kit';
import {
  listTickets,
  OPEN_STATUSES,
  FILTER_FIELDS,
  bulkUpdateTickets,
  bulkDeleteTickets,
  summarizeBulk
} from '$lib/server/v2/tickets.js';
import { readFilters, buildFilterQuery } from '$lib/server/v2/filter-params.js';
import { getOrgPeopleAndTeams, resolveMe } from '$lib/server/v2/org-people.js';
import { getTags } from '$lib/server/v2/tags.js';
import { parseBulkForm } from '$lib/server/v2/bulk-form.js';

/**
 * Only filters the API actually applies are forwarded. A parameter that
 * changes the URL and nothing else teaches people the filter bar is decorative.
 *
 * The queue defaults to open tickets. `status` is repeatable and the API
 * switches to `status__in` when more than one arrives, so "open" is three
 * values rather than a fourth definition of the word.
 *
 * The pickers are fetched here rather than inside `listTickets` because
 * `getSettingsHub` calls read functions for their totals alone, and a picker
 * fetch folded into one costs a redundant request on every hub load.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, url, locals }) {
  const params = buildFilterQuery(FILTER_FIELDS, readFilters(url, 'tickets'));

  const search = url.searchParams.get('search');
  if (search) params.set('search', search);
  const limit = url.searchParams.get('limit');
  if (limit) params.set('limit', limit);

  const status = url.searchParams.get('status') ?? '';
  const showAll = url.searchParams.get('all') === '1';
  if (status) {
    params.set('status', status);
  } else if (!showAll) {
    for (const open of OPEN_STATUSES) params.append('status', open);
  }

  const [{ results, totals }, orgPeople, tagList] = await Promise.all([
    listTickets({ cookies }, params),
    getOrgPeopleAndTeams(cookies),
    // getTags has no fallback of its own: on /settings/tags a failed fetch is
    // meant to surface as an error. Here the tag list is just one picker in
    // the filter bar, so the degradation belongs to this caller, not to the
    // shared function. Losing the picker should cost the Tag dropdown, not
    // the whole queue.
    getTags({ cookies }).catch(() => ({ tags: [] }))
  ]);

  return {
    tickets: results,
    totals,
    showAll,
    status,
    search: params.get('search') ?? '',
    priority: params.get('priority') ?? '',
    people: orgPeople.people,
    tags: tagList.tags ?? [],
    meId: resolveMe(orgPeople.people, /** @type {any} */ (locals).user?.email)
  };
}

export const actions = {
  bulkUpdate: async ({ request, cookies }) => {
    const { ids, fields } = parseBulkForm(await request.formData());
    if (ids.length === 0) return fail(400, { message: 'Select at least one ticket.' });
    const res = await bulkUpdateTickets({ cookies }, ids, fields);
    return { ok: true, kind: 'update', summary: summarizeBulk(res.results) };
  },
  bulkDelete: async ({ request, cookies }) => {
    const ids = (await request.formData()).getAll('ids').map(String);
    if (ids.length === 0) return fail(400, { message: 'Select at least one ticket.' });
    const res = await bulkDeleteTickets({ cookies }, ids);
    return { ok: true, kind: 'delete', summary: summarizeBulk(res.results) };
  }
};
