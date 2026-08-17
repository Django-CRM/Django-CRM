import { fail, redirect } from '@sveltejs/kit';
import {
  getWebForms,
  createWebForm,
  publishWebForm,
  unpublishWebForm,
  deleteWebForm
} from '$lib/server/v2/web-forms.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/** @type {import('./$types').PageServerLoad} */
export async function load({ cookies }) {
  return getWebForms({ cookies });
}

/**
 * Every action here is admin-only server-side and answers 403 to a member.
 * The page hides the controls from a member, but hiding is not the boundary:
 * `is_org_admin(request.profile)` in `webforms/views.py` is, and these
 * handlers turn its 403 into a sentence rather than a stack trace, because a
 * member can still reach these actions by posting to them directly.
 *
 * A 404 means the id belongs to another org (or to nothing). The API answers
 * 404 rather than 403 on purpose, so the id space cannot be used to discover
 * which forms exist elsewhere; the copy below keeps that distinction rather
 * than collapsing it back into "forbidden".
 *
 * @param {any} err
 * @param {string} forbidden
 * @param {string} missing
 * @param {string} fallback
 */
function actionError(err, forbidden, missing, fallback) {
  if (err?.status === 403) return { status: 403, message: forbidden };
  if (err?.status === 404) return { status: 404, message: missing };
  return { status: 400, message: readableError(err, fallback) };
}

/** @type {import('./$types').Actions} */
export const actions = {
  /**
   * A new form starts as a name and nothing else. It is created unpublished
   * and with no fields, because a form with no email field cannot be
   * published at all, and asking for the whole field list in a one-line
   * create panel would put the field editor on two pages.
   */
  async create(event) {
    const form = await event.request.formData();
    const name = form.get('name')?.toString() ?? '';
    /** @type {any} */
    let created;
    try {
      created = await createWebForm(event, { name });
    } catch (/** @type {any} */ err) {
      const { status, message } = actionError(
        err,
        'Only an admin can create a web form. A form accepts leads from anyone on the internet, so making one is an admin action.',
        'That form no longer exists.',
        'Could not create the form.'
      );
      return fail(status, { create: { error: message } });
    }
    // Straight into the field editor: a form with no fields collects nothing,
    // so the list is never where someone wants to land after creating one.
    redirect(303, `/settings/web-forms/${created.id}`);
  },

  async publish(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    try {
      await publishWebForm(event, id);
    } catch (/** @type {any} */ err) {
      // The 400 body is the whole point here: "add an email field before
      // publishing" is the response, and `readableError` carries it through.
      const { status, message } = actionError(
        err,
        'Only an admin can publish a web form.',
        'That form no longer exists.',
        'Could not publish the form.'
      );
      return fail(status, { publish: { error: message } });
    }
    return { published: true };
  },

  async unpublish(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    try {
      await unpublishWebForm(event, id);
    } catch (/** @type {any} */ err) {
      const { status, message } = actionError(
        err,
        'Only an admin can unpublish a web form.',
        'That form no longer exists.',
        'Could not unpublish the form.'
      );
      return fail(status, { unpublish: { error: message } });
    }
    return { unpublished: true };
  },

  async delete(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    try {
      await deleteWebForm(event, id);
    } catch (/** @type {any} */ err) {
      const { status, message } = actionError(
        err,
        'Only an admin can remove a web form.',
        'That form no longer exists.',
        'Could not remove the form.'
      );
      return fail(status, { delete: { error: message } });
    }
    return { deleted: true };
  }
};
