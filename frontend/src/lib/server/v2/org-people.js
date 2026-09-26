/**
 * The in-org people and teams every picker needs.
 *
 * Server-only. `GET /users/get-teams-and-users/` filters profiles to
 * `is_active=True, org=request.profile.org` and teams to the same org, so a
 * picker built from this can only ever offer an active, in-org target.
 *
 * That is UX, not the boundary. Every consumer's own write path re-checks the
 * id against the caller's org, and they do it in three different ways, so do
 * not read one mechanism into all of them: the four `cases` settings
 * serializers scope their relational querysets to the caller's org in
 * `__init__` and reject a foreign id with a 400; `SalesGoalCreateSerializer`
 * raises a 400 from `validate_assigned_to` / `validate_team`; the document
 * views filter `Profile.objects.filter(id__in=..., org=..., is_active=True)`
 * inline and silently drop a foreign id rather than failing. All three close
 * the hole. None of them depend on what this list offered.
 *
 * This exists because four modules had grown their own private copy of the
 * same fetch. `documents.js` and `goals.js` were byte-identical in behaviour
 * and now import this. `deals.js` and `leads.js` keep theirs: they label a
 * person by email rather than name, and changing what their owner select
 * displays is not a refactor.
 *
 * A failed fetch resolves to empty lists rather than throwing. The pickers
 * are one part of a settings page, and taking the whole page down to a 500
 * because an optional list did not load is worse than rendering it with
 * nothing to choose.
 *
 * `options_failed` says which of the two it was. Empty lists after a failure
 * look exactly like an org with nobody in it, and an edit form that cannot
 * tell them apart submits "nobody" over whatever the record already holds.
 * An edit form must keep the stored selection when this is true.
 */
import { apiRequest } from '$lib/api-helpers.js';

/** A profile as the pickers label it. */
function personName(profile) {
  const d = profile?.user_details ?? {};
  return d.name || d.email || 'Unnamed';
}

/**
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @returns {Promise<{ people: { id: string, name: string, email: string }[], teams: { id: string, name: string }[], options_failed: boolean }>}
 */
export async function getOrgPeopleAndTeams(cookies) {
  try {
    const resp = await apiRequest('/users/get-teams-and-users/', {}, { cookies });
    return {
      people: (resp?.profiles ?? []).map((/** @type {any} */ p) => ({
        id: p.id,
        name: personName(p),
        email: p.user_details?.email ?? ''
      })),
      teams: (resp?.teams ?? []).map((/** @type {any} */ t) => ({ id: t.id, name: t.name })),
      options_failed: false
    };
  } catch {
    return { people: [], teams: [], options_failed: true };
  }
}

/**
 * The viewer's own PROFILE id, for the "Mine" presets.
 *
 * Matched by email because no id is available any other way: the JWT carries
 * `user_email` but no profile id, and `locals.user.id` is the User id, not the
 * Profile id that `assigned_to` filters on. Those are different tables and
 * mixing them has silently broken checks in this codebase before.
 *
 * Returns null when nothing matches, and callers must then hide the preset
 * rather than render a link that filters to nobody.
 *
 * @param {{id: string, email?: string}[]} people
 * @param {string | undefined} viewerEmail
 * @returns {string | null}
 */
export function resolveMe(people, viewerEmail) {
  if (!viewerEmail) return null;
  const wanted = viewerEmail.toLowerCase();
  return people.find((p) => (p.email ?? '').toLowerCase() === wanted)?.id ?? null;
}
