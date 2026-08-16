/**
 * Goals: the twelfth v2 module wired to the real API.
 *
 * Lives under `$lib/server` like the eleven before it: SvelteKit keeps this
 * directory out of the client bundle, so the httpOnly access token never
 * reaches the browser. Org is a JWT claim the backend re-derives; it is never a
 * parameter here.
 *
 * WHY THIS MODULE
 * `/goals` is a sidebar dead rail: every live page links to it and it still
 * read a fixture, its "New goal" button writing nowhere. Wiring it drove
 * `SalesGoalListView`, `SalesGoalLeaderboardView` and `SalesGoalDetailView`
 * (`/api/opportunities/goals/…`) for real. Reads *and* the admin-only
 * create/update/delete writes.
 *
 * THE FINDING THE WIRING EXPOSED
 * `SalesGoalCreateSerializer` exposed `assigned_to` (a Profile) and `team` with
 * DRF's default all-rows queryset and no org check. `common_profile` is not
 * RLS-protected, so before the backend fix an admin could POST/PUT a goal
 * assigned to a Profile in *another org*, leaking that person's name and email
 * into this org's goal detail and leaderboard. The fix scopes both to the
 * caller's org in the serializer (the trust boundary); this file only ever
 * offers in-org people and teams in its pickers, but that is a UX hint, not the
 * guard.
 *
 * WHAT THE MOCK ASSUMED THAT THE API SHAPES DIFFERENTLY
 * - The fixture pre-joined `assigned_to`/`team` to `{ id, name }`. The API sends
 *   the raw FK plus a nested `assigned_to_detail` / `team_detail`; `toGoal`
 *   flattens those back to the shape the page reads.
 * - The leaderboard fixture carried `user` as a bare string. The API nests
 *   `user: { id, name, email }`, so `toLeaderRow` collapses it to a name.
 * - There is **no** totals endpoint. The fixture's `goalTotals` is recomputed
 *   here from the fetched goals (sum over the active subset), arithmetic on
 *   fields already in the payload, never a second aggregate query.
 */
import { apiRequest } from '$lib/api-helpers.js';
import { getOrgPeopleAndTeams } from './org-people.js';

/** The three goal kinds the backend accepts (common/utils.py GOAL_TYPES). */
export const GOAL_TYPES = ['REVENUE', 'DEALS_CLOSED', 'ACTIVITIES'];

/** The four periods the backend accepts (common/utils.py PERIOD_TYPES). */
export const PERIOD_TYPES = ['MONTHLY', 'QUARTERLY', 'YEARLY', 'CUSTOM'];

/**
 * The deal types a weight can be set against (common/utils.py
 * OPPORTUNITY_TYPES). A type left out of a goal's map counts at full value, so
 * the form leaves every box blank by default and an untouched form stores `{}`.
 * ACTIVITIES goals have no deal type, and the backend refuses weights on one.
 */
export const DEAL_TYPES = ['NEW_BUSINESS', 'EXISTING_BUSINESS', 'RENEWAL', 'UPSELL', 'CROSS_SELL'];

/**
 * Collect the `weight_<TYPE>` inputs into the map the write serializer takes.
 *
 * A blank box is not a weight of zero, it is "do not re-weigh this type", so it
 * is left out of the map entirely. A box with something non-numeric in it is
 * passed along as typed rather than coerced: the backend validator names the
 * offending type in its 400, and silently turning "heavy" into NaN or 0 would
 * store a quota nobody asked for.
 *
 * @param {FormData} form
 * @returns {Record<string, number | string>}
 */
export function weightsFromForm(form) {
  /** @type {Record<string, number | string>} */
  const weights = {};
  for (const type of DEAL_TYPES) {
    const raw = form.get(`weight_${type}`)?.toString().trim();
    if (!raw) continue;
    const parsed = Number(raw);
    weights[type] = Number.isFinite(parsed) ? parsed : raw;
  }
  return weights;
}

/**
 * Plain scalar fields the write serializer accepts. `assigned_to`/`team` are
 * handled apart, through the single `target` picker, so the form can never send
 * both. `org` and `created_by` are set by the view from `request.profile` and
 * are not serializer fields. They cannot be assigned from the body.
 */
export const EDITABLE_FIELDS = [
  'name',
  'goal_type',
  'target_value',
  'period_type',
  'period_start',
  'period_end'
];

/**
 * The leaderboard is period-scoped server-side; MONTHLY is the API default and
 * the period most likely to hold current individual goals. Kept as one constant
 * so the choice is easy to see and change.
 */
const LEADERBOARD_PERIOD = 'MONTHLY';

/**
 * The signed-in role, decoded from the JWT `role` claim. A display hint only.
 * It decides whether the page shows an edit affordance. The backend re-derives
 * the role and is the thing that actually refuses a non-admin write, so this is
 * never trusted for authorization.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @returns {string | null}
 */
function viewerRole(cookies) {
  const token = cookies.get('jwt_access');
  if (!token) return null;
  try {
    const payload = token.split('.')[1];
    const json = Buffer.from(payload, 'base64url').toString('utf-8');
    return JSON.parse(json).role ?? null;
  } catch {
    return null;
  }
}

/** A person's display name from a nested ProfileSerializer row. */
function personName(detail) {
  const d = detail?.user_details ?? {};
  return d.name || d.email || 'Unnamed';
}

/**
 * One goal as the page reads it, from a `SalesGoalSerializer` row.
 *
 * `progress_value`, `progress_percent` and `status` are the server's, computed
 * over CLOSED_WON opportunities in the period. They are passed straight through
 * and never recomputed here; that recomputation is the aggregate bug the v2
 * redesign exists to kill.
 *
 * @param {any} g
 */
function toGoal(g) {
  return {
    id: g.id,
    name: g.name,
    goal_type: g.goal_type,
    target_value: Number(g.target_value ?? 0),
    period_type: g.period_type,
    period_start: g.period_start,
    period_end: g.period_end,
    assigned_to: g.assigned_to_detail
      ? { id: g.assigned_to_detail.id, name: personName(g.assigned_to_detail) }
      : null,
    team: g.team_detail ? { id: g.team_detail.id, name: g.team_detail.name } : null,
    is_active: g.is_active,
    // Always an object, never undefined, so the edit form and the list badge
    // can read it without guarding at every use.
    type_weights: g.type_weights ?? {},
    progress_value: Number(g.progress_value ?? 0),
    progress_percent: g.progress_percent ?? 0,
    status: g.status
  };
}

/**
 * A leaderboard row, with `user` collapsed from a nested object to a name.
 *
 * `user.name` used to be the person's email address, and the row carried that
 * same address again under `email`, so this list printed raw addresses. The
 * endpoint now sends `User.name` (falling back to the email itself when a user
 * has none) and no longer sends `email` at all, so there is nothing left to
 * fall back to here.
 */
function toLeaderRow(r) {
  return {
    rank: r.rank,
    goal_id: r.goal_id,
    user: r.user?.name || 'Unknown',
    target: Number(r.target ?? 0),
    achieved: Number(r.achieved ?? 0),
    percent: r.percent ?? 0
  };
}

/**
 * A Date as `YYYY-MM-DD`, so it compares against the API's date-only fields.
 * `toISOString()` would convert to UTC first and hand back yesterday for
 * anywhere east of Greenwich for part of every day.
 *
 * @param {Date} d
 */
function localDate(d) {
  const pad = (/** @type {number} */ n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Active first, then most-urgent status, then furthest along, the mock order. */
const STATUS_RANK = { behind: 0, at_risk: 1, on_track: 2, completed: 3 };
function byUrgency(a, b) {
  return (
    Number(b.is_active) - Number(a.is_active) ||
    (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9) ||
    b.progress_percent - a.progress_percent
  );
}

/**
 * Totals over the *active* goals only, mirroring the header ("Active goals
 * only"). Computed here because the API has no goals-summary endpoint. `behind`
 * counts active goals that are behind pace and still open. An ended goal is
 * settled, not something anyone can still influence.
 *
 * @param {ReturnType<typeof toGoal>[]} goals
 */
function computeTotals(goals) {
  // Compared as calendar dates, not instants. `period_end` is a date-only
  // field, so `new Date(period_end)` is midnight UTC, and measuring that
  // against `Date.now()` dropped a goal out of `behind` part-way through its
  // own final day. A goal ending today is still one somebody can act on, which
  // is the whole point of counting it.
  const today = localDate(new Date());
  const active = goals.filter((g) => g.is_active);
  const behindPace = (g) => g.status === 'behind' && g.period_end >= today;
  return {
    count: goals.length,
    active: active.length,
    target: active.reduce((sum, g) => sum + g.target_value, 0),
    achieved: active.reduce((sum, g) => sum + g.progress_value, 0),
    behind: active.filter(behindPace).length
  };
}

/**
 * The goals page: the list, the leaderboard and the derived totals in two round
 * trips. `limit=1000` fetches the whole (small) set so the totals are computed
 * over every goal, not one page of them.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies, url?: URL }} event
 */
export async function listGoals({ cookies, url }) {
  const filters = readFilters(url);
  const query = new URLSearchParams({ limit: '1000' });
  if (filters.period_type) query.set('period_type', filters.period_type);
  if (filters.q) query.set('search', filters.q);
  if (filters.window === 'current') query.set('current', 'true');
  if (filters.window === 'active') query.set('active', 'true');

  const [listResp, boardResp] = await Promise.all([
    apiRequest(`/opportunities/goals/?${query}`, {}, { cookies }),
    apiRequest(
      `/opportunities/goals/leaderboard/?period_type=${LEADERBOARD_PERIOD}`,
      {},
      { cookies }
    )
  ]);

  const goals = (listResp?.goals ?? []).map(toGoal).sort(byUrgency);
  const leaderboard = (boardResp?.leaderboard ?? []).map(toLeaderRow);

  return {
    goals,
    leaderboard,
    filters,
    totals: computeTotals(goals),
    can_edit: viewerRole(cookies) === 'ADMIN'
  };
}

/** The windows the filter bar offers, mapped to the API's own flags. */
const WINDOWS = ['all', 'current', 'active'];

/**
 * The filter state from the page URL, narrowed to values the API accepts.
 *
 * `period_type` and `window` are checked against their allowed sets rather than
 * forwarded: they are query-string values, so anything at all can arrive there,
 * and passing an unknown one straight to the API would turn a typo into an
 * empty list with no explanation. `q` is free text by nature and goes through
 * `URLSearchParams`, which encodes it.
 *
 * @param {URL} [url]
 */
function readFilters(url) {
  const params = url?.searchParams;
  const periodType = params?.get('period_type') ?? '';
  const window = params?.get('window') ?? '';
  return {
    period_type: PERIOD_TYPES.includes(periodType) ? periodType : '',
    q: (params?.get('q') ?? '').trim(),
    window: WINDOWS.includes(window) ? window : ''
  };
}

/**
 * The handful of goals running right now, for the Today page's progress strip.
 *
 * Deliberately not `/api/dashboard/`, which carries a `goal_summary` of the
 * same three goals: that endpoint also builds tasks, activities and pipeline
 * aggregates the Today page never reads, and this one is already narrowed to
 * what the caller may see by `_visible_to`.
 *
 * A failure here yields an empty strip rather than a broken dashboard. The
 * goals are context on a page whose actual job is the queue, and taking the
 * whole page down because a secondary panel failed is the wrong trade.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {number} [limit]
 */
export async function getCurrentGoals({ cookies }, limit = 3) {
  try {
    const resp = await apiRequest(
      `/opportunities/goals/?current=true&active=true&limit=${limit}`,
      {},
      { cookies }
    );
    return (resp?.goals ?? []).map(toGoal);
  } catch {
    return [];
  }
}

/**
 * Finished goal periods with what each one attained, newest first.
 *
 * The list page answers "how are we doing"; this answers "how did we do", which
 * nothing did before. Numbers come from the API already rolled up per period,
 * so nothing is re-aggregated here.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function getGoalHistory({ cookies }) {
  const resp = await apiRequest('/opportunities/goals/history/', {}, { cookies });
  return {
    history: (resp?.history ?? []).map((period) => ({
      period_start: period.period_start,
      period_end: period.period_end,
      period_type: period.period_type,
      goal_type: period.goal_type,
      goals_count: period.goals_count ?? 0,
      attained_count: period.attained_count ?? 0,
      target: Number(period.target ?? 0),
      achieved: Number(period.achieved ?? 0),
      percent: period.percent ?? 0,
      goals: (period.goals ?? []).map(toGoal)
    })),
    can_edit: viewerRole(cookies) === 'ADMIN'
  };
}

/**
 * What the create form needs. `can_edit` gates the whole route: creating a goal
 * is admin-only server-side, and a non-admin who reaches `/goals/new` gets
 * an "admins only" state rather than a form that would 403 on submit.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function getGoalFormOptions({ cookies }) {
  if (viewerRole(cookies) !== 'ADMIN') return { can_edit: false };
  const { people, teams } = await getOrgPeopleAndTeams(cookies);
  return { can_edit: true, people, teams };
}

/**
 * The goal plus the picker options, for the edit form. Admin-only, for the same
 * reason as create. `target` collapses the assigned_to/team pair into the single
 * value the form binds.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function getGoalForEdit({ cookies }, id) {
  if (viewerRole(cookies) !== 'ADMIN') return { can_edit: false };

  const [raw, { people, teams }] = await Promise.all([
    apiRequest(`/opportunities/goals/${id}/`, {}, { cookies }),
    getOrgPeopleAndTeams(cookies)
  ]);

  const target = raw.assigned_to
    ? `profile:${raw.assigned_to}`
    : raw.team
      ? `team:${raw.team}`
      : 'org';

  return {
    can_edit: true,
    people,
    teams,
    goal: {
      id: raw.id,
      name: raw.name,
      goal_type: raw.goal_type,
      target_value: raw.target_value,
      period_type: raw.period_type,
      period_start: raw.period_start,
      period_end: raw.period_end,
      is_active: raw.is_active,
      type_weights: raw.type_weights ?? {},
      target
    }
  };
}

/**
 * Turn the form's flat values into a write body. `target` is decoded into
 * exactly one of assigned_to / team (or neither for a whole-org goal), so the
 * two can never both be set from the UI.
 *
 * @param {Record<string, any>} values
 */
function writeBody(values) {
  /** @type {Record<string, any>} */
  const body = {};
  for (const field of EDITABLE_FIELDS) {
    if (field in values) body[field] = values[field];
  }
  if ('is_active' in values) body.is_active = values.is_active;
  // An object, so it is set apart from the scalar EDITABLE_FIELDS loop. An
  // empty map is a meaningful value (it clears the weighting), so it is sent
  // whenever the key is present rather than skipped for being falsy.
  if ('type_weights' in values) body.type_weights = values.type_weights;

  if ('target' in values) {
    const t = values.target ?? 'org';
    if (t.startsWith('profile:')) {
      body.assigned_to = t.slice('profile:'.length);
      body.team = null;
    } else if (t.startsWith('team:')) {
      body.team = t.slice('team:'.length);
      body.assigned_to = null;
    } else {
      body.assigned_to = null;
      body.team = null;
    }
  }
  return body;
}

/**
 * `POST /api/opportunities/goals/`. Admin-only server-side.
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {Record<string, any>} values
 */
export function createGoal({ cookies }, values) {
  return apiRequest(
    '/opportunities/goals/',
    { method: 'POST', body: writeBody(values) },
    { cookies }
  );
}

/**
 * `PUT /api/opportunities/goals/<id>/`. Admin-only; shares the write serializer
 * (and therefore the org check) with create.
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {Record<string, any>} values
 */
export function updateGoal({ cookies }, id, values) {
  return apiRequest(
    `/opportunities/goals/${id}/`,
    { method: 'PUT', body: writeBody(values) },
    { cookies }
  );
}

/**
 * `DELETE /api/opportunities/goals/<id>/`. Admin-only.
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export function deleteGoal({ cookies }, id) {
  return apiRequest(`/opportunities/goals/${id}/`, { method: 'DELETE' }, { cookies });
}
