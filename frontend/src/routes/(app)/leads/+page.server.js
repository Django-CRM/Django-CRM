import { listLeads } from '$lib/server/v2/leads.js';
import { leadListQuery } from '$lib/server/v2/list-queries.js';
import { getOrgPeopleAndTeams, resolveMe } from '$lib/server/v2/org-people.js';
import { getTags } from '$lib/server/v2/tags.js';
import { forwardCsvImport } from '$lib/server/v2/csv-import.js';

/**
 * Server load, not a universal one. The access token is an httpOnly cookie;
 * a `+page.js` would have to run this fetch in the browser too, where that
 * cookie is deliberately unreadable.
 *
 * Only filters the API actually applies are forwarded. A parameter that
 * changes the URL and nothing else teaches people the filter bar is decorative.
 *
 * The pickers are fetched here rather than inside `listLeads` for the same
 * reason as `tickets.js`: a picker fetch folded into the list read would cost
 * a redundant request on every caller that reads leads for their totals alone.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, url, locals }) {
  const params = leadListQuery(url);

  const [{ results, totals }, orgPeople, tagList] = await Promise.all([
    listLeads({ cookies }, params),
    getOrgPeopleAndTeams(cookies),
    // A failed tag fetch should cost the Tag dropdown in the filter bar, not
    // the whole list. Follows the tickets.js pattern; see the note there.
    getTags({ cookies }).catch(() => ({ tags: [] }))
  ]);

  return {
    // `listLeads` returns `{ results, totals }`; the page reads `data.leads`,
    // so this renames rather than spreading `list` verbatim.
    leads: results,
    totals,
    people: orgPeople.people,
    tags: tagList.tags ?? [],
    meId: resolveMe(orgPeople.people, /** @type {any} */ (locals).user?.email)
  };
}

/** The CSV import drawer's two steps; see `forwardCsvImport`. */
export const actions = {
  importPreview: (event) => forwardCsvImport(event, '/leads/import/preview/', 'importPreview'),
  importCommit: (event) => forwardCsvImport(event, '/leads/import/commit/', 'importCommit')
};
