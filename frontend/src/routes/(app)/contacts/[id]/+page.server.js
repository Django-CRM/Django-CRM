import { fail, redirect } from '@sveltejs/kit';
import { addContactNote, deleteContact, getContact } from '$lib/server/v2/contacts.js';
import { readableError } from '$lib/server/v2/form-errors.js';
import { recordDuplicates } from '$lib/server/v2/duplicates.js';

/** @type {import('./$types').PageServerLoad} */
export async function load({ cookies, params }) {
  const [contact, duplicates] = await Promise.all([
    getContact({ cookies }, params.id),
    recordDuplicates({ cookies }, 'contacts', params.id)
  ]);
  return { ...contact, duplicates };
}

/** @type {import('./$types').Actions} */
export const actions = {
  /**
   * Log a note against the contact, with an optional file. The body carries only
   * the note text and the file; who wrote it and which org it belongs to are
   * derived server-side from the JWT (see `addContactNote`), never from the form.
   *
   * A contact accepts a file on its own, unlike a lead, `ContactDetailView.post`
   * saves the attachment in a separate block from the comment, so this refuses
   * only the empty case: nothing typed and nothing picked. The DRF view enforces
   * the same access as reading the contact, so this action cannot post to a
   * contact the caller could not open.
   */
  note: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const comment = form.get('comment')?.toString().trim() ?? '';

    const picked = form.get('attachment');
    const file =
      picked && typeof picked === 'object' && 'size' in picked && picked.size > 0 ? picked : null;

    if (!comment && !file) {
      return fail(400, { message: 'Write a note or attach a file before you save.' });
    }

    try {
      await addContactNote({ cookies }, params.id, comment, file);
    } catch (/** @type {any} */ err) {
      return fail(400, { message: String(err?.message ?? 'Could not save that note.') });
    }

    return { noted: true };
  },
  /**
   * Delete this contact. The API applies the delete rule and answers 403 or 404
   * when it refuses; the page shows its sentence. On success the contact is
   * gone, so the list is where to land.
   */
  delete: async ({ cookies, params }) => {
    try {
      await deleteContact({ cookies }, params.id);
    } catch (/** @type {any} */ err) {
      const code = err?.status >= 400 && err?.status < 500 ? err.status : 400;
      return fail(code, { deleteError: readableError(err, 'Could not delete this contact.') });
    }
    redirect(303, '/contacts');
  }
};
