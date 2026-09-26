/**
 * Public help center switch: the wiring behind `/settings/help-center`.
 *
 * Server-only. `GET /org/help-center/` answers any member with the switch, the
 * address, the public URL and `can_edit`, which the API derives from the
 * caller's role (admins and superusers). `PATCH` is admin-only there; `can_edit`
 * decides what this page offers, never what it is allowed to do.
 */
import { apiRequest } from '$lib/api-helpers.js';

/**
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @returns {Promise<{ help_center_enabled: boolean, help_center_slug: string | null, public_url: string | null, can_edit: boolean }>}
 */
export async function getHelpCenterSettings({ cookies }) {
  return apiRequest('/org/help-center/', {}, { cookies });
}

/**
 * Save the switch and the address together.
 *
 * Exactly two keys reach the API, so a hostile form field (`org`, `id`,
 * `is_active`) is dropped here as well as ignored there. The slug is sent as
 * typed apart from surrounding spaces: lowercasing, the format rule, reserved
 * words and uniqueness are the serializer's, so the message a person reads
 * comes from the one place the rule lives.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {{ enabled: boolean, slug: string }} values
 */
export async function updateHelpCenterSettings({ cookies }, { enabled, slug }) {
  return apiRequest(
    '/org/help-center/',
    {
      method: 'PATCH',
      body: { help_center_enabled: Boolean(enabled), help_center_slug: String(slug ?? '').trim() }
    },
    { cookies }
  );
}

/**
 * What to tell the person whose save was refused.
 *
 * Every rule the serializer applies to this form is about the address, so its
 * message is read straight off `help_center_slug` ("That address is already
 * taken. Choose another.") rather than through `readableError`, which would
 * prefix it with the raw field name.
 *
 * @param {any} err the error `apiRequest` threw
 * @param {(err: any, fallback: string) => string} readableError
 */
export function saveErrorMessage(err, readableError) {
  const slugErrors = err?.body?.help_center_slug;
  if (Array.isArray(slugErrors) && slugErrors.length) return String(slugErrors[0]);
  return readableError(err, 'Could not save the help center.');
}
