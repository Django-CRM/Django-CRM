import { fail, redirect } from '@sveltejs/kit';
import {
  checkFieldKey,
  checkKey,
  createField,
  deleteObject,
  getObject,
  listFields,
  turnOffField,
  updateField,
  updateObject
} from '#lib/server/v2/custom-objects.js';
import { readableError } from '#lib/server/v2/form-errors.js';
import { readFieldForm, readObjectForm } from '#lib/server/v2/object-settings.js';
import { setBoardField } from '#lib/server/v2/object-board.js';
import { viewerIsAdmin } from '#lib/server/v2/organization.js';
import { apiErrorBody, apiStatus } from '#lib/api-errors.js';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ cookies, params }) => {
  const key = checkKey(params.key);
  const [definition, fields] = await Promise.all([
    getObject({ cookies }, key),
    listFields({ cookies }, key)
  ]);
  return { definition, fields, can_edit: viewerIsAdmin(cookies) };
};

/**
 * Run a write and turn its failure into a `fail()` under the action's name.
 *
 * @param name The action's namespace in `form`.
 * @param forbidden What a 403 says.
 * @param fallback What an unexplained failure says.
 * @param values Echoed back on a refusal.
 */
async function attempt(
  name: string,
  forbidden: string,
  fallback: string,
  write: () => Promise<unknown>,
  values?: Record<string, unknown>
) {
  try {
    await write();
  } catch (err) {
    const detail = apiErrorBody(err)?.detail;
    if (apiStatus(err) === 403) {
      return fail(403, { [name]: { error: detail ?? forbidden } });
    }
    const error = typeof detail === 'string' ? detail : readableError(err, fallback);
    return fail(400, { [name]: { error, values } });
  }
  return null;
}

const fieldKeyOf = (form: FormData) => checkFieldKey(form.get('field_key')?.toString() ?? '');

export const actions: Actions = {
  async update(event) {
    const key = checkKey(event.params.key);
    const body = readObjectForm(await event.request.formData(), { creating: false });
    return (
      (await attempt(
        'update',
        'Only an admin can change custom objects.',
        'Could not save the object.',
        () => updateObject(event, key, body),
        body
      )) ?? { updated: true }
    );
  },

  async board(event) {
    const key = checkKey(event.params.key);
    const field = (await event.request.formData()).get('board_field')?.toString() ?? '';
    return (
      (await attempt(
        'board',
        'Only an admin can choose the board.',
        'Could not save the board.',
        () => setBoardField(event, key, field)
      )) ?? { boardSaved: true }
    );
  },

  async archive(event) {
    const key = checkKey(event.params.key);
    return (
      (await attempt(
        'archive',
        'Only an admin can archive custom objects.',
        'Could not archive the object.',
        () => deleteObject(event, key)
      )) ?? { archived: true }
    );
  },

  async restore(event) {
    const key = checkKey(event.params.key);
    return (
      (await attempt(
        'restore',
        'Only an admin can restore custom objects.',
        'Could not restore the object.',
        () => updateObject(event, key, { is_active: true })
      )) ?? { restored: true }
    );
  },

  async destroy(event) {
    const key = checkKey(event.params.key);
    const failed = await attempt(
      'destroy',
      'Only an admin can delete custom objects.',
      'Could not delete the object.',
      () => deleteObject(event, key)
    );
    if (failed) return failed;
    redirect(303, '/settings/objects');
  },

  async createField(event) {
    const key = checkKey(event.params.key);
    const body = readFieldForm(await event.request.formData(), { creating: true });
    return (
      (await attempt(
        'createField',
        'Only an admin can add fields.',
        'Could not add the field.',
        () => createField(event, key, body),
        body
      )) ?? { fieldCreated: true }
    );
  },

  async updateField(event) {
    const key = checkKey(event.params.key);
    const form = await event.request.formData();
    const fieldKey = fieldKeyOf(form);
    const body = readFieldForm(form, { creating: false });
    return (
      (await attempt(
        'updateField',
        'Only an admin can change fields.',
        'Could not save the field.',
        () => updateField(event, key, fieldKey, body),
        body
      )) ?? { fieldUpdated: true }
    );
  },

  async fieldOff(event) {
    const key = checkKey(event.params.key);
    const fieldKey = fieldKeyOf(await event.request.formData());
    return (
      (await attempt(
        'fieldOff',
        'Only an admin can turn fields off.',
        'Could not turn the field off.',
        () => turnOffField(event, key, fieldKey)
      )) ?? { fieldOff: true }
    );
  },

  async fieldOn(event) {
    const key = checkKey(event.params.key);
    const fieldKey = fieldKeyOf(await event.request.formData());
    return (
      (await attempt(
        'fieldOn',
        'Only an admin can turn fields on.',
        'Could not turn the field on.',
        () => updateField(event, key, fieldKey, { is_active: true })
      )) ?? { fieldOn: true }
    );
  }
};
