/**
 * Macros: the wiring behind `/settings/macros`.
 *
 * Server-only. Reads one endpoint, `GET /macros/`, which returns the macros
 * visible to the requester (every org-scope row plus their own personal ones),
 * a `totals` block for the stat cards, and the server's `placeholders` set for
 * the reference card. Visibility is decided by the API from the JWT, a member
 * never sees another member's personal macros, so nothing here filters rows.
 *
 * WRITE PATHS, AND WHO MAY USE THEM
 * This page's permission model is unlike the rest of settings. Every
 * signed-in member may create, edit and delete their own `personal` macros;
 * only an admin may do any of that to an `org`-scope macro.
 * `_resolve_scope_and_owner` enforces the scope rule server-side and turns a
 * non-admin's org-scope attempt into 403, so `owner` is never sent from
 * here: it is derived from `request.profile` on the way in, and a client
 * that could name one could file a macro as somebody else. Editing someone
 * else's personal macro answers 404, not 403, on purpose
 * (`MacroDetailView._get_writable`), so the id space cannot be used to
 * discover which rows are other people's private macros; callers of
 * `updateMacro`/`deleteMacro` have to carry that distinction through to
 * their error copy rather than "fixing" it into a uniform 403.
 *
 * Delete is soft for an org macro (`is_active` flips to false, the row stays
 * and stays counted) and a hard delete for a personal one. Both go through
 * `deleteMacro`, one endpoint; which happens is decided by the row's own
 * scope server-side, never by anything this module sends.
 *
 * The one reshape is `owner`: the API returns it as a Profile id plus a
 * separate `owner_name` (the owner's email; `User` has no display name), while
 * the page wants a nested `{ id, name }` it can print. Org macros have no owner
 * (they are shared) and stay `null`. `unknown_placeholders` is computed by the
 * server per row and passed straight through. The page never recomputes which
 * tokens are broken, so its "broken placeholder" flag can't drift from the set
 * the renderer actually expands.
 */
import { apiRequest } from '$lib/api-helpers.js';
import { viewerIsAdmin } from './organization.js';
import { myProfileId } from './leads.js';

/**
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @returns {Promise<{ macros: any[], totals: any, placeholders: any[], can_create_org: boolean, my_profile_id: string }>}
 */
export async function getMacros({ cookies }) {
  const resp = await apiRequest('/macros/', {}, { cookies });
  const results = (resp.results ?? []).map((m) => ({
    ...m,
    owner: m.owner ? { id: m.owner, name: m.owner_name || m.owner } : null
  }));
  return {
    macros: results,
    totals: resp.totals ?? {
      count: 0,
      org: 0,
      personal: 0,
      inactive: 0,
      with_unknown_placeholders: 0
    },
    placeholders: resp.placeholders ?? [],
    // A display hint, not the authorization: `_resolve_scope_and_owner`
    // re-derives admin status from `request.profile` server-side, and that
    // is what actually decides whether an org-scope write succeeds. This
    // only decides whether the scope select offers "Everyone in the org".
    can_create_org: viewerIsAdmin(cookies),
    // So the page can tell its own personal macros apart from a stranger's.
    // The API already keeps a stranger's personal macros out of `results`
    // entirely, so this is only ever compared against rows the viewer could
    // already see.
    my_profile_id: await myProfileId(cookies)
  };
}

/** The fields `MacroSerializer` accepts on create or update. `owner` is
 *  derived server-side by `_resolve_scope_and_owner` from `request.profile`,
 *  never from the body, so it can never appear here. `org` is a JWT claim
 *  for the same reason. */
const WRITABLE_FIELDS = ['title', 'body', 'scope', 'is_active'];

/**
 * Shape the request body from the allow-list.
 *
 * The three checks below are a fast fail for an obviously bad form, not the
 * authority: `MacroSerializer` requires both `title` and `body`, and
 * `_resolve_scope_and_owner` rejects any scope outside these two, so the
 * server re-validates everything here regardless of what this lets through.
 *
 * @param {{ [key: string]: any }} values
 */
function buildBody(values) {
  /** @type {Record<string, any>} */
  const body = {};
  for (const field of WRITABLE_FIELDS) {
    if (values[field] === undefined) continue;
    body[field] = values[field];
  }

  body.title = String(body.title ?? '').trim();
  body.body = String(body.body ?? '').trim();
  if (!body.title) throw new Error('A macro needs a title.');
  if (!body.body) throw new Error('A macro needs a body.');
  if (body.scope !== 'org' && body.scope !== 'personal') {
    throw new Error("scope must be 'org' or 'personal'.");
  }
  if (body.is_active !== undefined) body.is_active = Boolean(body.is_active);

  return body;
}

/** @param {{ cookies: import('@sveltejs/kit').Cookies }} event */
export async function createMacro({ cookies }, values) {
  const body = buildBody(values);
  return await apiRequest('/macros/', { method: 'POST', body }, { cookies });
}

/**
 * PATCH, not PUT: `MacroDetailView.put` runs the serializer with
 * `partial=False` and `MacroSerializer` requires both `title` and `body`, so
 * a PUT missing either 400s. `patch` is the partial verb and is what this
 * form, which always submits both fields anyway, should be using regardless.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function updateMacro({ cookies }, id, values) {
  if (!id) throw new Error('Which macro? No macro id was given.');
  const body = buildBody(values);
  return await apiRequest(`/macros/${id}/`, { method: 'PATCH', body }, { cookies });
}

/**
 * Delete is soft for an org macro (`MacroDetailView.delete` flips
 * `is_active` and leaves the row in place) and a hard delete for a personal
 * one. Both happen behind this one call; which one is decided by the row's
 * own scope server-side, not by anything sent here.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function deleteMacro({ cookies }, id) {
  if (!id) throw new Error('Which macro? No macro id was given.');
  return await apiRequest(`/macros/${id}/`, { method: 'DELETE' }, { cookies });
}

/**
 * Turn a macro back on.
 *
 * A dedicated action, not a reuse of `updateMacro`: `buildBody` above
 * unconditionally requires both `title` and `body` to be non-empty (a fast
 * client-side fail for the create/edit form, which always submits both), so
 * calling `updateMacro(event, id, { is_active: true })` would throw before a
 * request was even made. This bypasses `buildBody` and sends exactly
 * `{ is_active: true }`. PATCH, partial, so the rest of the row is untouched.
 * `_get_writable` still enforces the same admin-only-for-org /
 * owner-only-for-personal rule as any other write to this row.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function activateMacro({ cookies }, id) {
  if (!id) throw new Error('Which macro? No macro id was given.');
  return await apiRequest(
    `/macros/${id}/`,
    { method: 'PATCH', body: { is_active: true } },
    { cookies }
  );
}
