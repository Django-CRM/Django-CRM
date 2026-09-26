/**
 * Reopen policy: the wiring behind `/settings/reopen`.
 *
 * Server-only. Reads `GET /cases/reopen-policy/`, the per-org singleton
 * (admin-only, auto-created on first read). The endpoint returns the four
 * policy fields (is_enabled, reopen_window_days, reopen_to_status,
 * notify_assigned) alongside three 30-day metrics the settings page draws,
 * reopened_last_30d, replies_after_window_30d, median_days_to_reply, all
 * computed server-side from the reopen audit trail (REOPENED activities + the
 * out-of-window flag the signal writes on late replies).
 *
 * `updateReopenPolicy` below is the write path: `PUT /cases/reopen-policy/`,
 * admin-only, `partial=True` on the backend but sent as all four fields at
 * once. The page's own `+page.server.js` action turns a rejected save into a
 * message the user reads.
 */
import { apiRequest } from '$lib/api-helpers.js';
import { viewerIsAdmin } from './organization.js';
import { REOPEN_TO_STATUSES } from '$lib/v2/enums.js';

/**
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @returns {Promise<{ policy: any, can_edit: boolean }>}
 */
export async function getReopenPolicy({ cookies }) {
  const policy = await apiRequest('/cases/reopen-policy/', {}, { cookies });
  return { policy, can_edit: viewerIsAdmin(cookies) };
}

/** The four fields the serializer accepts. Everything else in the GET
 *  response is a computed metric and is not writable. */
const EDITABLE_FIELDS = ['is_enabled', 'reopen_window_days', 'reopen_to_status', 'notify_assigned'];

/**
 * Update the org's reopen policy.
 *
 * `ReopenPolicyView.put` is admin-only and `partial=True`, so this could send
 * a subset. It sends all four anyway: the form edits all four at once, and a
 * partial body would make "unchecked" indistinguishable from "not submitted"
 * for the two booleans.
 *
 * The two guards below are fast fails for an obvious mistake, not security
 * controls. `validate_reopen_window_days` and `validate_reopen_to_status` on
 * the serializer are the real authority and run regardless of what reaches
 * them.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {{ [key: string]: any }} values callers may pass extra keys (a
 *   computed metric read back off the page, a hostile `org`); the allow-list
 *   loop below is what stops them reaching the API.
 * @returns {Promise<any>} the updated policy, the four fields only.
 */
export async function updateReopenPolicy({ cookies }, values) {
  const days = Number(values.reopen_window_days);
  if (!Number.isInteger(days) || days < 1 || days > 365) {
    throw new Error('The reopen window must be a whole number of days between 1 and 365.');
  }
  if (!REOPEN_TO_STATUSES.includes(values.reopen_to_status)) {
    throw new Error('A reopened ticket has to come back as New, Assigned or Pending.');
  }

  /** @type {Record<string, any>} */
  const body = {};
  for (const field of EDITABLE_FIELDS) {
    body[field] = values[field];
  }
  body.reopen_window_days = days;
  body.is_enabled = Boolean(values.is_enabled);
  body.notify_assigned = Boolean(values.notify_assigned);

  return await apiRequest('/cases/reopen-policy/', { method: 'PUT', body }, { cookies });
}
