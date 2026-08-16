import { fail, redirect } from '@sveltejs/kit';
import { createArticle } from '$lib/server/v2/solutions.js';
import { getTags } from '$lib/server/v2/tags.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/** @type {import('./$types').PageServerLoad} */
export async function load({ cookies, locals }) {
  return {
    // `getTags` asks for archived ones too, for the settings page. Filtered
    // here because `_apply_tags` refuses an archived tag, so offering one
    // would be a checkbox that silently does nothing on save.
    tags: ((await getTags({ cookies })).tags ?? []).filter((t) => t.is_active),
    // Decides whether the form offers "Approved" and the publish switch at
    // all. The API refuses both for anyone else, so offering them would be a
    // form that fails on submit for reasons the writer cannot see.
    canRelease: /** @type {any} */ (locals).profile?.role === 'ADMIN'
  };
}

/** @type {import('./$types').Actions} */
export const actions = {
  create: async ({ cookies, request }) => {
    const form = await request.formData();

    const values = {
      title: form.get('title')?.toString().trim() ?? '',
      description: form.get('description')?.toString().trim() ?? '',
      status: form.get('status')?.toString() || 'draft',
      // A checkbox that is off submits nothing at all, which on a *create* is
      // unambiguous. There is no stored value for "unchanged" to mean.
      is_published: form.get('is_published') === 'on',
      tags: form.getAll('tags').map(String)
    };

    /** @type {any} */
    let created;
    try {
      created = await createArticle({ cookies }, values);
    } catch (/** @type {any} */ err) {
      return fail(400, { values, error: readableError(err, 'Could not save this article.') });
    }

    redirect(303, `/solutions/${created.id}`);
  }
};
