export const M2M_FIELDS = new Set(['assigned_to', 'tags']);

/**
 * Turn the bulk form into { ids, fields }. `field` names which field to set;
 * `value` is repeatable, so an m2m field arrives as an array and a scalar as a
 * single string. A Close also carries `closed_on`.
 * @param {FormData} formData
 */
export function parseBulkForm(formData) {
  const ids = formData.getAll('ids').map(String);
  const field = String(formData.get('field') ?? '');
  const values = formData.getAll('value').map(String);
  /** @type {Record<string, any>} */
  const fields = {};
  if (field) {
    fields[field] = M2M_FIELDS.has(field) ? values : (values[0] ?? '');
  }
  const closedOn = formData.get('closed_on');
  if (closedOn) fields.closed_on = String(closedOn);
  return { ids, fields };
}
