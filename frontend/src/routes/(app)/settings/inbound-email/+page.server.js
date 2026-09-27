import { fail } from '@sveltejs/kit';
import {
  getMailboxes,
  createMailbox,
  updateMailbox,
  deleteMailbox,
  topicArnEdit
} from '$lib/server/v2/inbound-email.js';
import { getOrgPeopleAndTeams } from '$lib/server/v2/org-people.js';
import { readableError } from '$lib/server/v2/form-errors.js';

// The mailbox list and the create/edit form's assignee picker are two
// independent fetches: `getMailboxes` does not fold `getOrgPeopleAndTeams`
// into its own return, because `getSettingsHub` (the settings-hub aggregator)
// also calls `getMailboxes` and only ever reads `.totals` off it. Folding the
// picker fetch in there would fire an extra, unused
// `/users/get-teams-and-users/` request on every settings-hub load. Fetched
// in parallel here instead, since this is the one place both are actually
// needed. This resource has no team FK, so only `.people` is read below, but
// `getOrgPeopleAndTeams` returns both and spreading both is harmless.
/** @type {import('./$types').PageServerLoad} */
export async function load({ cookies }) {
  const [data, options] = await Promise.all([
    getMailboxes({ cookies }),
    getOrgPeopleAndTeams(cookies)
  ]);
  return { ...data, ...options };
}

/**
 * Everything both the create and edit form submit. There is no signing-secret
 * key read here: no field on either form carries one, and reading one would be
 * the first step toward sending it. `is_active` is likewise deliberately not
 * read here: only the create form carries that checkbox (the edit form's own
 * row control, Turn off / Turn on, owns that state), so reading
 * `form.get('is_active')` here would answer `false` on every edit submit,
 * since an absent checkbox and an unchecked one look identical to `FormData`.
 * The `create` action below attaches `is_active` itself, straight from the
 * one form that renders it.
 *
 * `topic_arn` is read only when the admin changed it from the value the form
 * was prefilled with (`topic_arn_was`); see `topicArnEdit`.
 *
 * @param {FormData} form
 */
function readValues(form) {
  return {
    address: form.get('address')?.toString().trim() ?? '',
    provider: form.get('provider')?.toString() ?? '',
    default_priority: form.get('default_priority')?.toString() ?? '',
    default_case_type: form.get('default_case_type')?.toString() ?? '',
    default_assignee_id: form.get('default_assignee_id')?.toString() ?? '',
    topic_arn: topicArnEdit(form.get('topic_arn'), form.get('topic_arn_was'))
  };
}

/** @type {import('./$types').Actions} */
export const actions = {
  // Admin-only server-side (`InboundMailboxListCreateView.post` calls
  // `_is_admin` first). `can_edit` only hides the control.
  async create(event) {
    const form = await event.request.formData();
    const values = { ...readValues(form), is_active: form.get('is_active') === 'true' };
    try {
      await createMailbox(event, values);
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, { create: { error: 'Only an admin can change inbound mailboxes.' } });
      }
      return fail(400, { create: { error: readableError(err, 'Could not add the address.') } });
    }
    return { created: true };
  },

  async update(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    const values = readValues(form);
    try {
      await updateMailbox(event, id, values);
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, { update: { error: 'Only an admin can change inbound mailboxes.' } });
      }
      return fail(400, { update: { error: readableError(err, 'Could not save the address.') } });
    }
    return { updated: true };
  },

  async deactivate(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    try {
      await updateMailbox(event, id, { is_active: false });
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, { deactivate: { error: 'Only an admin can change inbound mailboxes.' } });
      }
      return fail(400, {
        deactivate: { error: readableError(err, 'Could not turn the address off.') }
      });
    }
    return { deactivated: true };
  },

  // A row's own action, not a reuse of `update`. `readValues` builds a full
  // mailbox payload from the form, and this control only ever submits an id:
  // if it posted through `update`, `address` would read back as `''`
  // (`FormData` has no key for it) and `createMailbox`/`updateMailbox`'s own
  // `buildBody` would forward that, and `validate_address` would reject the
  // empty string on its way to blanking the address. Calling `updateMailbox`
  // directly with only `{ is_active: true }` sidesteps `readValues` entirely,
  // so the PUT body is `{ is_active: true }` and nothing else.
  async activate(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    try {
      await updateMailbox(event, id, { is_active: true });
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, { activate: { error: 'Only an admin can change inbound mailboxes.' } });
      }
      return fail(400, {
        activate: { error: readableError(err, 'Could not turn the address on.') }
      });
    }
    return { activated: true };
  },

  // `InboundMailboxDetailView.delete` is a hard delete. The topic pin and the
  // webhook lookup go with the row, so a delivery already in flight from AWS
  // is refused, not just left unopened. The row control that posts here says
  // so before it does.
  async remove(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    try {
      await deleteMailbox(event, id);
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, { remove: { error: 'Only an admin can change inbound mailboxes.' } });
      }
      return fail(400, { remove: { error: readableError(err, 'Could not delete the address.') } });
    }
    return { removed: true };
  }
};
