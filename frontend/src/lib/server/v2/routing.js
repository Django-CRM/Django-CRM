/**
 * Ticket routing rules: the wiring behind `/settings/routing`.
 *
 * Server-only. Reads `GET /cases/routing-rules/`, ordered by priority (the
 * order IS the behaviour: the engine runs rules top-down and takes the first
 * match). Each rule carries its conditions, strategy, targets, and two things
 * the backend computes from the ROUTED activity log, `matched_last_30d` (how
 * often the rule fired) and the round-robin `state.last_assigned_index` (so the
 * page can name who is next). The response also carries org totals, including
 * `unrouted_last_30d` (cases no rule matched).
 *
 * Create, edit, turn off, turn on and delete are wired below. Drag-to-reorder
 * is still a deferred builder, so `priority_order` is a plain number field on
 * the form rather than something a drag sets. The backend returns
 * `target_assignees` as full profile objects (name under `user_details.name`,
 * plus `is_active`, which the page flags on) and `target_team` as a full team
 * object; the page wants `{ id, name, is_active }` / `{ id, name }`, so
 * flatten here.
 */
import { apiRequest } from '$lib/api-helpers.js';
import { viewerIsAdmin } from './organization.js';
import { CONDITION_FIELD_LABEL, CONDITION_OP_LABEL, ROUTING_STRATEGY_NAME } from '$lib/v2/enums.js';

/** Profile → the `{ id, name, is_active }` the rule card reads. */
function shapeAssignee(p) {
  return { id: p.id, name: p.user_details?.name ?? null, is_active: p.is_active };
}

/** Team → `{ id, name }`, or null. */
function shapeTeam(team) {
  if (!team) return null;
  return { id: team.id, name: team.name };
}

/**
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @returns {Promise<{ rules: any[], totals: any, can_edit: boolean }>}
 */
export async function getRoutingRules({ cookies }) {
  const { rules, totals } = await apiRequest('/cases/routing-rules/', {}, { cookies });
  return {
    rules: (rules ?? []).map((r) => ({
      id: r.id,
      name: r.name,
      priority_order: r.priority_order,
      is_active: r.is_active,
      conditions: r.conditions ?? [],
      strategy: r.strategy,
      stop_processing: r.stop_processing,
      target_team: shapeTeam(r.target_team),
      target_assignees: (r.target_assignees ?? []).map(shapeAssignee),
      state: r.state ?? null,
      matched_last_30d: r.matched_last_30d ?? 0
    })),
    totals: totals ?? { count: 0, active: 0, unrouted_last_30d: 0 },
    // A display hint: POST/PUT/DELETE on `/cases/routing-rules/` each start
    // with `_is_admin(request.profile)` server-side and 403 regardless of
    // what this says. This only decides whether the page offers the controls.
    can_edit: viewerIsAdmin(cookies)
  };
}

/** Everything a create may set. `org` is the JWT's, never the body's. */
const CREATE_FIELDS = [
  'name',
  'priority_order',
  'is_active',
  'conditions',
  'strategy',
  'stop_processing',
  'target_assignee_ids',
  'target_team_id'
];

/** Nothing is frozen after creation on this resource, so an edit may set the
 *  same fields a create may. `RoutingRuleDetailView.put` builds its serializer
 *  with `partial=True`, so an omitted key is left untouched rather than
 *  blanked, which is what lets the turn-on action below send a single key. */
const UPDATE_FIELDS = CREATE_FIELDS;

/** The fields and operators `RoutingRuleSerializer.validate_conditions`
 *  accepts, derived from the label maps the page renders its selects from.
 *  Derived rather than restated: a `.svelte` file cannot import from
 *  `$lib/server/`, so the page cannot share this module's list, and two
 *  hand-maintained copies of the same set drift. Keying off the labels
 *  means adding a field in one place adds it to both. */
export const CONDITION_FIELDS = Object.keys(CONDITION_FIELD_LABEL);
export const CONDITION_OPS = Object.keys(CONDITION_OP_LABEL);

/**
 * Read the repeating condition rows back off a submitted form.
 *
 * One indexed name per row (`condition_field_0`, `condition_op_0`,
 * `condition_value_0`, then `_1`, and so on), read by index. This lives here
 * rather than in the route's `+page.server.js` so the parse and the clean can
 * be tested together: the harness covers `$lib/server/` modules and cannot
 * import a route module at all.
 *
 * The indexing is the whole point, and it replaced three parallel `getAll`
 * arrays that were paired POSITIONALLY. A `<select>` whose value matches no
 * option has `selectedIndex === -1`, and such a select contributes NO entry to
 * `FormData`. With parallel arrays one missing `condition_field` therefore
 * shifted every later row's operator and value up by one, rewriting conditions
 * the admin never touched into ones that match nothing, or dropping the last
 * row entirely and widening the rule. The page also renders an unmatched
 * stored field as its own option so the select has something to match, but
 * these two fixes are not redundant: that one keeps a known value editable,
 * this one means no future unmatched value can corrupt its neighbours.
 *
 * Indices come from the submitted keys rather than from a hidden count field,
 * so a row that submits only two of its three inputs still holds its place,
 * and a missing count cannot silently collapse every condition into "matches
 * every ticket".
 *
 * @param {FormData} form
 * @returns {{ field: string, op: string, value: string }[]}
 */
export function readConditionRows(form) {
  /** @type {Map<number, { field: string, op: string, value: string }>} */
  const rows = new Map();
  for (const key of form.keys()) {
    const match = /^condition_(field|op|value)_(\d+)$/.exec(key);
    if (!match) continue;
    const index = Number(match[2]);
    let row = rows.get(index);
    if (!row) {
      row = { field: '', op: 'eq', value: '' };
      rows.set(index, row);
    }
    const part = /** @type {'field' | 'op' | 'value'} */ (match[1]);
    const raw = form.get(key);
    if (raw !== null) row[part] = raw.toString();
  }
  return [...rows.entries()].sort((a, b) => a[0] - b[0]).map(([, row]) => row);
}

/**
 * Clean the repeating condition rows into what the backend validates.
 *
 * A row the admin added and left blank is dropped, not rejected: an empty row
 * is how the editor represents "I am about to type here", and failing the save
 * for it would be hostile. A row with a field but no operator gets `eq`, the
 * backend's own default. `value` is always sent even when empty, including as
 * an empty array for `in`, because `validate_conditions` requires the KEY to
 * be present and only checks `"value" not in cond`.
 *
 * `in` gets an array; every other op keeps the plain string it always has.
 * `cases/routing.py`'s evaluator does
 * `if not isinstance(value, (list, tuple)): return False` for `in`, and does
 * it silently: the unknown-op and unknown-field branches next to it both log
 * a warning, this one does not. `validate_conditions` never checks the
 * value's type, only that the key exists, so a string here would save
 * cleanly (200) and then match nothing, forever, with no signal anywhere
 * pointing at why. A comma-separated string is split and trimmed; a value
 * that already arrives as an array (a condition this editor did not
 * originate) is kept as one, each entry trimmed the same way.
 */
function cleanConditions(rows) {
  return (rows ?? [])
    .map((r) => {
      const op = String(r?.op ?? 'eq').trim() || 'eq';
      const raw = r?.value;
      let value;
      if (op === 'in') {
        const parts = Array.isArray(raw)
          ? raw.map((v) => String(v).trim())
          : String(raw ?? '')
              .split(',')
              .map((v) => v.trim());
        value = parts.filter(Boolean);
      } else {
        value = raw === undefined || raw === null ? '' : String(raw);
      }
      return { field: String(r?.field ?? '').trim(), op, value };
    })
    .filter((r) => r.field)
    .map((r) => {
      if (!(CONDITION_FIELDS.includes(r.field) || r.field.startsWith('custom_fields.'))) {
        throw new Error(`"${r.field}" is not a field a rule can match on.`);
      }
      if (!CONDITION_OPS.includes(r.op)) {
        throw new Error(`"${r.op}" is not an operator a rule can use.`);
      }
      return r;
    });
}

/** @param {string[]} allowed @param {{ [key: string]: any }} values */
function buildBody(allowed, values) {
  /** @type {Record<string, any>} */
  const body = {};
  for (const field of allowed) {
    if (values[field] === undefined) continue;
    body[field] = values[field];
  }
  if (body.priority_order !== undefined) body.priority_order = Number(body.priority_order) || 0;
  for (const flag of ['is_active', 'stop_processing']) {
    if (body[flag] !== undefined) body[flag] = Boolean(body[flag]);
  }
  if (body.conditions !== undefined) body.conditions = cleanConditions(body.conditions);

  // `by_team` routes to a team and nothing else; the other three route to
  // people. Sending the half that does not apply is not rejected by the
  // backend, it is simply stored, and a later strategy switch would then
  // resurrect a stale target nobody chose. Send only the half in play.
  if (body.strategy === 'by_team') {
    delete body.target_assignee_ids;
  } else if (body.strategy !== undefined) {
    delete body.target_team_id;
  }

  // An empty string from an unselected `<select>` is not null. The field is
  // `allow_null=True`, so null clears it; `''` is a 400.
  if (body.target_team_id === '') body.target_team_id = null;
  if (body.target_assignee_ids !== undefined) {
    const ids = (body.target_assignee_ids ?? []).filter(Boolean);
    // An empty multi-select omits the key rather than sending `[]`, because
    // `[]` disarms the backend's own guard. `RoutingRuleSerializer.validate`
    // reads `assignees = attrs.get("target_assignees")` and only raises when
    // `assignees is None and self.instance is None`; `[]` is not `None`, so a
    // create with a condition and nobody selected returns 201. With
    // `stop_processing` on by default, `cases/routing.py` then hits
    // `if not pool: decision.reason = "empty_pool"` followed by
    // `if rule.stop_processing: return decision`, so every matching ticket is
    // left unassigned by a rule that also stops any lower rule from assigning
    // it. Omitting the key lets the serializer answer with its own message.
    //
    // Omitting it is only half the answer, and only on create. `put` is
    // `partial=True`, so on an edit the omission leaves the stored assignees
    // in place, and the backend has no update-path guard to speak up
    // (`self.instance is None` is false there). An admin who deselected
    // everyone would get a success, a closed panel, and the old list back,
    // with nothing saying the change was dropped. `updateRoutingRule` refuses
    // that request outright instead: see the guard below it.
    if (ids.length === 0) delete body.target_assignee_ids;
    else body.target_assignee_ids = ids;
  }
  return body;
}

/** @param {{ cookies: import('@sveltejs/kit').Cookies }} event */
export async function createRoutingRule({ cookies }, values) {
  const body = buildBody(CREATE_FIELDS, values);
  if (!body.name) throw new Error('A rule needs a name.');
  return await apiRequest('/cases/routing-rules/', { method: 'POST', body }, { cookies });
}

/** The strategies that route to people rather than to a team, which is every
 *  strategy except `by_team`. Derived from the same map the page labels its
 *  select from, rather than restated, for the reason `CONDITION_FIELDS` is
 *  derived: two hand-maintained copies of one set drift. It matches
 *  `RoutingRuleSerializer.validate`'s `("round_robin", "least_busy",
 *  "direct")`. All four strategies are exercised against the guard below, so
 *  a change to that map which no longer splits the same way fails there. */
const PEOPLE_STRATEGIES = Object.keys(ROUTING_STRATEGY_NAME).filter((s) => s !== 'by_team');

/** @param {{ cookies: import('@sveltejs/kit').Cookies }} event */
export async function updateRoutingRule({ cookies }, id, values) {
  if (!id) throw new Error('Which rule? No rule id was given.');
  const body = buildBody(UPDATE_FIELDS, values);

  // The one rule the backend enforces on create and cannot enforce on edit.
  // `RoutingRuleSerializer.validate` guards a people strategy with no
  // assignees behind `self.instance is None`, so on a PUT it never fires, and
  // `buildBody` above has just dropped the empty list so `partial=True` will
  // leave the stored assignees alone. Sending it would answer 200 and change
  // nothing the admin asked to change, which is a silent no-op on a control
  // they just operated. Refuse instead, and say which constraint refused.
  //
  // Both conditions are required. `body.strategy` alone is not enough: a
  // partial update that names a strategy without touching the assignee
  // picker (or the turn-on path, which sends `{ is_active: true }` and
  // nothing else) must still go through untouched. And an empty list under
  // `by_team` cannot reach here at all, because `buildBody` deletes the
  // assignee key outright for that strategy before the emptiness check.
  const submittedAssignees = values?.target_assignee_ids !== undefined;
  if (
    submittedAssignees &&
    body.target_assignee_ids === undefined &&
    PEOPLE_STRATEGIES.includes(body.strategy)
  ) {
    const name = ROUTING_STRATEGY_NAME[body.strategy].toLowerCase();
    throw new Error(`A ${name} rule needs at least one assignee.`);
  }

  return await apiRequest(`/cases/routing-rules/${id}/`, { method: 'PUT', body }, { cookies });
}

/**
 * Delete a rule, permanently.
 *
 * `RoutingRuleDetailView.delete` calls `obj.delete()`. Unlike
 * `/custom-fields/<id>/`, there is no soft-delete hiding behind this verb, so
 * the control that calls it has to say so.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function deleteRoutingRule({ cookies }, id) {
  if (!id) throw new Error('Which rule? No rule id was given.');
  return await apiRequest(`/cases/routing-rules/${id}/`, { method: 'DELETE' }, { cookies });
}
