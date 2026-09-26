/**
 * The ticket board: tickets grouped by status, or by the stages of a case
 * pipeline.
 *
 * Server-only, for the same reason as `tickets.js`: the access token is read
 * from an httpOnly cookie in here.
 *
 * Read in two calls. `GET /cases/pipelines/` lists the org's active
 * pipelines for the mode picker, then `GET /cases/kanban/` returns the
 * columns with their cards: status columns by default, a pipeline's stages
 * when `pipeline_id` names one. The kanban endpoint applies the ticket read
 * rule, so a member's board holds only the tickets their list does.
 *
 * One write: `PATCH /cases/<id>/move/` with a `status` (status mode) or a
 * `stage_id` (pipeline mode). The backend checks write access, the WIP limit
 * and the close gate; nothing here decides any of that.
 */
import { apiRequest } from '$lib/api-helpers.js';

const GREY = '#6B7280';

/**
 * The status column the board never shows. The kanban endpoint hides merged
 * duplicates, so this column is always empty, and a card moved into it would
 * vanish from the board without ever being merged into anything.
 */
const DUPLICATE = 'Duplicate';

/**
 * A column colour is tenant text (a stage's colour) that ends up in a `style`
 * attribute, so only a plain hex colour gets through. Anything else is grey.
 *
 * @param {unknown} value
 */
function swatch(value) {
  return typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value) ? value : GREY;
}

/** @param {any} row */
function toCard(row) {
  const owner = row.assigned_to?.[0];
  const breached = Boolean(row.is_sla_breached);
  return {
    id: row.id,
    name: row.name || 'Untitled ticket',
    account: row.account_name ?? '',
    owner: owner?.user_details?.name || owner?.user_details?.email || '',
    priority: row.priority ?? '',
    slaBreached: breached,
    // Exclusive server-side, but breached wins if the two ever disagree.
    slaAtRisk: !breached && Boolean(row.is_sla_at_risk)
  };
}

/** @param {any} column */
function toLane(column) {
  const rows = (column.cases ?? []).map(toCard);
  const total = column.case_count ?? rows.length;
  return {
    id: String(column.id),
    name: column.name,
    color: swatch(column.color),
    rows,
    count: total,
    truncated: total > rows.length,
    wipLimit: column.wip_limit ?? null
  };
}

/**
 * The board. `pipelineId` (from `?pipeline=`) switches to pipeline mode, but
 * only when it names a pipeline the API has just listed; an absent or unknown
 * id is status mode. So a hand-edited URL cannot turn into a 400 or a 404
 * from the kanban call.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string | null} [pipelineId]
 */
export async function getTicketBoard({ cookies }, pipelineId = null) {
  const listed = await apiRequest('/cases/pipelines/', {}, { cookies });
  const pipelines = (listed.pipelines ?? []).map((/** @type {any} */ p) => ({
    id: p.id,
    name: p.name
  }));
  const active = pipelines.find((/** @type {any} */ p) => p.id === pipelineId) ?? null;

  const endpoint = active
    ? `/cases/kanban/?${new URLSearchParams({ pipeline_id: active.id })}`
    : '/cases/kanban/';
  const board = await apiRequest(endpoint, {}, { cookies });

  const lanes = [...(board.columns ?? [])]
    .filter((/** @type {any} */ c) => !(c.is_status_column && c.id === DUPLICATE))
    .sort((a, b) => a.order - b.order)
    .map(toLane);

  return {
    pipelines,
    pipeline: active,
    mode: active ? 'pipeline' : 'status',
    total: board.total_cases ?? 0,
    lanes
  };
}

/**
 * Move a ticket into a column, optionally between two cards of it. Status
 * mode sends the column as `status`, pipeline mode as `stage_id`.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {{ mode: string, laneId: string, aboveId?: string, belowId?: string }} target
 */
export async function moveTicket({ cookies }, id, { mode, laneId, aboveId, belowId }) {
  return await apiRequest(
    `/cases/${id}/move/`,
    {
      method: 'PATCH',
      body: {
        ...(mode === 'pipeline' ? { stage_id: laneId } : { status: laneId }),
        ...(aboveId ? { above_case_id: aboveId } : {}),
        ...(belowId ? { below_case_id: belowId } : {})
      }
    },
    { cookies }
  );
}
