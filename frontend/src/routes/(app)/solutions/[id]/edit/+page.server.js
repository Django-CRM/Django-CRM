import { fail, redirect } from '@sveltejs/kit';
import { getArticle, updateArticle } from '$lib/server/v2/solutions.js';
import { getTags } from '$lib/server/v2/tags.js';
import { readableError } from '$lib/server/v2/form-errors.js';
import { sameIds } from '$lib/v2/pickers.js';

/**
 * The mock's detail page had an Edit button that went nowhere, so this route
 * is new rather than converted.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, locals, params }) {
  const { article } = await getArticle({ cookies }, params.id);
  return {
    article,
    canRelease: /** @type {any} */ (locals).profile?.role === 'ADMIN',
    // Archived tags are filtered out for the reason given on the create page:
    // `_apply_tags` refuses them, so the checkbox would do nothing. That also
    // means an article already carrying a tag archived later has no box for
    // it, and `_apply_tags` REPLACES the set with the active ids it is sent,
    // so any save that sends `tags` drops it. The action therefore sends
    // `tags` only when the ticked boxes differ from the stored ones.
    tags: ((await getTags({ cookies })).tags ?? []).filter((t) => t.is_active),
    form: {
      title: article.title,
      description: article.description,
      status: article.status,
      tags: (article.tags ?? []).map((/** @type {any} */ t) => t.id)
    }
  };
}

/** @type {import('./$types').Actions} */
export const actions = {
  save: async ({ cookies, params, request }) => {
    const form = await request.formData();

    /** @type {Record<string, any>} */
    const values = {
      title: form.get('title')?.toString().trim() ?? '',
      description: form.get('description')?.toString().trim() ?? '',
      tags: form.getAll('tags').map(String)
    };

    // Tags go only when somebody changed them. `_apply_tags` leaves an absent
    // key alone and replaces the whole set with the ACTIVE ids in a present
    // one, so sending the ticked boxes on every save dropped any archived tag
    // the article carried (it has no box). The page posts the stored tags
    // that do have a box as `tags_original`. An unticked box is still a
    // deliberate "not this one" once something changed.
    const tagsWere = form.getAll('tags_original').map(String);
    const tagsForRetry = values.tags;
    if (sameIds(values.tags, tagsWere)) delete values.tags;

    /*
     * The status is only sent when somebody actually changed it.
     *
     * This is the same "absent means unchanged" rule the other five modules
     * use for many-to-many fields, and here it matters for a different
     * reason: `SolutionDetailView` asks whether the request *moves* `status`
     * before demanding an admin. Sending the article's existing status on
     * every save is harmless for an admin and would be a 403 for the author
     * of an already-approved article who only fixed a typo, a form that
     * refuses to save what it just showed you.
     *
     * `is_published` is not on this form at all. It is the publish button on
     * the article page, which is a decision rather than a field.
     */
    const status = form.get('status')?.toString() ?? '';
    const statusWas = form.get('status_original')?.toString() ?? '';
    if (status && status !== statusWas) values.status = status;

    try {
      await updateArticle({ cookies }, params.id, values);
    } catch (/** @type {any} */ err) {
      return fail(400, {
        values: { ...values, tags: tagsForRetry },
        error: readableError(err, 'Could not save this article.')
      });
    }

    redirect(303, `/solutions/${params.id}`);
  }
};
