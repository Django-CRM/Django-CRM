/**
 * The security audit log: the wiring behind `/settings/audit-log`.
 *
 * Server-only. `GET /api/org/audit-log/` is admin-only
 * (`common/views/audit_log_views.py`), so a member's 403 folds into
 * `forbidden` and the page says "Admins only" instead of crashing, the shape
 * `listWebhooks` uses. A 400 is a filter the API refused (a hand-edited URL);
 * the page shows the reason beside an empty list rather than an error page.
 *
 * Filters travel in the page URL and are passed on as they are. The API is
 * what validates them.
 */
import { apiRequest } from '$lib/api-helpers.js';

/** Entries per page. */
export const AUDIT_PAGE = 25;

/** The query parameters the API filters on. Nothing else is forwarded. */
export const AUDIT_FILTERS = /** @type {const} */ (['event_type', 'actor', 'from', 'to']);

/**
 * The filters set in the page URL, blank ones left out.
 * @param {URLSearchParams} params
 * @returns {Record<string, string>}
 */
export function auditFilters(params) {
  /** @type {Record<string, string>} */
  const out = {};
  for (const key of AUDIT_FILTERS) {
    const value = params.get(key)?.trim();
    if (value) out[key] = value;
  }
  return out;
}

/**
 * One page of the caller's org's audit log.
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {Record<string, string>} filters
 * @param {number} offset
 */
export async function listAuditLog({ cookies }, filters, offset = 0) {
  const query = new URLSearchParams({
    ...filters,
    limit: String(AUDIT_PAGE),
    offset: String(offset)
  });
  const page = { filters, offset, pageSize: AUDIT_PAGE };
  let resp;
  try {
    resp = await apiRequest(`/org/audit-log/?${query}`, {}, { cookies });
  } catch (/** @type {any} */ err) {
    if (err?.status === 403) return { forbidden: true };
    if (err?.status === 400) {
      return {
        forbidden: false,
        ...page,
        error: err.message || 'That filter is not valid.',
        entries: [],
        count: 0,
        eventTypes: []
      };
    }
    throw err;
  }
  return {
    forbidden: false,
    ...page,
    error: null,
    entries: resp?.results ?? [],
    count: resp?.count ?? 0,
    eventTypes: resp?.event_types ?? []
  };
}
