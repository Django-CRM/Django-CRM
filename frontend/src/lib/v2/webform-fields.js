/**
 * Pure helpers for the web form field editor.
 *
 * Kept out of the component so the ordering rules can be tested without a DOM,
 * and so the drag path (desktop) and the button path (touch) provably produce
 * the same list. `order` is always derived from list position here and is also
 * derived from list position server-side (`WebFormDetailSerializer._write_fields`
 * enumerates the rows it is given), so a client that sends its own `order`
 * cannot reorder someone else's form fields by lying about it. That makes
 * `withOrder` a convenience for the page's own preview, not a security control.
 *
 * `isFieldComplete` is likewise a UX gate: it stops the page offering Save on a
 * half-filled row. The same two rules are enforced in `WebFormFieldSerializer`
 * (a `label` is required by the model, and the exactly-one-target rule is both a
 * serializer check and a database CheckConstraint), so a caller who skips this
 * page is rejected by the API, not accepted by it.
 */

/**
 * The Lead columns a web form may collect, mirrored 1:1 from
 * `backend/webforms/constants.py::LEAD_FIELD_CHOICES`, the same way
 * `$lib/v2/enums.js` mirrors the vocabularies in `common/utils.py`.
 *
 * A whitelist rather than "any field on Lead": the person filling this in is
 * an anonymous stranger, so which columns they can reach is a decision
 * somebody made. Assignment, pipeline stage, probability and deal value are
 * deliberately absent.
 *
 * Two of these are easy to confuse and mean different things. `title` is the
 * lead's subject line ("Website enquiry"); `salutation` is the honorific
 * ("Ms", "Dr"). The labels below say "Subject" and "Salutation" for that
 * reason, and the legacy endpoint's `title` request parameter maps to
 * `salutation`, not to this `title`.
 *
 * Drift is contained rather than prevented: `WebFormFieldSerializer
 * .validate_lead_field` refuses anything outside the backend's own list, so a
 * stale entry here produces a clean 400 rather than a bad write. Flutter needs
 * the same list, in `mobile/lib/models/web_form.dart`.
 */
export const WEBFORM_LEAD_FIELDS = [
  { value: 'salutation', label: 'Salutation' },
  { value: 'first_name', label: 'First name' },
  { value: 'last_name', label: 'Last name' },
  { value: 'email', label: 'Email' },
  { value: 'phone', label: 'Phone' },
  { value: 'company_name', label: 'Company name' },
  { value: 'job_title', label: 'Job title' },
  { value: 'website', label: 'Website' },
  { value: 'title', label: 'Subject' },
  { value: 'description', label: 'Message' },
  { value: 'city', label: 'City' },
  { value: 'state', label: 'State' },
  { value: 'country', label: 'Country' },
  { value: 'postcode', label: 'Postal code' },
  { value: 'industry', label: 'Industry' }
];

/**
 * The one field a form must collect before it can be published, mirroring
 * `REQUIRED_LEAD_FIELD`. It is the key the submission service dedupes on;
 * without it a second submission from the same address hits Lead's
 * `UniqueConstraint(Lower("email"), "org")` and fails.
 */
export const REQUIRED_LEAD_FIELD = 'email';

/** @param {string} value */
export function leadFieldLabel(value) {
  return WEBFORM_LEAD_FIELDS.find((f) => f.value === value)?.label ?? value;
}

/**
 * Whether this list would survive `WebFormPublishView`'s check.
 *
 * A display hint, and the page uses it to explain the disabled Publish button
 * before the round trip. The server runs the same check and is what actually
 * decides; this only saves someone a 400 that says the same thing.
 *
 * @param {{ source?: string, lead_field?: string, custom_field?: string | null, label?: string }[]} fields
 */
export function hasRequiredField(fields) {
  return fields.some((f) => f.source === 'lead' && f.lead_field === REQUIRED_LEAD_FIELD);
}

/**
 * Move the item at `index` by `delta`, returning a new array.
 *
 * A move off either end returns the list unchanged rather than wrapping or
 * clamping to the nearest slot: the up button on the first row is disabled, so
 * reaching here at all means something else is wrong, and silently reordering
 * would hide it.
 *
 * @template T
 * @param {T[]} fields
 * @param {number} index
 * @param {number} delta
 * @returns {T[]}
 */
export function moveField(fields, index, delta) {
  const target = index + delta;
  if (target < 0 || target >= fields.length) return [...fields];
  const next = [...fields];
  const [moved] = next.splice(index, 1);
  next.splice(target, 0, moved);
  return next;
}

/**
 * Stamp `order` from list position, discarding whatever was there.
 *
 * @template {Record<string, any>} T
 * @param {T[]} fields
 * @returns {(T & { order: number })[]}
 */
export function withOrder(fields) {
  return fields.map((field, index) => ({ ...field, order: index }));
}

/**
 * Whether a row is complete enough to save.
 *
 * @param {{ source?: string, lead_field?: string, custom_field?: string | null, label?: string }} field
 * @returns {boolean}
 */
export function isFieldComplete(field) {
  if (!field.label?.trim()) return false;
  if (field.source === 'custom') return Boolean(field.custom_field);
  return Boolean(field.lead_field);
}
