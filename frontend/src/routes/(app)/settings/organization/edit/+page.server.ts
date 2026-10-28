import { fail, redirect } from '@sveltejs/kit';
import {
  getOrgSettings,
  updateOrgSettings,
  EDITABLE_FIELDS,
  BOOLEAN_FIELDS,
  listTimezones
} from '#lib/server/v2/organization.js';
import { readableError } from '#lib/server/v2/form-errors.js';
import { apiStatus } from '#lib/api-errors.js';
import type { Actions, PageServerLoad } from './$types';
import type { FormBody } from '#lib/api-helpers.js';
import type { components } from '#lib/api-schema.js';

/**
 * Editing organization settings.
 *
 * Admin-only. The load returns `forbidden` for a non-admin (the read page only
 * links here for admins, but a direct visit must not render the form either),
 * and the save action is gated a second time by the backend, `PATCH
 * /api/org/settings/` 403s a non-admin regardless of what reaches it.
 */
export const load: PageServerLoad = async ({ cookies }) => {
  const { org, can_edit } = await getOrgSettings({ cookies });
  if (!can_edit) return { forbidden: true };
  // Fetched rather than built from `Intl`, so the option list uses the same
  // zone vocabulary the org's stored value came from. See `listTimezones`.
  const timezones = await listTimezones(cookies).catch(() => [{ name: 'UTC', label: 'UTC' }]);
  return { forbidden: false, org, timezones };
};

export const actions: Actions = {
  save: async ({ cookies, request }) => {
    const form = await request.formData();

    const body: FormBody<components['schemas']['PatchedOrgSettingsRequest']> = {};

    // Text and select fields: only what the form actually submitted, trimmed.
    // An absent field is left alone (PATCH is partial), never blanked.
    for (const field of EDITABLE_FIELDS) {
      if (form.has(field)) body[field] = form.get(field)?.toString().trim() ?? '';
    }

    // The two switches always submit an explicit 'true'/'false', so "off" is a
    // real value rather than the "absent → leave alone" convention above.
    for (const flag of BOOLEAN_FIELDS) {
      if (form.has(flag)) body[flag] = form.get(flag) === 'true';
    }

    // Off is null, not an empty string, which the integer column would refuse.
    if (form.has('lead_reminder_minutes')) {
      body.lead_reminder_minutes = Number(form.get('lead_reminder_minutes')) || null;
    }

    try {
      await updateOrgSettings({ cookies }, body);
    } catch (err) {
      if (apiStatus(err) === 403) {
        return fail(403, {
          values: body,
          error: 'Only an admin can change organization settings.'
        });
      }
      return fail(400, {
        values: body,
        error: readableError(err, 'Could not save these settings.')
      });
    }

    redirect(303, '/settings/organization');
  }
};
