/**
 * Reading filters off a URL, and turning them into an API query.
 *
 * Server-only: it is imported by `+page.server.js`, never by a component.
 *
 * Two allow-lists, deliberately, not one. `readFilters` drops anything the page
 * descriptor does not declare, so a hand-typed param never becomes a variable
 * this layer carries. `buildFilterQuery` then decides what reaches the API, the
 * same shape `CREATE_FIELDS` / `buildBody` use in every other v2 module.
 *
 * Neither is the security boundary. The backend gates `include_deleted` on
 * `is_org_admin` itself (`cases/views.py:176`). These exist so the UI does not
 * silently inherit every param these endpoints grow.
 */
import { FILTERS } from '$lib/v2/filters.js';

const BOOLEANS = ['true', 'false'];
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const NUMERIC = /^\d+(\.\d+)?$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** A deal stage code, as the API derives it from a stage's label. */
const STAGE_CODE = /^[A-Z0-9_]{1,64}$/;
/** Field types whose value is an opaque id the API looks up by primary key. */
const ID_TYPES = ['person', 'tag', 'account'];

/**
 * Validated filter params for one page.
 *
 * Enumerable types are checked and an unrecognised value is DROPPED rather than
 * forwarded. Forwarding it would return an empty list with no error, which
 * reads to a person as "there is nothing here" rather than "that filter was
 * nonsense".
 *
 * Opaque ids (person, tag, account) are checked for UUID SHAPE only, not for
 * existence, which would cost a fetch. Shape is enough to matter: every list
 * endpoint answers 500 on a malformed id, because the value reaches Django as
 * `id__in=['x']` and psycopg raises before any handler sees it. Verified
 * against `/opportunities/`, `/opportunities/kanban/`, `/leads/`, `/cases/`,
 * `/accounts/` and `/tasks/`, all six of which return 500 for `?tags=x`. This
 * is not a security control, the backend remains the trust boundary; it stops
 * a stale bookmark or an edited URL from taking a page down.
 *
 * Preset-only params such as `all`, `inactive` and `visibility` are NOT handled
 * here. Each page's `load` already interprets its own, and this function
 * deliberately does not duplicate that logic.
 *
 * @param {URL} url
 * @param {string} pageKey
 * @returns {Record<string, string>}
 */
export function readFilters(url, pageKey) {
  const descriptor = FILTERS[pageKey];
  if (!descriptor) return {};

  /** @type {Record<string, string>} */
  const out = {};
  for (const field of descriptor.fields) {
    if (field.type === 'date-range' || field.type === 'number-range') {
      const valid = field.type === 'date-range' ? ISO_DATE : NUMERIC;
      for (const key of [field.gteKey, field.lteKey]) {
        const value = url.searchParams.get(/** @type {string} */ (key));
        if (value && valid.test(value)) out[/** @type {string} */ (key)] = value;
      }
      continue;
    }

    const value = url.searchParams.get(field.key);
    if (!value) continue;

    if (field.type === 'select') {
      if (field.options?.includes(value)) out[field.key] = value;
    } else if (field.type === 'boolean') {
      if (BOOLEANS.includes(value)) out[field.key] = value;
    } else if (ID_TYPES.includes(field.type)) {
      if (UUID.test(value)) out[field.key] = value;
    } else if (field.type === 'stage') {
      if (STAGE_CODE.test(value)) out[field.key] = value;
    } else {
      out[field.key] = value;
    }
  }
  return out;
}

/**
 * The API query for a set of filters, restricted to the module's allow-list.
 *
 * @param {string[]} allowed
 * @param {Record<string, string>} filters
 * @returns {URLSearchParams}
 */
export function buildFilterQuery(allowed, filters) {
  const query = new URLSearchParams();
  for (const key of allowed) {
    const value = filters[key];
    if (value === undefined || value === null || value === '') continue;
    query.set(key, value);
  }
  return query;
}
