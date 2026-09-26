import { listAccounts } from '$lib/server/v2/accounts.js';
import { accountListQuery } from '$lib/server/v2/list-queries.js';
import { getOrgPeopleAndTeams, resolveMe } from '$lib/server/v2/org-people.js';
import { getTags } from '$lib/server/v2/tags.js';

/**
 * Only filters the API actually applies are forwarded. A parameter that
 * changes the URL and nothing else teaches people the filter bar is decorative.
 *
 * `city` and `industry` used to be forwarded straight off the URL, unvalidated
 * as anything the API happened to read; they now go through `readFilters`
 * like every other v2 list, so a hand-typed param never becomes a variable
 * this layer carries and an `industry` value outside the option list gets
 * dropped rather than returning a silent empty page.
 *
 * The pickers are fetched here rather than inside `listAccounts` for the same
 * reason as `leads.js`: folding them into the list read would cost every
 * other caller of that function a request it never renders.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, url, locals }) {
  const params = accountListQuery(url);

  const [{ results, totals }, orgPeople, tagList] = await Promise.all([
    listAccounts({ cookies }, params),
    getOrgPeopleAndTeams(cookies),
    // A failed tag fetch should cost the Tag dropdown in the filter bar, not
    // the whole list. Same pattern as leads.js and pipeline's deals.js.
    getTags({ cookies }).catch(() => ({ tags: [] }))
  ]);

  return {
    accounts: results,
    totals,
    people: orgPeople.people,
    tags: tagList.tags ?? [],
    meId: resolveMe(orgPeople.people, /** @type {any} */ (locals).user?.email)
  };
}
