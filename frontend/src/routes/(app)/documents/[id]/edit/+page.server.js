import { error, fail, redirect } from '@sveltejs/kit';
import {
  getDocumentForEdit,
  updateDocument,
  deleteDocument,
  STATUS_CHOICES
} from '$lib/server/v2/documents.js';
import { readableError } from '$lib/server/v2/form-errors.js';
import { sameIds } from '$lib/v2/pickers.js';

/**
 * Editing a document: rename, archive/restore, and manage who can open it.
 *
 * Writing is as narrow as deleting: the uploader or an admin. `getDocumentForEdit`
 * returns `can_edit: false` for anyone else, including a user the document is
 * merely shared with, who can open it but not change it, so the form is never
 * drawn for someone the PUT would refuse. Both writes are gated again by the
 * backend (`_may_write` / `_may_delete`), and the detail fetch is org-scoped, so
 * a bad or foreign id 404s here.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load(event) {
  try {
    return await getDocumentForEdit(event, event.params.id);
  } catch (/** @type {any} */ err) {
    if (err?.status === 404) {
      error(404, 'That document does not exist, or it belongs to another org.');
    }
    throw err;
  }
}

/** @type {import('./$types').Actions} */
export const actions = {
  save: async (event) => {
    const form = await event.request.formData();

    const title = form.get('title')?.toString().trim() ?? '';
    const statusRaw = form.get('status')?.toString() ?? 'active';
    const status = STATUS_CHOICES.includes(statusRaw) ? statusRaw : 'active';
    const shared_to = form.getAll('shared_to').map((v) => v.toString());
    const teams = form.getAll('teams').map((v) => v.toString());
    const values = { title, status, shared_to, teams };

    // Sharing is sent only when it changed. The page renders a checkbox per
    // ACTIVE person and per team, a `*_original` hidden input for each stored
    // share, and a hidden `shared_to` for each share with no checkbox (a person
    // deactivated since), so equal lists mean nobody touched sharing and a PUT
    // still names every share it should keep. Sending the lists anyway is a
    // PUT, and when the people and teams list failed to load (no team
    // checkboxes at all) that PUT would empty the team list. See `updateDocument`.
    const sharingChanged =
      !sameIds(shared_to, form.getAll('shared_to_original').map(String)) ||
      !sameIds(teams, form.getAll('teams_original').map(String));

    // Replacing the file is optional, so an empty input is "leave it alone",
    // not "clear it". A browser sends a zero-byte File for an untouched file
    // input, which is why size is checked and not just the type.
    const picked = form.get('document_file');
    const file = picked instanceof File && picked.size > 0 ? picked : null;

    if (!title) {
      return fail(400, { values, error: 'Give the document a title.' });
    }

    try {
      await updateDocument(
        event,
        event.params.id,
        sharingChanged ? { ...values, file } : { title, status, file }
      );
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, { values, error: 'Only the owner or an admin can change this document.' });
      }
      return fail(400, {
        values,
        error: readableError(err, 'Could not save this document.')
      });
    }

    redirect(303, '/documents');
  },

  delete: async (event) => {
    try {
      await deleteDocument(event, event.params.id);
    } catch (/** @type {any} */ err) {
      if (err?.status === 403) {
        return fail(403, { error: 'Only the owner or an admin can delete this document.' });
      }
      // Already gone is the outcome the caller wanted; treat 404 as done.
      if (err?.status === 404) redirect(303, '/documents');
      return fail(400, { error: readableError(err, 'Could not delete this document.') });
    }

    redirect(303, '/documents');
  }
};
