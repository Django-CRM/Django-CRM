import { describe, it, expect } from 'vitest';
import { readObjectForm, readFieldForm } from './object-settings.js';

function formOf(values: Record<string, string | string[]>) {
  const form = new FormData();
  for (const [name, value] of Object.entries(values)) {
    for (const v of Array.isArray(value) ? value : [value]) form.append(name, v);
  }
  return form;
}

describe('readFieldForm', () => {
  it('never sends a key, type, target or many when editing a field', () => {
    const form = formOf({
      key: 'x',
      field_type: 'number',
      target: 'lead',
      many: 'true',
      label: 'Price'
    });
    expect(readFieldForm(form, { creating: false })).toEqual({
      label: 'Price',
      is_required: false
    });
  });

  it('sends target and many, and no options, for a new link field', () => {
    const form = formOf({
      key: 'owner',
      field_type: 'link',
      label: ' Owner ',
      target: 'contact',
      many: 'true',
      is_required: 'true',
      option_value: ['a'],
      option_label: ['A']
    });
    expect(readFieldForm(form, { creating: true })).toEqual({
      key: 'owner',
      field_type: 'link',
      label: 'Owner',
      target: 'contact',
      many: true
    });
  });

  it('sends many false for a link that is not many', () => {
    const form = formOf({
      key: 'o',
      field_type: 'link',
      label: 'O',
      target: 'lead',
      many: 'false'
    });
    expect(readFieldForm(form, { creating: true }).many).toBe(false);
  });

  it('zips dropdown options by position', () => {
    const form = formOf({
      key: 'stage',
      field_type: 'dropdown',
      label: 'Stage',
      option_value: ['new', ''],
      option_label: ['New', 'Very High']
    });
    expect(readFieldForm(form, { creating: true }).options).toEqual([
      { value: 'new', label: 'New' },
      { value: 'very-high', label: 'Very High' }
    ]);
  });

  it('keeps a choice whose label slugifies to nothing, with a unique generated value', () => {
    const form = formOf({
      key: 'stage',
      field_type: 'dropdown',
      label: 'Stage',
      option_value: ['option_1', '', '', ''],
      option_label: ['Kept', '!!!', 'Продано', '   ']
    });
    expect(readFieldForm(form, { creating: true }).options).toEqual([
      { value: 'option_1', label: 'Kept' },
      { value: 'option_2', label: '!!!' },
      { value: 'option_3', label: 'Продано' }
    ]);
  });

  it('leaves display_order out when it is blank', () => {
    const form = formOf({ label: 'Price', display_order: '' });
    expect(readFieldForm(form, { creating: false })).not.toHaveProperty('display_order');
  });

  it('keeps the options of a dropdown when editing, and sends display_order as an integer', () => {
    const form = formOf({
      label: 'Stage',
      field_type: 'dropdown',
      option_value: ['new'],
      option_label: ['Newer'],
      display_order: '3',
      is_required: 'true'
    });
    expect(readFieldForm(form, { creating: false })).toEqual({
      label: 'Stage',
      is_required: true,
      display_order: 3,
      options: [{ value: 'new', label: 'Newer' }]
    });
  });

  it('ignores keys the form should not carry', () => {
    const form = formOf({
      key: 'x',
      field_type: 'text',
      label: 'L',
      org: 'o',
      id: 'i',
      is_active: 'false',
      created_by: 'c'
    });
    expect(Object.keys(readFieldForm(form, { creating: true })).sort()).toEqual([
      'field_type',
      'is_required',
      'key',
      'label'
    ]);
  });
});

describe('readObjectForm', () => {
  it('sends Name for a blank name_label when editing, and leaves it out when creating', () => {
    const base = {
      key: 'property',
      label: 'Property',
      label_plural: 'Properties',
      name_label: ' '
    };
    expect(readObjectForm(formOf(base), { creating: false })).toHaveProperty('name_label', 'Name');
    expect(readObjectForm(formOf(base), { creating: true })).not.toHaveProperty('name_label');
    expect(
      readObjectForm(formOf({ ...base, name_label: ' Address ' }), { creating: false })
    ).toHaveProperty('name_label', 'Address');
  });

  it('sends only record_access when the shape is locked', () => {
    const form = formOf({
      customizable: 'false',
      label: 'Property',
      label_plural: 'Properties',
      record_access: 'everyone'
    });
    expect(readObjectForm(form, { creating: false })).toEqual({ record_access: 'everyone' });
  });

  it('leaves the key out when editing an object', () => {
    const form = formOf({ key: 'property', label: 'Property', label_plural: 'Properties' });
    expect(readObjectForm(form, { creating: false })).not.toHaveProperty('key');
  });

  it('sends the key when creating, trimmed fields, and leaves a blank name_label out', () => {
    const form = formOf({
      key: ' property ',
      label: ' Property ',
      label_plural: 'Properties',
      name_label: '  ',
      description: ' Homes ',
      record_access: 'everyone',
      org: 'x',
      is_active: 'true'
    });
    expect(readObjectForm(form, { creating: true })).toEqual({
      key: 'property',
      label: 'Property',
      label_plural: 'Properties',
      description: 'Homes',
      record_access: 'everyone'
    });
  });

  it('falls back to own for an unknown access value', () => {
    const form = formOf({ label: 'P', label_plural: 'Ps', record_access: 'admin' });
    expect(readObjectForm(form, { creating: false }).record_access).toBe('own');
  });
});
