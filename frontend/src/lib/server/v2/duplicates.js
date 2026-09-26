/**
 * Possible duplicates and merging, for leads, contacts and accounts (G19).
 *
 * Three API calls, the same for each module:
 *
 *   POST /api/<module>/duplicates/             while a create form is typed
 *   GET  /api/<module>/<id>/duplicates/        the detail page's panel
 *   POST /api/<module>/<keeper>/merge/         {"merge_id": "<loser>"}
 *
 * The API decides everything that matters: it searches only the records the
 * caller may open, so nothing here can show or count a hidden one, and it
 * refuses a merge the caller may not make whatever this layer or the page
 * believed. `can_delete` on each hit is a hint for the page, not a gate.
 */
import { error, fail, redirect } from '@sveltejs/kit';
import { apiRequest } from '$lib/api-helpers.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/**
 * The modules that have duplicates, the query fields each one's create form
 * may send, and what the compare page shows side by side.
 *
 * @type {Record<string, { singular: string, record: string, query: string[], compare: Array<[string, (o: any) => any]> }>}
 */
export const DUPLICATE_MODULES = {
  leads: {
    singular: 'lead',
    record: 'lead_obj',
    query: ['email', 'phone', 'first_name', 'last_name', 'company_name'],
    compare: [
      ['Name', (o) => personName(o)],
      ['Company', (o) => o.company_name],
      ['Email', (o) => o.email],
      ['Phone', (o) => o.phone],
      ['Website', (o) => o.website],
      ['Status', (o) => o.status],
      ['Created', (o) => o.created_at?.slice(0, 10)]
    ]
  },
  contacts: {
    singular: 'contact',
    record: 'contact_obj',
    query: ['email', 'phone', 'first_name', 'last_name'],
    compare: [
      ['Name', (o) => personName(o)],
      ['Job title', (o) => o.title],
      ['Company', (o) => o.organization],
      ['Email', (o) => o.email],
      ['Phone', (o) => o.phone],
      ['City', (o) => o.city],
      ['Created', (o) => o.created_at?.slice(0, 10)]
    ]
  },
  accounts: {
    singular: 'account',
    record: 'account_obj',
    query: ['name', 'email', 'phone', 'website'],
    compare: [
      ['Name', (o) => o.name],
      ['Website', (o) => o.website],
      ['Email', (o) => o.email],
      ['Phone', (o) => o.phone],
      ['Industry', (o) => o.industry],
      ['City', (o) => o.city],
      ['Created', (o) => o.created_at?.slice(0, 10)]
    ]
  }
};

/** @param {any} o */
function personName(o) {
  return [o.first_name, o.last_name].filter(Boolean).join(' ');
}

/** @param {string} module */
function spec(module) {
  // `Object.hasOwn`, so `__proto__` or `constructor` is not a module.
  if (!Object.hasOwn(DUPLICATE_MODULES, module)) error(404, 'Not found');
  return DUPLICATE_MODULES[module];
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * An id about to become part of an API path, or a 404.
 *
 * `?with=` and the merge form's fields come from the browser. Built into a
 * path unchecked, `../..` would walk this server's authenticated request to
 * any API route the caller's token reaches, so only a UUID gets through.
 *
 * @param {unknown} value
 * @returns {string}
 */
function recordId(value) {
  if (typeof value !== 'string' || !UUID.test(value)) error(404, 'Not found');
  return value;
}

/**
 * "email and phone", for "shares their email and phone". The API names each
 * rule in plain words already (`email`, `phone`, `name`, `company`,
 * `website`), so this only joins them.
 * @param {string[]} reasons
 */
export function matchedLabel(reasons) {
  const words = reasons ?? [];
  if (words.length <= 1) return words.join('');
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`;
}

/** @param {any} hit */
function toHit(hit) {
  return {
    id: hit.id,
    name: hit.name,
    email: hit.email ?? '',
    phone: hit.phone ?? '',
    matched_on: matchedLabel(hit.matched_on),
    can_delete: Boolean(hit.can_delete)
  };
}

/**
 * Possible duplicates of a record being typed into a create form. Only the
 * module's own fields are forwarded, and blank ones are dropped. A POST body,
 * never a query string: an email and a phone in a URL are written to every
 * access log on the way.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} module
 * @param {Record<string, unknown>} criteria
 */
export async function checkDuplicates({ cookies }, module, criteria) {
  /** @type {Record<string, string>} */
  const body = {};
  for (const field of spec(module).query) {
    const value = criteria?.[field];
    if (typeof value === 'string' && value.trim()) body[field] = value.trim();
  }
  if (!Object.keys(body).length) return [];
  const response = await apiRequest(
    `/${module}/duplicates/`,
    { method: 'POST', body },
    { cookies }
  );
  return (response?.duplicates ?? []).map(toHit);
}

/**
 * Possible duplicates of a saved record, for its detail page. A failed check
 * is not a reason to fail the page, so it answers "none" instead.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} module
 * @param {string} id
 */
export async function recordDuplicates({ cookies }, module, id) {
  spec(module);
  recordId(id);
  try {
    const response = await apiRequest(`/${module}/${id}/duplicates/`, {}, { cookies });
    return {
      can_delete: Boolean(response?.can_delete),
      duplicates: (response?.duplicates ?? []).map(toHit)
    };
  } catch {
    return { can_delete: false, duplicates: [] };
  }
}

/**
 * The two records of a merge, side by side. Both come from their detail
 * endpoint, so either one the caller may not open is the same 404 as a
 * missing id.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} module
 * @param {string} id
 * @param {string | null} otherId
 */
export async function getMergePair({ cookies }, module, id, otherId) {
  const { singular, record, compare } = spec(module);
  recordId(id);
  if (!otherId || otherId === id) error(404, `Pick the ${singular} to compare this one with.`);
  recordId(otherId);

  const load = async (/** @type {string} */ pk) => {
    try {
      const [detail, dups] = await Promise.all([
        apiRequest(`/${module}/${pk}/`, {}, { cookies }),
        // Only for `can_delete`. If it fails the page still loads, and that
        // record is simply not offered as the one to merge away.
        apiRequest(`/${module}/${pk}/duplicates/`, {}, { cookies }).catch(() => null)
      ]);
      const obj = detail[record];
      return {
        id: obj.id,
        name: record === 'account_obj' ? obj.name : personName(obj) || obj.email || singular,
        can_delete: Boolean(dups?.can_delete),
        fields: compare.map(([label, read]) => ({ label, value: read(obj) || '' }))
      };
    } catch (/** @type {any} */ err) {
      if (err?.status === 404) {
        error(404, `That ${singular} does not exist, or you do not have access to it.`);
      }
      throw err;
    }
  };

  const [current, other] = await Promise.all([load(id), load(otherId)]);
  return { module, singular, current, other };
}

/**
 * Merge `loserId` into `keeperId`. The API checks both records and the
 * caller's rights on each; a refusal arrives as a thrown error with its status.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} module
 * @param {string} keeperId
 * @param {string} loserId
 */
export async function mergeRecords({ cookies }, module, keeperId, loserId) {
  spec(module);
  recordId(keeperId);
  recordId(loserId);
  return await apiRequest(
    `/${module}/${keeperId}/merge/`,
    { method: 'POST', body: { merge_id: loserId } },
    { cookies }
  );
}

/**
 * `load` and the `merge` action for `/<module>/<id>/merge?with=<other>`, the
 * side-by-side page where the person picks which record to keep. The three
 * routes are one line each on top of this.
 *
 * The two ids arrive in the form rather than the query string, because a
 * form action posts to `?/merge` and drops `?with=`. Whichever the form says,
 * the keeper must be one of the two records on the page and the loser is the
 * other one; the API re-checks both.
 *
 * @param {string} module
 */
export function mergeRoute(module) {
  return {
    /** @param {{ cookies: import('@sveltejs/kit').Cookies, params: Record<string, string>, url: URL }} event */
    load: async ({ cookies, params, url }) =>
      getMergePair({ cookies }, module, params.id, url.searchParams.get('with')),

    actions: {
      /** @param {{ cookies: import('@sveltejs/kit').Cookies, params: Record<string, string>, request: Request }} event */
      merge: async ({ cookies, params, request }) => {
        const form = await request.formData();
        const other = form.get('other')?.toString() ?? '';
        const keep = form.get('keep')?.toString() ?? '';
        const pair = [params.id, other];
        if (!UUID.test(params.id) || !UUID.test(other) || other === params.id) {
          return fail(404, { error: 'Not found' });
        }
        if (!pair.includes(keep)) {
          return fail(400, { error: 'Choose which record to keep.' });
        }
        if (form.get('confirm') !== 'on') {
          return fail(400, { error: 'Tick the box to confirm the other record will be deleted.' });
        }
        const loser = keep === params.id ? other : params.id;
        try {
          await mergeRecords({ cookies }, module, keep, loser);
        } catch (/** @type {any} */ err) {
          return fail(err?.status === 404 || err?.status === 403 ? err.status : 400, {
            error: readableError(err, 'Could not merge these records.')
          });
        }
        redirect(303, `/${module}/${keep}`);
      }
    }
  };
}
