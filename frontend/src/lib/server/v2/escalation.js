/**
 * Escalation policies: the wiring behind `/settings/escalation`.
 *
 * Server-only. Reads `GET /cases/escalation-policies/`, at most one row per
 * priority. Each row carries the policy's two halves (first_response /
 * resolution action + target), the team to notify, and the reason the page
 * exists: `breaches_last_30d`, a server-side count of SLA breaches per
 * priority over the last 30 days. That count is condition-based, not sourced
 * from the escalation event log, so it still surfaces breaches under a dead
 * policy (off, or reassigning to nobody), which is the page's whole point.
 *
 * The backend returns targets as full profile objects (`user_details.name`)
 * and the team as a full team object; the page only wants `{ id, name }`, so
 * flatten to that here rather than teach the component the backend's nesting.
 * The same flattened shape is what the edit form uses to preselect a target.
 *
 * Create, edit, turn off, turn on and delete are wired below. The picker
 * lists (`people`, `teams`) are NOT fetched here: `settings.js`'s
 * `getSettingsHub` calls `getEscalationPolicies` and reads only `.policies`
 * off it, so a picker fetch in this function would be a redundant request on
 * every settings-hub load. The route's `load` fetches `getOrgPeopleAndTeams`
 * itself, in parallel with this.
 */
import { apiRequest } from '$lib/api-helpers.js';
import { ESCALATION_PRIORITIES } from '$lib/v2/enums.js';
import { viewerRole } from './organization.js';

/**
 * Profile → the `{ id, name }` the card renders, or null when unset.
 *
 * `name` falls back to the local part of the email, because `User.name` may be
 * blank and dropping the email left the card rendering "Notify " with nothing
 * after it. The mobile client's `UserLookup.displayName` resolves the same
 * payload the same way.
 */
function shapeTarget(target) {
  if (!target) return null;
  const name = target.user_details?.name?.trim();
  const email = target.user_details?.email ?? '';
  return { id: target.id, name: name || email.split('@')[0] || 'Unnamed' };
}

/** Team → `{ id, name }`, or null. */
function shapeTeam(team) {
  if (!team) return null;
  return { id: team.id, name: team.name };
}

/**
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @returns {Promise<{ policies: any[], can_edit: boolean }>}
 */
export async function getEscalationPolicies({ cookies }) {
  const { policies } = await apiRequest('/cases/escalation-policies/', {}, { cookies });
  return {
    policies: (policies ?? []).map((p) => ({
      id: p.id,
      priority: p.priority,
      is_active: p.is_active,
      first_response_action: p.first_response_action,
      resolution_action: p.resolution_action,
      first_response_target: shapeTarget(p.first_response_target),
      resolution_target: shapeTarget(p.resolution_target),
      notify_team: shapeTeam(p.notify_team),
      // null means "this org never set a target", and the card says so by
      // naming the built-in default rather than printing a blank.
      first_response_hours: p.first_response_hours ?? null,
      resolution_hours: p.resolution_hours ?? null,
      breaches_last_30d: p.breaches_last_30d ?? { first_response: 0, resolution: 0 }
    })),
    // A display hint: POST/PUT/DELETE on `/cases/escalation-policies/` each
    // start with `_is_admin(request.profile)` server-side and 403 regardless
    // of what this says. This only decides whether the page offers the
    // controls.
    can_edit: viewerRole(cookies) === 'ADMIN'
  };
}

export const CREATE_FIELDS = [
  'priority',
  'first_response_hours',
  'resolution_hours',
  'first_response_action',
  'resolution_action',
  'first_response_target_id',
  'resolution_target_id',
  'notify_team_id',
  'is_active'
];

/** The two SLA target fields, which share their parsing and their bounds. */
const HOUR_FIELDS = ['first_response_hours', 'resolution_hours'];

/** Mirrors `MAX_SLA_HOURS` in backend/cases/workflow.py. The bound exists
 *  because the business-hours walker gives up after 5 years of calendar days. */
const MAX_SLA_HOURS = 8760;

/**
 * Number input → what the API wants, or an error the form can show.
 *
 * An emptied input has to reach the backend as `null` (clear the override,
 * fall back to the built-in default), never as `''` or `0`: `0` puts the
 * deadline on `created_at`, so every new case would open already breached.
 * The serializer rejects both, so this is a faster message rather than the
 * rule itself.
 */
function normalizeHours(value, field) {
  if (value === '' || value === null) return null;
  const hours = Number(value);
  if (!Number.isInteger(hours) || hours < 1 || hours > MAX_SLA_HOURS) {
    throw new Error(
      `${field.replace(/_/g, ' ')} must be a whole number of hours from 1 to ${MAX_SLA_HOURS}, or blank for the default.`
    );
  }
  return hours;
}

/** `priority` is the natural key and is frozen after create. It is not merely
 *  unwise to change: `EscalationPolicyDetailView.put` deletes the key from the
 *  body before the serializer sees it, so a PUT carrying a new priority
 *  returns 200 and changes nothing. Omitting it here means the page never
 *  offers a control whose success is a lie. */
export const UPDATE_FIELDS = CREATE_FIELDS.filter((f) => f !== 'priority');

/** @param {string[]} allowed @param {{ [key: string]: any }} values */
function buildBody(allowed, values) {
  /** @type {Record<string, any>} */
  const body = {};
  for (const field of allowed) {
    if (values[field] === undefined) continue;
    body[field] = values[field];
  }
  for (const nullable of ['first_response_target_id', 'resolution_target_id', 'notify_team_id']) {
    if (body[nullable] === '') body[nullable] = null;
  }
  for (const field of HOUR_FIELDS) {
    if (body[field] !== undefined) body[field] = normalizeHours(body[field], field);
  }
  if (body.is_active !== undefined) body.is_active = Boolean(body.is_active);
  return body;
}

/** @param {{ cookies: import('@sveltejs/kit').Cookies }} event */
export async function createEscalationPolicy({ cookies }, values) {
  const body = buildBody(CREATE_FIELDS, values);
  if (!ESCALATION_PRIORITIES.includes(body.priority)) {
    throw new Error('Pick a priority for the policy.');
  }
  return await apiRequest('/cases/escalation-policies/', { method: 'POST', body }, { cookies });
}

/** @param {{ cookies: import('@sveltejs/kit').Cookies }} event */
export async function updateEscalationPolicy({ cookies }, id, values) {
  if (!id) throw new Error('Which policy? No policy id was given.');
  const body = buildBody(UPDATE_FIELDS, values);
  return await apiRequest(
    `/cases/escalation-policies/${id}/`,
    { method: 'PUT', body },
    { cookies }
  );
}

/**
 * Delete a policy, permanently. `EscalationPolicyDetailView.delete` calls
 * `obj.delete()`. The priority it covered then escalates to nobody, which is
 * what the confirm line has to say.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function deleteEscalationPolicy({ cookies }, id) {
  if (!id) throw new Error('Which policy? No policy id was given.');
  return await apiRequest(`/cases/escalation-policies/${id}/`, { method: 'DELETE' }, { cookies });
}
