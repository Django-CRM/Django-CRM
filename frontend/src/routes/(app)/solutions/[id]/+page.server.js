import { fail, redirect } from '@sveltejs/kit';
import {
  deleteArticle,
  getArticle,
  setPublished,
  updateArticle
} from '$lib/server/v2/solutions.js';
import { readableError } from '$lib/server/v2/form-errors.js';
import { isOrgAdmin } from '$lib/admin.js';

/** @type {import('./$types').PageServerLoad} */
export async function load({ cookies, locals, params }) {
  const article = await getArticle({ cookies }, params.id);
  return {
    ...article,
    // See the list loader: this decides what renders, never what is allowed.
    canRelease: isOrgAdmin(/** @type {any} */ (locals).profile)
  };
}

/** @type {import('./$types').Actions} */
export const actions = {
  /**
   * Move the article along the review workflow.
   *
   * Only the status: the body is not in this form, so a PATCH carrying one
   * field is exactly what should go. `draft → reviewed` is anyone's to press;
   * anything → `approved` is an admin's, and the API is what says so.
   */
  setStatus: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const status = form.get('status')?.toString() ?? '';
    if (!status) return fail(400, { error: 'No status to set.' });

    try {
      await updateArticle({ cookies }, params.id, { status });
    } catch (/** @type {any} */ err) {
      return fail(400, { error: readableError(err, 'Could not change the status.') });
    }
    return { status };
  },

  /**
   * Release the article to customers, or take it back.
   *
   * The two verbs are one action with a flag rather than two nearly identical
   * ones, because the thing they have in common. Both are admin-only, both
   * are the same switch, is the part worth keeping in one place.
   */
  setPublished: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const published = form.get('published') === 'true';

    try {
      await setPublished({ cookies }, params.id, published);
    } catch (/** @type {any} */ err) {
      return fail(400, {
        error: readableError(
          err,
          published ? 'Could not publish this article.' : 'Could not unpublish this article.'
        )
      });
    }
    return { published };
  },
  /**
   * Delete this article. The API applies the delete rule (the author or an
   * admin) and answers 403 or 404 when it refuses; the page shows its
   * sentence. On success the article is gone, so the list is where to land.
   */
  delete: async ({ cookies, params }) => {
    try {
      await deleteArticle({ cookies }, params.id);
    } catch (/** @type {any} */ err) {
      const code = err?.status >= 400 && err?.status < 500 ? err.status : 400;
      return fail(code, { error: readableError(err, 'Could not delete this article.') });
    }
    redirect(303, '/solutions');
  }
};
