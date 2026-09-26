/**
 * Custom fields: the wiring behind `/settings/custom-fields`.
 *
 * Server-only. Reads `GET /custom-fields/` (no filter, so it returns every
 * definition including the turned-off ones the page dims). Each row carries the
 * computed `records_missing_value`: how many records of that target_model
 * predate the field and hold no value for it, and the response includes a
 * `totals` block { count, active, models_extended, required_with_gaps } for the
 * stat cards. Both are org-scoped server-side; the page never counts records.
 *
 * Create, edit, turn off and turn back on are all wired below. `key`,
 * `target_model` and `field_type` are only ever sent on create: values live in
 * each record's `custom_fields` JSON keyed by `key` (see the module docstring
 * in `backend/common/custom_fields.py`), so changing `key` orphans them,
 * changing `target_model` leaves them on the old entity, and changing
 * `field_type` reinterprets values written under the old one.
 * `CustomFieldDefinitionSerializer.validate()` freezes all three server-side
 * too; this module just avoids offering the doomed edit in the first place.
 * "Turn off" is `DELETE /custom-fields/<id>/`, a soft delete
 * (`CustomFieldDefinitionDetailView.delete` flips `is_active`, never removes
 * the row or the values on records that already carry it). "Turn on" is the
 * same `updateCustomField` PUT with only `is_active: true`, since the
 * detail view has no separate reactivate endpoint.
 */
import { apiRequest } from '$lib/api-helpers.js';
import { viewerIsAdmin } from './organization.js';

/**
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @returns {Promise<{ fields: any[], totals: any, can_edit: boolean }>}
 */
export async function getCustomFields({ cookies }) {
  const resp = await apiRequest('/custom-fields/', {}, { cookies });
  return {
    fields: resp.definitions ?? [],
    totals: resp.totals ?? {
      count: 0,
      active: 0,
      models_extended: 0,
      required_with_gaps: 0
    },
    can_edit: viewerIsAdmin(cookies)
  };
}

/** The fields the serializer accepts on create. `org` is set from the JWT
 *  server-side and `records_missing_value` is computed, so neither appears. */
const CREATE_FIELDS = [
  'target_model',
  'key',
  'label',
  'field_type',
  'is_required',
  'is_filterable',
  'display_order',
  'is_active'
];

/** Edit omits the three fields `CustomFieldDefinitionSerializer.validate()`
 *  freezes after creation. Values live in each record's `custom_fields` JSON
 *  keyed by `key` (see `backend/common/custom_fields.py`): changing `key`
 *  orphans them, changing `target_model` leaves them on the old entity, and
 *  changing `field_type` reinterprets values written under the old one. The
 *  backend refuses all three with a 400; not offering them is better than
 *  teaching it through a failed save. */
const FROZEN_AFTER_CREATE = ['key', 'target_model', 'field_type'];
const UPDATE_FIELDS = CREATE_FIELDS.filter((f) => !FROZEN_AFTER_CREATE.includes(f));

/** "Very High" to "very-high". Only ever applied to a NEW dropdown option: an
 *  existing option's value is stored on every record that uses it and must
 *  survive a relabel untouched. */
function slugifyOptionValue(label) {
  return label
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/**
 * Shape the request body from an allow-list.
 *
 * @param {string[]} allowed
 * @param {{ [key: string]: any }} values
 */
function buildBody(allowed, values) {
  /** @type {Record<string, any>} */
  const body = {};
  for (const field of allowed) {
    if (values[field] === undefined) continue;
    body[field] = values[field];
  }
  if (body.display_order !== undefined) body.display_order = Number(body.display_order) || 0;
  for (const flag of ['is_required', 'is_filterable', 'is_active']) {
    if (body[flag] !== undefined) body[flag] = Boolean(body[flag]);
  }

  // `options` is only legal on a dropdown. `validate_definition_options`
  // rejects a non-empty options list on any other type, so this is not
  // defensive noise: sending it turns a valid text field into a 400.
  if (values.field_type === 'dropdown') {
    const rows = (values.options ?? [])
      .map((/** @type {any} */ o) => ({
        value: o.value ? String(o.value) : slugifyOptionValue(String(o.label ?? '')),
        label: String(o.label ?? '').trim()
      }))
      .filter((/** @type {any} */ o) => o.label && o.value);
    if (!rows.length) {
      throw new Error('A dropdown needs at least one option.');
    }
    const seen = new Set();
    for (const row of rows) {
      if (seen.has(row.value)) {
        throw new Error(`Two options would both be stored as "${row.value}". Rename one.`);
      }
      seen.add(row.value);
    }
    body.options = rows;
  }

  return body;
}

/** @param {{ cookies: import('@sveltejs/kit').Cookies }} event */
export async function createCustomField({ cookies }, values) {
  const body = buildBody(CREATE_FIELDS, values);
  return await apiRequest('/custom-fields/', { method: 'POST', body }, { cookies });
}

/** @param {{ cookies: import('@sveltejs/kit').Cookies }} event */
export async function updateCustomField({ cookies }, id, values) {
  if (!id) throw new Error('Which field? No field id was given.');
  const body = buildBody(UPDATE_FIELDS, values);
  return await apiRequest(`/custom-fields/${id}/`, { method: 'PUT', body }, { cookies });
}

/**
 * Turn a field off.
 *
 * `CustomFieldDefinitionDetailView.delete` is a soft delete: it flips
 * `is_active` and leaves every stored value readable. The UI says "Turn off"
 * for that reason; calling it Delete would promise something the backend does
 * not do.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function deactivateCustomField({ cookies }, id) {
  if (!id) throw new Error('Which field? No field id was given.');
  return await apiRequest(`/custom-fields/${id}/`, { method: 'DELETE' }, { cookies });
}
