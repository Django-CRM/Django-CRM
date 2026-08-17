/**
 * Turning a DRF rejection back into a sentence.
 *
 * The API answers a bad save with `{"error": true, "errors": {"field":
 * ["message"]}}`, and `apiRequest` flattens that into an Error whose message
 * carries the JSON. Showing "Request failed" over the top of it throws away the
 * one useful thing in the response: the API already said exactly what was wrong
 * with which field.
 *
 * This lived twice in the accounts routes and was about to live twice more in
 * contacts. Two copies of a function is how the backend ended up with five
 * different opinions about who may open a record.
 */

/**
 * @param {any} err the error thrown by `apiRequest`
 * @param {string} fallback what to say when the response explained nothing
 * @returns {string} a message worth putting in front of somebody
 */
export function readableError(err, fallback) {
  /*
   * The `{"error": true, "errors": "<a whole sentence>"}` envelope, which the
   * backend uses in around three hundred places, gets read off the response
   * body before anything else.
   *
   * `apiRequest` has no branch for it. Its checks look for `errors` as an
   * OBJECT (a per-field map) and for `error` as a STRING, and this shape is
   * neither, so it falls through to the generic entry-flattener and produces
   * "errors: Add an email field before publishing." The JSON key ends up in
   * the sentence a person reads, on every one of the fifty-odd pages that
   * call this. Reading `err.body` (which `apiRequest` attaches) skips the
   * flattening entirely and gets the sentence the API actually wrote.
   *
   * A per-field map is deliberately left alone: "name: This field may not be
   * blank" needs the field name, and `apiRequest` already renders it well.
   *
   * A blank one falls to `fallback` rather than to the flattened message,
   * because the flattened message in that case is the bare string "errors:"
   * and an empty apology beside a failed save reads as though it worked.
   */
  const direct = err?.body?.errors;
  if (typeof direct === 'string') return direct.trim() || fallback;

  const message = String(err?.message ?? '');
  const start = message.indexOf('{');
  if (start === -1) return message || fallback;
  try {
    const parsed = JSON.parse(message.slice(start));
    const errors = parsed.errors ?? parsed;
    if (typeof errors === 'string') return errors;
    const first = Object.entries(errors)[0];
    if (!first) return fallback;
    const [field, detail] = first;
    const text = Array.isArray(detail) ? detail[0] : detail;
    return field === 'non_field_errors' ? String(text) : `${field}: ${text}`;
  } catch {
    return message;
  }
}
