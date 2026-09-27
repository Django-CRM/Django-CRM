import { fail } from '@sveltejs/kit';
import {
  getMacros,
  createMacro,
  updateMacro,
  deleteMacro,
  activateMacro
} from '$lib/server/v2/macros.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/** @type {import('./$types').PageServerLoad} */
export async function load({ cookies }) {
  return getMacros({ cookies });
}

// `is_active` is deliberately not read here: the create/edit form has no
// control for it (see the page's `fields` snippet), so a macro write from
// this form never carries it. That closes the gap "Turn off"'s two-click
// `ConfirmAction` used to have a sibling: unchecking a box in the edit panel
// and hitting Save used to flip `is_active` with no confirm and none of the
// consequence copy the dedicated action carries. Turning a macro back on now
// only happens through the `activate` action below, which sends nothing but
// the id and `is_active: true`.
/** @param {FormData} form */
function readValues(form) {
  return {
    title: form.get('title')?.toString() ?? '',
    body: form.get('body')?.toString() ?? '',
    scope: form.get('scope')?.toString() ?? '',
    // The actions. Blank means "no change"; the two lists are sent whole, and
    // their options always include what the macro already carries (see
    // `optionsWithStored`), so a save never drops one by omission.
    set_status: form.get('set_status')?.toString() ?? '',
    set_priority: form.get('set_priority')?.toString() ?? '',
    set_assignees: form.getAll('set_assignees').map(String).filter(Boolean),
    add_tags: form.getAll('add_tags').map(String).filter(Boolean)
  };
}

/** @type {import('./$types').Actions} */
export const actions = {
  // Every signed-in member may create their own personal macro here.
  // `can_create_org` only decides whether the form offers `scope=org` at
  // all; `_resolve_scope_and_owner` re-derives admin status server-side and
  // is what actually turns a non-admin's org-scope attempt into 403.
  async create(event) {
    const form = await event.request.formData();
    const values = readValues(form);
    try {
      await createMacro(event, values);
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, {
          create: { error: 'Only an admin can create a macro shared with everyone.' }
        });
      }
      return fail(400, { create: { error: readableError(err, 'Could not add the macro.') } });
    }
    return { created: true };
  },

  async update(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    const values = readValues(form);
    try {
      await updateMacro(event, id, values);
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, {
          update: { error: 'Only an admin can change a macro shared with everyone.' }
        });
      }
      if (err?.status === 404) {
        // A personal macro belonging to someone else answers 404 on purpose:
        // a 403 would confirm the row exists. Say the same thing back.
        return fail(404, { update: { error: 'That macro is not yours to change.' } });
      }
      return fail(400, { update: { error: readableError(err, 'Could not save the macro.') } });
    }
    return { updated: true };
  },

  async delete(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    try {
      await deleteMacro(event, id);
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, {
          delete: { error: 'Only an admin can remove a macro shared with everyone.' }
        });
      }
      if (err?.status === 404) {
        // Same reasoning as `update`: the row not being yours reads the same
        // as the row not existing, on purpose.
        return fail(404, { delete: { error: 'That macro is not yours to remove.' } });
      }
      return fail(400, { delete: { error: readableError(err, 'Could not remove the macro.') } });
    }
    return { deleted: true };
  },

  // A macro's own action, not a reuse of `update`. `readValues` builds a
  // full macro payload from the form, and this control only ever submits an
  // id: posting through `update` would read `title`/`body` back as `''`
  // (FormData has no key for either, `readValues` falls back to an empty
  // string) and `buildBody` would then throw ("A macro needs a title.")
  // before a request was even made. Calling `activateMacro` sidesteps
  // `readValues`/`buildBody` entirely, so the PATCH body is
  // `{ is_active: true }` and nothing else.
  async activate(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    try {
      await activateMacro(event, id);
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, {
          activate: { error: 'Only an admin can turn on a macro shared with everyone.' }
        });
      }
      if (err?.status === 404) {
        // Same reasoning as `update`/`delete`: the row not being yours reads
        // the same as the row not existing, on purpose.
        return fail(404, { activate: { error: 'That macro is not yours to turn on.' } });
      }
      return fail(400, { activate: { error: readableError(err, 'Could not turn the macro on.') } });
    }
    return { activated: true };
  }
};
