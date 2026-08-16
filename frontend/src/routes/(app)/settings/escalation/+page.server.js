import { fail } from '@sveltejs/kit';
import {
  getEscalationPolicies,
  createEscalationPolicy,
  updateEscalationPolicy,
  deleteEscalationPolicy
} from '$lib/server/v2/escalation.js';
import { getOrgPeopleAndTeams } from '$lib/server/v2/org-people.js';
import { readableError } from '$lib/server/v2/form-errors.js';

// The policy list and the create/edit form's target picker are two
// independent fetches: `getEscalationPolicies` does not fold
// `getOrgPeopleAndTeams` into its own return, because `getSettingsHub` (the
// settings-hub aggregator) also calls `getEscalationPolicies` and only ever
// reads `.policies` off it. Folding the picker fetch in there would fire an
// extra, unused `/users/get-teams-and-users/` request on every settings-hub
// load. Fetched in parallel here instead, since this is the one place both
// are actually needed.
/** @type {import('./$types').PageServerLoad} */
export async function load({ cookies }) {
  const [data, options] = await Promise.all([
    getEscalationPolicies({ cookies }),
    getOrgPeopleAndTeams(cookies)
  ]);
  return { ...data, ...options };
}

/**
 * Everything both the create and edit form submit. `is_active` is
 * deliberately not read here: only the create form carries that checkbox
 * (the row's own Turn off / Turn on control owns that state on an existing
 * policy), so reading `form.get('is_active')` here would answer `false` on
 * every edit submit, since an absent checkbox and an unchecked one look
 * identical to `FormData`. The `create` action below attaches `is_active`
 * itself, straight from the one form that renders it.
 *
 * `priority` is read here too, but it only ever reaches the outgoing body on
 * a create: `updateEscalationPolicy` filters through `UPDATE_FIELDS`, which
 * excludes it, and the edit form never renders a `priority` input in the
 * first place.
 *
 * @param {FormData} form
 */
function readValues(form) {
  return {
    priority: form.get('priority')?.toString() ?? '',
    first_response_action: form.get('first_response_action')?.toString() ?? '',
    resolution_action: form.get('resolution_action')?.toString() ?? '',
    first_response_target_id: form.get('first_response_target_id')?.toString() ?? '',
    resolution_target_id: form.get('resolution_target_id')?.toString() ?? '',
    notify_team_id: form.get('notify_team_id')?.toString() ?? '',
    // Blank is meaningful: it clears the org's override and falls back to the
    // built-in target for that priority. `normalizeHours` turns it into null.
    first_response_hours: form.get('first_response_hours')?.toString() ?? '',
    resolution_hours: form.get('resolution_hours')?.toString() ?? ''
  };
}

/** @type {import('./$types').Actions} */
export const actions = {
  // Admin-only server-side (`EscalationPolicyListCreateView.post` and
  // `EscalationPolicyDetailView.put`/`.delete` each call `_is_admin` first).
  // `can_edit` only hides the control.
  async create(event) {
    const form = await event.request.formData();
    const values = { ...readValues(form), is_active: form.get('is_active') === 'true' };
    try {
      await createEscalationPolicy(event, values);
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, { create: { error: 'Only an admin can change escalation policies.' } });
      }
      return fail(400, { create: { error: readableError(err, 'Could not add the policy.') } });
    }
    return { created: true };
  },

  async update(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    const values = readValues(form);
    try {
      await updateEscalationPolicy(event, id, values);
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, { update: { error: 'Only an admin can change escalation policies.' } });
      }
      return fail(400, { update: { error: readableError(err, 'Could not save the policy.') } });
    }
    return { updated: true };
  },

  async deactivate(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    try {
      await updateEscalationPolicy(event, id, { is_active: false });
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, {
          deactivate: { error: 'Only an admin can change escalation policies.' }
        });
      }
      return fail(400, {
        deactivate: { error: readableError(err, 'Could not turn the policy off.') }
      });
    }
    return { deactivated: true };
  },

  // A policy's own action, not a reuse of `update`. `readValues` builds a
  // full policy payload from the form, and this control only ever submits an
  // id: if it posted through `update`, every other field would read back
  // empty (`FormData` has no key for it) and `buildBody` would forward that,
  // blanking the policy on every reactivation. Calling `updateEscalationPolicy`
  // directly with only `{ is_active: true }` sidesteps `readValues` entirely,
  // so the PUT body is `{ is_active: true }` and nothing else.
  async activate(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    try {
      await updateEscalationPolicy(event, id, { is_active: true });
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, { activate: { error: 'Only an admin can change escalation policies.' } });
      }
      return fail(400, {
        activate: { error: readableError(err, 'Could not turn the policy on.') }
      });
    }
    return { activated: true };
  },

  // `EscalationPolicyDetailView.delete` is a hard delete. The row control
  // that posts here says so before it does, and names what happens next: the
  // priority it covered escalates to nobody.
  async remove(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    try {
      await deleteEscalationPolicy(event, id);
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, { remove: { error: 'Only an admin can change escalation policies.' } });
      }
      return fail(400, { remove: { error: readableError(err, 'Could not delete the policy.') } });
    }
    return { removed: true };
  }
};
