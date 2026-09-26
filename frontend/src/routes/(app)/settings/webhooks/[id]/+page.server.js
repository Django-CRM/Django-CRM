import { error, fail, redirect } from '@sveltejs/kit';
import {
  getWebhook,
  updateWebhook,
  deleteWebhook,
  testWebhook,
  rotateWebhookSecret,
  redeliverWebhook,
  eventsFromForm
} from '$lib/server/v2/webhooks.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/**
 * One webhook: its settings, its actions and a page of its delivery log.
 *
 * A 404 means the id belongs to another org or to nothing, and is the answer.
 * A 403 is a member, who gets the same "admins only" state as the list.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load(event) {
  const offset = Math.max(0, Number.parseInt(event.url.searchParams.get('offset') ?? '0', 10) || 0);
  try {
    return { forbidden: false, ...(await getWebhook(event, event.params.id, offset)) };
  } catch (/** @type {any} */ err) {
    if (err?.status === 404) error(404, 'That webhook does not exist.');
    if (err?.status === 403) return { forbidden: true };
    throw err;
  }
}

/**
 * Turn an API refusal into the sentence the page shows, under `key`.
 * @param {string} key @param {any} err @param {string} fallback
 */
function refused(key, err, fallback) {
  if (err?.status === 403) {
    return fail(403, { [key]: { error: 'Only an admin can change webhooks.' } });
  }
  return fail(400, { [key]: { error: readableError(err, fallback) } });
}

/** @type {import('./$types').Actions} */
export const actions = {
  async update(event) {
    const form = await event.request.formData();
    const values = {
      url: form.get('url')?.toString().trim() ?? '',
      description: form.get('description')?.toString().trim() ?? '',
      format: form.get('format')?.toString() ?? 'json',
      events: eventsFromForm(form)
    };
    if (!values.events.length) {
      return fail(400, { update: { error: 'Choose at least one event.' } });
    }
    try {
      await updateWebhook(event, event.params.id, values);
    } catch (err) {
      return refused('update', err, 'Could not save the webhook.');
    }
    return { updated: true };
  },

  // Its own action carrying only `is_active`, so switching an endpoint on or
  // off can never send a half-empty settings body along with it.
  async toggle(event) {
    const form = await event.request.formData();
    const isActive = form.get('is_active') === 'true';
    try {
      await updateWebhook(event, event.params.id, { is_active: isActive });
    } catch (err) {
      return refused('toggle', err, 'Could not change the webhook.');
    }
    return { toggled: true };
  },

  async test(event) {
    try {
      await testWebhook(event, event.params.id);
    } catch (err) {
      return refused('test', err, 'Could not send a test.');
    }
    return { tested: true };
  },

  async rotate(event) {
    try {
      const res = await rotateWebhookSecret(event, event.params.id);
      return { rotated: { secret: res.secret } };
    } catch (err) {
      return refused('rotate', err, 'Could not rotate the secret.');
    }
  },

  async redeliver(event) {
    const form = await event.request.formData();
    const deliveryId = form.get('delivery_id')?.toString() ?? '';
    try {
      await redeliverWebhook(event, deliveryId);
    } catch (err) {
      return refused('redeliver', err, 'Could not redeliver.');
    }
    return { redelivered: true };
  },

  async remove(event) {
    try {
      await deleteWebhook(event, event.params.id);
    } catch (err) {
      return refused('remove', err, 'Could not delete the webhook.');
    }
    redirect(303, '/settings/webhooks');
  }
};
