/**
 * Form readers for the custom object settings pages. Each builds an
 * allow-listed API body, never a spread of the form. They live here, not in
 * the route files, so vitest can load them.
 */
import type { FormBody } from '#lib/api-helpers.js';
import type { components } from '#lib/enterprise-api-schema.js';

type Enterprise = components['schemas'];

function text(form: FormData, name: string): string {
  return form.get(name)?.toString().trim() ?? '';
}

/**
 * "Very High" to "very-high". Only a new option row gets a generated value;
 * an existing one is stored on records and travels back unchanged.
 */
function slugify(label: string): string {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export function readObjectForm(
  form: FormData,
  { creating }: { creating: boolean }
): FormBody<Enterprise['ObjectCreateRequest']> {
  // The server refuses a mixed PATCH whole, so when the shape is locked only the
  // allowed key travels.
  if (!creating && form.get('customizable') === 'false') {
    return { record_access: text(form, 'record_access') === 'everyone' ? 'everyone' : 'own' };
  }
  const body: FormBody<Enterprise['ObjectCreateRequest']> = {
    label: text(form, 'label'),
    label_plural: text(form, 'label_plural'),
    description: text(form, 'description'),
    record_access: text(form, 'record_access') === 'everyone' ? 'everyone' : 'own'
  };
  if (creating) body.key = text(form, 'key');
  const nameLabel = text(form, 'name_label');
  if (nameLabel) body.name_label = nameLabel;
  else if (!creating) body.name_label = 'Name';
  return body;
}

function readOptions(form: FormData): { value: string; label: string }[] {
  const values = form.getAll('option_value').map((v) => v.toString());
  const labels = form.getAll('option_label').map((v) => v.toString().trim());
  const used = new Set(values.filter(Boolean));
  let next = 1;
  const rows: { value: string; label: string }[] = [];
  labels.forEach((label, i) => {
    if (!label) return;
    let value = values[i];
    if (!value) {
      // A label with no Latin letters or digits slugifies to nothing; keep the choice anyway.
      value = slugify(label);
      while (!value || used.has(value)) value = `option_${next++}`;
      used.add(value);
    }
    rows.push({ value, label });
  });
  return rows;
}

export function readFieldForm(
  form: FormData,
  { creating }: { creating: boolean }
): FormBody<Enterprise['FieldCreateRequest']> {
  const fieldType = text(form, 'field_type');
  const isLink = fieldType === 'link';
  const body: FormBody<Enterprise['FieldCreateRequest']> = { label: text(form, 'label') };
  if (creating) {
    body.key = text(form, 'key');
    body.field_type = fieldType;
    if (isLink) {
      body.target = text(form, 'target');
      body.many = text(form, 'many') === 'true';
    }
  }
  if (!isLink) body.is_required = form.get('is_required') === 'true';
  const order = text(form, 'display_order');
  if (order !== '' && Number.isFinite(Number(order)))
    body.display_order = Math.trunc(Number(order));
  if (fieldType === 'dropdown') body.options = readOptions(form);
  return body;
}
