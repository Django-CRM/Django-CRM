import { fail, redirect } from '@sveltejs/kit';
import { getTask, setTaskDone, updateTask, deleteTask, addTaskNote } from '$lib/server/v2/tasks.js';
import { readableError } from '$lib/server/v2/form-errors.js';
import { isOrgAdmin } from '$lib/admin.js';

/**
 * One task.
 *
 * The mock never had this page, the list was the whole module, but the
 * contact page has been rendering a Tasks rail with nowhere to click since
 * contacts were wired, and a comment thread has nowhere else to live.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, locals, params }) {
  const detail = await getTask({ cookies }, params.id);
  return {
    ...detail,
    // Decides what renders, never what is allowed. The API refuses a delete
    // from an assignee with a 403 whatever this says.
    canDelete: isOrgAdmin(/** @type {any} */ (locals).profile)
  };
}

/** @type {import('./$types').Actions} */
export const actions = {
  /** Tick it off, or reopen it. Same one-field PATCH as the list row. */
  toggle: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const done = form.get('done')?.toString() === 'true';
    try {
      await setTaskDone({ cookies }, params.id, done);
    } catch (/** @type {any} */ err) {
      return fail(400, { error: readableError(err, 'Could not change the status.') });
    }
    return { done };
  },

  /**
   * Comment on the task, with an optional file. Anyone who may open the task may
   * add one. The DRF view enforces the same `assert_task_access` gate.
   *
   * A task accepts a file on its own: `TaskDetailView.post` saves the attachment
   * in a block separate from the comment, so this refuses only the empty case,
   * nothing typed and nothing picked. Not `updateTask`: comments and files go to
   * the same URL by POST (see `addTaskNote`); routing them through the PATCH body
   * would make them task fields.
   */
  comment: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const body = form.get('comment')?.toString().trim() ?? '';

    const picked = form.get('attachment');
    const file =
      picked && typeof picked === 'object' && 'size' in picked && picked.size > 0 ? picked : null;

    if (!body && !file) {
      return fail(400, { error: 'Write a comment or attach a file first.' });
    }

    try {
      await addTaskNote({ cookies }, params.id, body, file);
    } catch (/** @type {any} */ err) {
      return fail(400, { error: readableError(err, 'Could not post that comment.') });
    }
    return { commented: true };
  },

  /**
   * Hand the task to someone else.
   *
   * `assigned_to` is many-to-many and the API clears it whenever the key is
   * present, so this sends the whole list. A single-select posting one id
   * would drop every co-assignee, which is a data loss that only shows up
   * against real records. The seeded org has tasks with two.
   */
  assign: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const ids = form
      .getAll('assigned_to')
      .map((value) => value.toString())
      .filter(Boolean);
    try {
      await updateTask({ cookies }, params.id, { assigned_to: ids });
    } catch (/** @type {any} */ err) {
      return fail(400, { error: readableError(err, 'Could not reassign this task.') });
    }
    return { assigned: true };
  },

  /**
   * Destroy it.
   *
   * Admins and the creator. An assignee gets a 403 from the API and sees it
   * here rather than a button that quietly does nothing.
   */
  delete: async ({ cookies, params, request }) => {
    await request.formData();
    try {
      await deleteTask({ cookies }, params.id);
    } catch (/** @type {any} */ err) {
      return fail(err?.status === 403 ? 403 : 400, {
        error:
          err?.status === 403
            ? 'Only an admin or whoever created this task can delete it.'
            : readableError(err, 'Could not delete this task.')
      });
    }
    redirect(303, '/tasks');
  }
};
