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
 *
 * ACTIONS
 * A macro may also carry actions: `set_status`, `set_priority`,
 * `set_assignees` (replaces the ticket's) and `add_tags` (added to the
 * ticket's). The ticket composer shows them as removable chips and applies
 * the kept ones through `POST /macros/<id>/apply/` right after the reply
 * posts; the API runs them through the ticket PATCH's own write, gates and
 * all. The assignee and tag pickers offer active rows plus whatever the macro
 * already carries, so a deactivated assignee is shown rather than silently
 * dropped by a select with no matching option.
 */
import { apiRequest } from '$lib/api-helpers.js';
import { CASE_STATUSES } from '$lib/v2/enums.js';
import { viewerIsAdmin } from './organization.js';
import { myProfileId } from './leads.js';
import { getOrgPeopleAndTeams } from './org-people.js';

/** What a macro may set a ticket to: every status but Duplicate, which only a
 *  merge sets. The API refuses it too (`MACRO_STATUS_CHOICES`). */
export const MACRO_STATUSES = CASE_STATUSES.filter((s) => s !== 'Duplicate');

/** The action names `POST /macros/<id>/apply/` takes in `only`. */
export const MACRO_ACTION_KEYS = ['status', 'priority', 'assignees', 'tags'];

/** @param {{ name?: string, email?: string, is_active?: boolean }} p */
function personLabel(p) {
  const name = p.name || p.email || 'Unknown';
  return p.is_active === false ? `${name} (deactivated)` : name;
}

/** @param {{ name?: string, is_active?: boolean }} t */
function tagLabel(t) {
  const name = t.name || 'Unnamed';
  return t.is_active === false ? `${name} (archived)` : name;
}

/**
 * One chip per action a macro carries, in `MACRO_ACTION_KEYS` order.
 *
 * @param {any} m a macro row from the API
 * @returns {{ key: string, label: string }[]}
 */
export function macroActionChips(m) {
  /** @type {{ key: string, label: string }[]} */
  const chips = [];
  if (m?.set_status) chips.push({ key: 'status', label: `Status: ${m.set_status}` });
  if (m?.set_priority) chips.push({ key: 'priority', label: `Priority: ${m.set_priority}` });
  const people = m?.set_assignees_details ?? [];
  if (people.length) {
    chips.push({ key: 'assignees', label: `Assign: ${people.map(personLabel).join(', ')}` });
  }
  const tags = m?.add_tags_details ?? [];
  if (tags.length) chips.push({ key: 'tags', label: `Tag: ${tags.map(tagLabel).join(', ')}` });
  return chips;
}

/**
 * Picker options: the active rows, plus any the macro carries that are not
 * among them (deactivated, archived, or a list that failed to load), labelled
 * as such. Without the second half a select with no matching option submits
 * nothing, and saving the form would drop them.
 *
 * @param {{ id: string, name: string }[]} active
 * @param {any[]} stored the macro's `*_details`
 * @param {(row: any) => string} label
 */
export function optionsWithStored(active, stored, label) {
  const seen = new Set(active.map((o) => o.id));
  const extra = (stored ?? [])
    .filter((row) => !seen.has(row.id))
    .map((row) => ({ id: row.id, name: label(row) }));
  return [...active, ...extra];
}

/**
 * The action names the composer kept, from its `macro_action` inputs. Anything
 * else is dropped; the API refuses an unknown name anyway.
 *
 * @param {FormData} form
 */
export function keptActions(form) {
  const kept = form.getAll('macro_action').map(String);
  return MACRO_ACTION_KEYS.filter((key) => kept.includes(key));
}

/**
 * A sentence for what applying did, from the API's `applied` and `skipped`.
 *
 * @param {{ applied?: string[], skipped?: { action: string, reason: string }[] }} resp
 */
export function applySummary(resp) {
  const applied = resp?.applied ?? [];
  const skipped = resp?.skipped ?? [];
  const head = applied.length
    ? `Macro applied: ${applied.join(', ')}.`
    : 'The macro changed nothing on this ticket.';
  return [head, ...skipped.map((s) => s.reason)].join(' ');
}

/** Active tags in this org, for the macro tag picker. Empty on failure; the
 *  picker still offers the tags each macro already carries. */
async function listActiveTags(cookies) {
  try {
    const resp = await apiRequest('/tags/', {}, { cookies });
    return (resp.tags ?? []).map((/** @type {any} */ t) => ({ id: t.id, name: t.name }));
  } catch {
    return [];
  }
}

/**
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @returns {Promise<{ macros: any[], people: { id: string, name: string }[], tags: { id: string, name: string }[], statuses: string[], totals: any, placeholders: any[], can_create_org: boolean, my_profile_id: string }>}
 */
export async function getMacros({ cookies }) {
  const [resp, people, tags] = await Promise.all([
    apiRequest('/macros/', {}, { cookies }),
    getOrgPeopleAndTeams(cookies),
    listActiveTags(cookies)
  ]);
  const activePeople = people.people.map((p) => ({ id: p.id, name: p.name }));
  const results = (resp.results ?? []).map((m) => ({
    ...m,
    owner: m.owner ? { id: m.owner, name: m.owner_name || m.owner } : null,
    chips: macroActionChips(m),
    // The edit form's picker options for this row: active rows plus its own.
    assignee_options: optionsWithStored(activePeople, m.set_assignees_details, personLabel),
    tag_options: optionsWithStored(tags, m.add_tags_details, tagLabel)
  }));
  return {
    // Active members and active tags, the new-macro form's picker options.
    people: activePeople,
    tags,
    statuses: MACRO_STATUSES,
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
const WRITABLE_FIELDS = [
  'title',
  'body',
  'scope',
  'is_active',
  'set_status',
  'set_priority',
  'set_assignees',
  'add_tags'
];

/**
 * Shape the request body from the allow-list.
 *
 * The checks below are a fast fail for an obviously bad form, not the
 * authority: `MacroSerializer` requires a `title` and a body or an action,
 * checks every status, priority, assignee and tag, and
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
  const acts = Boolean(
    body.set_status || body.set_priority || body.set_assignees?.length || body.add_tags?.length
  );
  if (!body.title) throw new Error('A macro needs a title.');
  if (!body.body && !acts) throw new Error('A macro needs a body, an action, or both.');
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
 * `partial=False` and `MacroSerializer` requires a `title`, so a PUT missing
 * it 400s. `patch` is the partial verb and is what this form, which always
 * submits every field anyway, should be using regardless.
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
 * requires a title and a body or an action (a fast client-side fail for the
 * create/edit form, which always submits them), so
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

/**
 * The saved replies this person may insert: active ones only, org-wide plus
 * their own personal ones (the API's visibility rule). The composer needs the
 * title and id, whether there is any text to insert, and the chips for the
 * macro's actions.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function listUsableMacros({ cookies }) {
  const resp = await apiRequest('/macros/?active=true', {}, { cookies });
  return (resp.results ?? []).map((/** @type {any} */ m) => ({
    id: m.id,
    title: m.title ?? '',
    has_body: Boolean(String(m.body ?? '').trim()),
    chips: macroActionChips(m)
  }));
}

/**
 * Expand a saved reply against a ticket. The server substitutes the
 * placeholders (`macros/render.py`), refuses a ticket the caller may not
 * open, and counts the use. The text is only put in the composer; sending it
 * is the ordinary reply.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} macroId
 * @param {string} caseId
 * @returns {Promise<string>}
 */
export async function renderMacro({ cookies }, macroId, caseId) {
  const resp = await apiRequest(
    `/macros/${macroId}/render/`,
    { method: 'POST', body: { case_id: caseId } },
    { cookies }
  );
  return resp.rendered_body ?? '';
}

/**
 * Apply a macro's actions to a ticket. `only` lists the kept action names;
 * omitted, every action the macro carries applies. The API refuses a ticket
 * the caller may not change and runs the ticket PATCH's gates (the close
 * approval, the merged-ticket lock), answering a refusal with the PATCH's
 * own 400.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} macroId
 * @param {string} caseId
 * @param {string[] | undefined} only
 * @returns {Promise<{ applied: string[], skipped: { action: string, reason: string }[] }>}
 */
export async function applyMacro({ cookies }, macroId, caseId, only) {
  /** @type {Record<string, any>} */
  const body = { case_id: caseId };
  if (only) body.only = only;
  return await apiRequest(`/macros/${macroId}/apply/`, { method: 'POST', body }, { cookies });
}
