import { fail } from '@sveltejs/kit';
import { listWebhooks, createWebhook, eventsFromForm } from '$lib/server/v2/webhooks.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/**
 * Outbound webhooks: the list and the create panel.
 *
 * Every route underneath is admin-only server-side (`webhooks/views.py`), the
 * list included, so a member gets `forbidden` and an explanation rather than
 * a broken page.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies }) {
  return await listWebhooks({ cookies });
}

/** @type {import('./$types').Actions} */
export const actions = {
  /**
   * The response is the one place the full signing secret appears, besides a
   * rotate. It is handed to the page for this render only.
   */
  async create(event) {
    const form = await event.request.formData();
    const values = {
      url: form.get('url')?.toString().trim() ?? '',
      description: form.get('description')?.toString().trim() ?? '',
      format: form.get('format')?.toString() ?? 'json',
      events: eventsFromForm(form)
    };
    if (!values.events.length) {
      return fail(400, { create: { error: 'Choose at least one event.', values } });
    }
    try {
      const created = await createWebhook(event, values);
      return { created: { id: created.id, url: created.url, secret: created.secret } };
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, { create: { error: 'Only an admin can add webhooks.', values } });
      }
      return fail(400, {
        create: { error: readableError(err, 'Could not add the webhook.'), values }
      });
    }
  }
};
