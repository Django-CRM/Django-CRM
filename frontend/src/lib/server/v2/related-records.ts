/**
 * The Related section on a built-in record's page: the load and the two form
 * actions every detail page shares.
 */

import { fail, type Action, type Cookies, type RequestEvent } from '@sveltejs/kit';
import {
  getRelated,
  addRelated,
  removeRelated,
  checkKey,
  checkFieldKey,
  checkId,
  type RelatedRecords
} from '#lib/server/v2/custom-objects.js';
import { readableError } from '#lib/server/v2/form-errors.js';
import { apiStatus } from '#lib/api-errors.js';

const REFUSED = 'No such record, or you cannot open it.';

/**
 * @param to A built-in registry key, or `obj:<object key>`.
 * @returns Null on any failure, including no custom objects.
 */
export function loadRelated(
  event: { cookies: Cookies },
  to: string,
  id: string
): Promise<RelatedRecords | null> {
  return getRelated(event, to, id);
}

/**
 * The record and link field from a form. The field may be `<object key>:<field
 * key>` (the add select), of which only the field key goes to the API.
 *
 * @returns Null for anything malformed.
 */
function readLink(form: FormData): { record: string; field: string } | null {
  const record = form.get('record')?.toString() ?? '';
  const raw = form.get('field')?.toString() ?? '';
  const parts = raw.split(':');
  if (parts.length > 2) return null;
  try {
    if (parts.length === 2) checkKey(parts[0]);
    return { record: checkId(record), field: checkFieldKey(parts[parts.length - 1]) };
  } catch {
    return null;
  }
}

/**
 * @param to A target, or a function that reads it from the request.
 * @param needsRecord Refuse an empty record before any API call.
 */
function writer(
  to: RelatedTarget,
  call: (event: RequestEvent, link: Parameters<typeof addRelated>[1]) => Promise<unknown>,
  fallback: string,
  needsRecord = false
) {
  return async (event: RequestEvent) => {
    const form = await event.request.formData();
    if (needsRecord && !form.get('record')?.toString()) {
      return fail(400, { related: { error: 'Choose a record to add.' } });
    }
    const link = readLink(form);
    if (!link) return fail(400, { related: { error: REFUSED } });
    try {
      await call(event, {
        to: typeof to === 'function' ? to(event) : to,
        id: event.params.id ?? '',
        ...link
      });
    } catch (err) {
      const status = apiStatus(err);
      const code = status !== undefined && [400, 403, 404].includes(status) ? status : 400;
      return fail(code, { related: { error: readableError(err, fallback) } });
    }
    return { related: { done: true } };
  };
}

type RelatedTarget = string | ((event: RequestEvent) => string);

/**
 * @param to A built-in registry key, or a function of the request for a page
 *   whose target is only known per request (`obj:<params.key>`).
 */
export function relatedActions(to: RelatedTarget): { relatedAdd: Action; relatedRemove: Action } {
  return {
    relatedAdd: writer(to, addRelated, 'Could not add this record.', true),
    relatedRemove: writer(to, removeRelated, 'Could not remove this record.')
  };
}
