import { error, fail, redirect } from '@sveltejs/kit';
import {
  getWebForm,
  getSubmissions,
  getAnalytics,
  updateWebForm,
  deleteWebForm,
  publishWebForm,
  unpublishWebForm
} from '$lib/server/v2/web-forms.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/**
 * The form, its recent submissions, and its 30-day analytics.
 *
 * Submissions and analytics are best-effort: they are context beside the
 * editor, not the editor itself, so a failure there shows an empty Activity
 * section rather than taking the whole page down. The form fetch is not
 * best-effort. A 404 means the id belongs to another org or to nothing, and
 * that is the answer, not a detail to paper over.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load(event) {
  const { id } = event.params;

  /** @type {any} */
  let detail;
  try {
    detail = await getWebForm(event, id);
  } catch (/** @type {any} */ err) {
    if (err?.status === 404) error(404, 'That web form does not exist.');
    throw err;
  }

  const [submissions, analytics] = await Promise.all([
    getSubmissions(event, id).catch(() => ({ submissions: [], count: 0 })),
    getAnalytics(event, id).catch(() => null)
  ]);

  return { ...detail, ...submissions, analytics };
}

/**
 * The field list arrives as one JSON string in a hidden input rather than as
 * N sets of indexed form fields.
 *
 * Rows are added, removed and reordered in the browser, so their names would
 * have to be index-derived (`fields[3][label]`), and a reorder would then
 * renumber every name in the DOM. Serialising the array the component already
 * holds keeps one source of truth for order and makes the save one request.
 *
 * A parse failure or a non-array is treated as "no field list sent" rather
 * than as an empty one. `updateWebForm` only touches `fields` when the key is
 * present, so `null` leaves the stored list alone; returning `[]` would delete
 * every field on the form because of a malformed hidden input.
 *
 * @param {FormData} form
 */
function readFields(form) {
  const raw = form.get('fields')?.toString();
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/**
 * One origin per line. Blank lines are dropped rather than sent as empty
 * strings, which `validate_allowed_origins` would reject with a message about
 * a field the person never typed in.
 *
 * @param {FormData} form
 */
function readOrigins(form) {
  return (form.get('allowed_origins')?.toString() ?? '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

/** @param {FormData} form */
function readValues(form) {
  /** @type {Record<string, any>} */
  const values = {
    name: form.get('name')?.toString() ?? '',
    submit_button_label: form.get('submit_button_label')?.toString() ?? '',
    success_mode: form.get('success_mode')?.toString() ?? '',
    success_message: form.get('success_message')?.toString() ?? '',
    redirect_url: form.get('redirect_url')?.toString() ?? '',
    lead_source: form.get('lead_source')?.toString() ?? '',
    allowed_origins: readOrigins(form),
    // An unchecked checkbox sends nothing at all, so absence is `false`.
    // Reading it any other way would make the box impossible to turn off.
    reject_disposable_email: form.get('reject_disposable_email') === 'on',
    captcha_provider: form.get('captcha_provider')?.toString() ?? '',
    captcha_site_key: form.get('captcha_site_key')?.toString() ?? '',
    // A select with nothing chosen sends '', which has to mean "nobody" and
    // not "leave it alone": clearing the owner is a thing an admin does.
    assign_to: form.get('assign_to')?.toString() || null,
    notify_profiles: form.getAll('notify_profiles').map(String).filter(Boolean),
    tags: form.getAll('tags').map(String).filter(Boolean)
  };

  /*
   * The secret is write-only: the API never sends it back, so the box is
   * always rendered empty and an empty box means "unchanged", never "erase".
   * Including a blank value here would wipe a working Turnstile secret every
   * time somebody saved an unrelated setting on this page, and because the
   * value is never read back, nothing would show that it had happened until
   * the next visitor was refused. Turnstile fails closed.
   */
  const secret = form.get('captcha_secret')?.toString().trim();
  if (secret) values.captcha_secret = secret;

  const fields = readFields(form);
  if (fields !== null) values.fields = fields;

  return values;
}

/**
 * @param {any} err
 * @param {string} forbidden
 * @param {string} fallback
 */
function actionError(err, forbidden, fallback) {
  if (err?.status === 403) return { status: 403, message: forbidden };
  if (err?.status === 404) return { status: 404, message: 'That form no longer exists.' };
  return { status: 400, message: readableError(err, fallback) };
}

/** @type {import('./$types').Actions} */
export const actions = {
  async save(event) {
    const form = await event.request.formData();
    try {
      await updateWebForm(event, event.params.id, readValues(form));
    } catch (/** @type {any} */ err) {
      const { status, message } = actionError(
        err,
        'Only an admin can change a web form.',
        'Could not save the form.'
      );
      return fail(status, { save: { error: message } });
    }
    return { saved: true };
  },

  async publish(event) {
    try {
      await publishWebForm(event, event.params.id);
    } catch (/** @type {any} */ err) {
      // The 400 body carries the reason ("add an email field first"), and that
      // reason is the entire useful content of the response.
      const { status, message } = actionError(
        err,
        'Only an admin can publish a web form.',
        'Could not publish the form.'
      );
      return fail(status, { publish: { error: message } });
    }
    return { published: true };
  },

  async unpublish(event) {
    try {
      await unpublishWebForm(event, event.params.id);
    } catch (/** @type {any} */ err) {
      const { status, message } = actionError(
        err,
        'Only an admin can unpublish a web form.',
        'Could not unpublish the form.'
      );
      return fail(status, { unpublish: { error: message } });
    }
    return { unpublished: true };
  },

  async delete(event) {
    try {
      await deleteWebForm(event, event.params.id);
    } catch (/** @type {any} */ err) {
      const { status, message } = actionError(
        err,
        'Only an admin can remove a web form.',
        'Could not remove the form.'
      );
      return fail(status, { delete: { error: message } });
    }
    // The record this page describes is gone, so there is nothing to render.
    redirect(303, '/settings/web-forms');
  }
};
