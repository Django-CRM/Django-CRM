import { listContacts } from '$lib/server/v2/contacts.js';
import { contactListQuery } from '$lib/server/v2/list-queries.js';
import { getOrgPeopleAndTeams, resolveMe } from '$lib/server/v2/org-people.js';
import { getTags } from '$lib/server/v2/tags.js';
import { forwardCsvImport } from '$lib/server/v2/csv-import.js';

/**
 * Only filters the API actually applies are forwarded. A parameter that
 * changes the URL and nothing else teaches people the filter bar is decorative.
 *
 * `inactive=1` in the URL is kept from the fixture page, because the question
 * it asks is a good one, but it is answered by the API now (`?is_active=`)
 * rather than by discarding rows after they arrive, which was only ever right
 * on the first page.
 *
 * The pickers are fetched here rather than inside `listContacts` for the same
 * reason as `tickets.js`: a picker fetch folded into the list read would cost
 * a redundant request on every caller that reads contacts for their totals
 * alone.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, url, locals }) {
  const params = contactListQuery(url);
  const includeInactive = url.searchParams.get('inactive') === '1';

  const [{ results, totals }, orgPeople, tagList] = await Promise.all([
    listContacts({ cookies }, params),
    getOrgPeopleAndTeams(cookies),
    // A failed tag fetch should cost the Tag dropdown in the filter bar, not
    // the whole list. Follows the tickets.js pattern; see the note there.
    getTags({ cookies }).catch(() => ({ tags: [] }))
  ]);

  return {
    contacts: results,
    totals,
    includeInactive,
    search: params.get('search') ?? '',
    people: orgPeople.people,
    tags: tagList.tags ?? [],
    meId: resolveMe(orgPeople.people, /** @type {any} */ (locals).user?.email)
  };
}

/** The CSV import drawer's two steps; see `forwardCsvImport`. */
export const actions = {
  importPreview: (event) => forwardCsvImport(event, '/contacts/import/preview/', 'importPreview'),
  importCommit: (event) => forwardCsvImport(event, '/contacts/import/commit/', 'importCommit')
};
