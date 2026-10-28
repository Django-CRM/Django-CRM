import { fail, redirect } from '@sveltejs/kit';
import {
  addRecordNote,
  checkId,
  checkKey,
  deleteAttachment,
  deleteRecord,
  deleteRecordNote,
  editRecordNote,
  recordPage,
  uploadAttachment
} from '#lib/server/v2/custom-objects.js';
import { dismissMatch, loadMatches, undismissMatch } from '#lib/server/v2/real-estate.js';
import { pairForDisplay } from '#lib/server/v2/record-fields.js';
import { readableError } from '#lib/server/v2/form-errors.js';
import { commentAction } from '#lib/server/v2/record-notes.js';
import { buildRecordTimeline } from '#lib/v2/record-timeline.js';
import { isOrgAdmin } from '#lib/admin.js';
import { uploadTooLarge } from '#lib/v2/custom-objects.js';
import { apiStatus } from '#lib/api-errors.js';
import type { Actions, PageServerLoad } from './$types';

const refusalCode = (err: unknown) => {
  const status = apiStatus(err);
  return status !== undefined && status >= 400 && status < 500 ? status : 400;
};

export const load: PageServerLoad = async ({ cookies, params, locals, url }) => {
  const page = await recordPage({ cookies }, params.key, params.id);
  const fields = page.definition.fields ?? [];
  const matches =
    page.definition.matches === true
      ? await loadMatches({ cookies }, params.id, url.searchParams.get('dismissed') === '1')
      : null;
  return {
    ...page,
    matches,
    details: pairForDisplay(
      fields.filter((f) => f.field_type !== 'link'),
      page.record.custom_fields
    ),
    linkFields: fields.filter((f) => f.field_type === 'link'),
    timeline: buildRecordTimeline(page.record, {
      email: locals.user?.email,
      isAdmin: isOrgAdmin(locals.profile)
    })
  };
};

export const actions: Actions = {
  dismiss: dismissMatch,
  undismiss: undismissMatch,

  async comment(event) {
    const key = checkKey(event.params.key);
    const post = commentAction((e, id, comment) => addRecordNote(e, key, id, comment));
    return await post(event);
  },

  async editNote({ cookies, params, request }) {
    const key = checkKey(params.key);
    const form = await request.formData();
    const note = checkId(String(form.get('note') ?? ''));
    const comment = form.get('comment')?.toString().trim() ?? '';
    if (!comment)
      return fail(400, { noteFailure: { id: note, message: 'A note cannot be empty.' } });
    try {
      await editRecordNote({ cookies }, key, note, comment);
    } catch (err) {
      return fail(refusalCode(err), {
        noteFailure: { id: note, message: readableError(err, 'Could not save that note.') }
      });
    }
    return { noteEdited: true };
  },

  async deleteNote({ cookies, params, request }) {
    const key = checkKey(params.key);
    const note = checkId(String((await request.formData()).get('note') ?? ''));
    try {
      await deleteRecordNote({ cookies }, key, note);
    } catch (err) {
      return fail(refusalCode(err), {
        noteFailure: { id: note, message: readableError(err, 'Could not delete that note.') }
      });
    }
    return { noteDeleted: true };
  },

  async delete({ cookies, params }) {
    const key = checkKey(params.key);
    const id = checkId(params.id);
    try {
      await deleteRecord({ cookies }, key, id);
    } catch (err) {
      return fail(refusalCode(err), {
        deleteError: readableError(err, 'Could not delete this record.')
      });
    }
    redirect(303, `/objects/${key}`);
  },

  async upload({ cookies, params, request }) {
    const key = checkKey(params.key);
    const id = checkId(params.id);
    const picked = (await request.formData()).get('file');
    if (!picked || typeof picked !== 'object' || !('size' in picked) || picked.size === 0) {
      return fail(400, { uploadError: 'Choose a file to upload.' });
    }
    const tooLarge = uploadTooLarge(picked);
    if (tooLarge) return fail(400, { uploadError: tooLarge });
    try {
      await uploadAttachment({ cookies }, key, id, picked);
    } catch (err) {
      return fail(refusalCode(err), {
        uploadError: readableError(err, 'Could not upload that file.')
      });
    }
    return { uploaded: true };
  },

  async deleteFile({ cookies, params, request }) {
    const key = checkKey(params.key);
    const id = checkId(params.id);
    const attachment = checkId(String((await request.formData()).get('attachment') ?? ''));
    try {
      await deleteAttachment({ cookies }, key, id, attachment);
    } catch (err) {
      return fail(refusalCode(err), {
        fileError: readableError(err, 'Could not delete that file.')
      });
    }
    return { fileDeleted: true };
  }
};
