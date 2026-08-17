import { describe, it, expect } from 'vitest';
import {
  moveField,
  withOrder,
  isFieldComplete,
  hasRequiredField,
  leadFieldLabel,
  WEBFORM_LEAD_FIELDS,
  REQUIRED_LEAD_FIELD
} from './webform-fields.js';

const fields = () => [
  { source: 'lead', lead_field: 'email', label: 'Email' },
  { source: 'lead', lead_field: 'first_name', label: 'First name' },
  { source: 'lead', lead_field: 'phone', label: 'Phone' }
];

describe('moveField', () => {
  it('moves an item up', () => {
    expect(moveField(fields(), 1, -1).map((f) => f.lead_field)).toEqual([
      'first_name',
      'email',
      'phone'
    ]);
  });

  it('moves an item down', () => {
    expect(moveField(fields(), 0, 1).map((f) => f.lead_field)).toEqual([
      'first_name',
      'email',
      'phone'
    ]);
  });

  it('refuses to move the first item up', () => {
    expect(moveField(fields(), 0, -1).map((f) => f.lead_field)).toEqual([
      'email',
      'first_name',
      'phone'
    ]);
  });

  it('refuses to move the last item down', () => {
    expect(moveField(fields(), 2, 1).map((f) => f.lead_field)).toEqual([
      'email',
      'first_name',
      'phone'
    ]);
  });

  it('does not mutate the input', () => {
    const input = fields();
    moveField(input, 0, 1);
    expect(input[0].lead_field).toBe('email');
  });
});

describe('withOrder', () => {
  it('assigns order from list position', () => {
    expect(withOrder(fields()).map((f) => f.order)).toEqual([0, 1, 2]);
  });

  it('overwrites any order the caller supplied', () => {
    const tampered = fields().map((f) => ({ ...f, order: 99 }));
    expect(withOrder(tampered).map((f) => f.order)).toEqual([0, 1, 2]);
  });
});

describe('isFieldComplete', () => {
  it('accepts a lead field with a label', () => {
    expect(isFieldComplete({ source: 'lead', lead_field: 'email', label: 'Email' })).toBe(true);
  });

  it('rejects a lead field with no label', () => {
    expect(isFieldComplete({ source: 'lead', lead_field: 'email', label: '' })).toBe(false);
  });

  it('rejects a lead row with no target', () => {
    expect(isFieldComplete({ source: 'lead', lead_field: '', label: 'X' })).toBe(false);
  });

  it('accepts a custom field row', () => {
    expect(isFieldComplete({ source: 'custom', custom_field: 'abc', label: 'Budget' })).toBe(true);
  });

  it('rejects a custom row with no definition', () => {
    expect(isFieldComplete({ source: 'custom', custom_field: null, label: 'Budget' })).toBe(false);
  });
});

describe('WEBFORM_LEAD_FIELDS', () => {
  /**
   * Mirrored from `backend/webforms/constants.py::LEAD_FIELD_CHOICES`. These
   * pin the two properties that matter if the list ever drifts: the email
   * field has to exist (nothing can be published without it) and the values
   * have to be the backend's own spelling, since `validate_lead_field`
   * rejects anything else.
   */
  it('carries every field the backend whitelists, in the backend order', () => {
    expect(WEBFORM_LEAD_FIELDS.map((f) => f.value)).toEqual([
      'salutation',
      'first_name',
      'last_name',
      'email',
      'phone',
      'company_name',
      'job_title',
      'website',
      'title',
      'description',
      'city',
      'state',
      'country',
      'postcode',
      'industry'
    ]);
  });

  it('labels `title` as Subject and keeps Salutation separate', () => {
    // The two are routinely confused. `Lead.title` is the subject line, and a
    // form that put honorifics there would fill every lead's subject with "Ms".
    expect(leadFieldLabel('title')).toBe('Subject');
    expect(leadFieldLabel('salutation')).toBe('Salutation');
  });

  it('falls back to the raw value for a field it does not know', () => {
    expect(leadFieldLabel('invented_field')).toBe('invented_field');
  });

  it('offers the required field', () => {
    expect(WEBFORM_LEAD_FIELDS.some((f) => f.value === REQUIRED_LEAD_FIELD)).toBe(true);
  });
});

describe('hasRequiredField', () => {
  it('is true when an email field is present', () => {
    expect(hasRequiredField([{ source: 'lead', lead_field: 'email' }])).toBe(true);
  });

  it('is false for a form with no email field', () => {
    expect(hasRequiredField([{ source: 'lead', lead_field: 'phone' }])).toBe(false);
  });

  it('is false for an empty form', () => {
    expect(hasRequiredField([])).toBe(false);
  });

  it('does not count a custom field that happens to be labelled email', () => {
    // The publish check reads `lead_field`, so a custom definition called
    // "email" would not satisfy it. Matching that exactly keeps the page's
    // explanation from contradicting the 400 the server would send.
    expect(hasRequiredField([{ source: 'custom', custom_field: 'abc', label: 'Email' }])).toBe(
      false
    );
  });
});
