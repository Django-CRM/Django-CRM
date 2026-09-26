import { fail } from '@sveltejs/kit';
import {
  getHelpCenterSettings,
  saveErrorMessage,
  updateHelpCenterSettings
} from '$lib/server/v2/help-center-settings.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/** @type {import('./$types').PageServerLoad} */
export async function load({ cookies }) {
  return { settings: await getHelpCenterSettings({ cookies }) };
}

/** @type {import('./$types').Actions} */
export const actions = {
  // Admin-only on the API. `can_edit` hides the form from a member, but that is
  // the affordance, not the check: `HelpCenterSettingsView.patch` refuses a
  // member whatever reaches it, and this turns that refusal into a sentence.
  async update(event) {
    const form = await event.request.formData();
    const values = {
      enabled: form.get('help_center_enabled') === 'true',
      slug: form.get('help_center_slug')?.toString() ?? ''
    };
    try {
      await updateHelpCenterSettings(event, values);
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, { update: { error: 'Only an admin can change the help center.' } });
      }
      return fail(400, {
        update: { error: saveErrorMessage(err, readableError), values }
      });
    }
    return { updated: true };
  }
};
