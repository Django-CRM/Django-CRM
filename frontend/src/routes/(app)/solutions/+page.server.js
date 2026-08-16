import { fail } from '@sveltejs/kit';
import { listArticles, setPublished, FILTER_FIELDS } from '$lib/server/v2/solutions.js';
import { readFilters, buildFilterQuery } from '$lib/server/v2/filter-params.js';
import { getTags } from '$lib/server/v2/tags.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/**
 * Only filters the API actually applies are forwarded. A parameter that
 * changes the URL and nothing else teaches people the filter bar is decorative.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, locals, url }) {
  const params = buildFilterQuery(FILTER_FIELDS, readFilters(url, 'solutions'));
  for (const key of ['search', 'limit']) {
    const value = url.searchParams.get(key);
    if (value) params.set(key, value);
  }

  // Three states, not two: unset means "everything".
  const visibility = url.searchParams.get('visibility') ?? '';
  if (visibility === 'published') params.set('is_published', 'true');
  if (visibility === 'internal') params.set('is_published', 'false');

  const [{ results, totals }, tagList] = await Promise.all([
    listArticles({ cookies }, params),
    // Same degradation as the ticket queue: losing the tag list should cost
    // the Tag dropdown, not the whole knowledge base.
    getTags({ cookies }).catch(() => ({ tags: [] }))
  ]);

  return {
    articles: results,
    totals,
    tags: tagList.tags ?? [],
    search: params.get('search') ?? '',
    status: params.get('status') ?? '',
    visibility,
    // From the JWT's own claim, so it costs no round trip. It decides which
    // buttons render and nothing else; `assert_solution_release_access` is
    // the control, and it answers 403 whatever this says.
    canRelease: /** @type {any} */ (locals).profile?.role === 'ADMIN'
  };
}

/** @type {import('./$types').Actions} */
export const actions = {
  /**
   * Release an article from the row it is on. The list is where somebody
   * notices the approved-and-invisible pile, so it is where the button
   * belongs, walking into the article to press it is the step that leaves
   * those rows sitting there.
   */
  publish: async ({ cookies, request }) => {
    const form = await request.formData();
    const id = form.get('id')?.toString();
    if (!id) return fail(400, { error: 'No article to publish.' });

    try {
      await setPublished({ cookies }, id, true);
    } catch (/** @type {any} */ err) {
      return fail(400, { error: readableError(err, 'Could not publish this article.') });
    }
    return { published: true };
  }
};
