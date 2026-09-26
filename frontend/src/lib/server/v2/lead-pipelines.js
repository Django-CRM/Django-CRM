/**
 * Lead pipelines: the wiring behind `/settings/lead-pipelines`.
 *
 * Server-only (the access token is read from an httpOnly cookie in here).
 * Reads `GET /leads/pipelines/` for the list, then `GET /leads/pipelines/<id>/`
 * for the one pipeline whose stages are on screen.
 *
 * READ IS OPEN, WRITE IS ADMIN-ONLY
 * Every member can read pipelines (the board needs them). Every write below is
 * refused with a 403 by `leads/views/kanban_views.py` unless the caller is an
 * org admin or a superuser. `can_edit` mirrors that so the page hides controls
 * a member could only be turned away from; it is decoded from the JWT and is a
 * display hint, never the check.
 */
import { apiRequest } from '$lib/api-helpers.js';
import { viewerRole } from './organization.js';

/**
 * The page: every active pipeline, and the stages of the selected one. An
 * absent or unknown `pipelineId` falls back to the first listed, so a stale
 * link shows a pipeline rather than an error.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string | null} [pipelineId]
 */
export async function getLeadPipelines({ cookies }, pipelineId = null) {
  const listed = await apiRequest('/leads/pipelines/', {}, { cookies });
  const pipelines = (listed.pipelines ?? []).map((/** @type {any} */ p) => ({
    id: p.id,
    name: p.name,
    stageCount: p.stage_count ?? 0,
    leadCount: p.lead_count ?? 0,
    isDefault: Boolean(p.is_default)
  }));
  const can_edit = viewerRole(cookies) === 'ADMIN';
  if (pipelines.length === 0) return { pipelines, pipeline: null, can_edit };

  const chosen = pipelines.find((p) => p.id === pipelineId) ?? pipelines[0];
  const detail = await apiRequest(`/leads/pipelines/${chosen.id}/`, {}, { cookies });
  const stages = [...(detail.stages ?? [])]
    .sort((a, b) => a.order - b.order)
    .map((/** @type {any} */ s) => ({
      id: s.id,
      name: s.name,
      stageType: s.stage_type,
      mapsToStatus: s.maps_to_status || '',
      winProbability: s.win_probability ?? 0,
      leadCount: s.lead_count ?? 0
    }));

  return {
    pipelines,
    pipeline: {
      id: detail.id,
      name: detail.name,
      description: detail.description ?? '',
      leadCount: detail.lead_count ?? 0,
      stages
    },
    can_edit
  };
}

/**
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {{ name: string }} values
 * @returns {Promise<any>} the created pipeline, stages seeded
 */
export async function createPipeline({ cookies }, { name }) {
  return await apiRequest(
    '/leads/pipelines/',
    { method: 'POST', body: { name, create_default_stages: true } },
    { cookies }
  );
}

/**
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {{ name: string }} values
 */
export async function renamePipeline({ cookies }, id, { name }) {
  return await apiRequest(
    `/leads/pipelines/${id}/`,
    { method: 'PUT', body: { name } },
    { cookies }
  );
}

/**
 * Soft delete. Refused with a 400 naming the count while any lead the board
 * shows is still in one of its stages.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function deletePipeline({ cookies }, id) {
  return await apiRequest(`/leads/pipelines/${id}/`, { method: 'DELETE' }, { cookies });
}

/**
 * The four stage fields this page edits, as the serializer wants them. An
 * empty `maps_to_status` goes as `null`, which clears it.
 *
 * @param {{ name: string, stageType: string, mapsToStatus: string, winProbability: string }} values
 */
function stageBody({ name, stageType, mapsToStatus, winProbability }) {
  return {
    name,
    stage_type: stageType,
    maps_to_status: mapsToStatus || null,
    win_probability: winProbability === '' ? 0 : Number(winProbability)
  };
}

/**
 * Appended as the last stage: the backend orders a stage sent without one
 * after the others.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} pipelineId
 * @param {{ name: string, stageType: string, mapsToStatus: string, winProbability: string }} values
 */
export async function createStage({ cookies }, pipelineId, values) {
  return await apiRequest(
    `/leads/pipelines/${pipelineId}/stages/`,
    { method: 'POST', body: stageBody(values) },
    { cookies }
  );
}

/**
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {{ name: string, stageType: string, mapsToStatus: string, winProbability: string }} values
 */
export async function updateStage({ cookies }, id, values) {
  return await apiRequest(
    `/leads/stages/${id}/`,
    { method: 'PUT', body: stageBody(values) },
    { cookies }
  );
}

/**
 * Refused with a 400 naming the count while the stage still holds leads.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function deleteStage({ cookies }, id) {
  return await apiRequest(`/leads/stages/${id}/`, { method: 'DELETE' }, { cookies });
}

/**
 * Move one stage a place up or down. The reorder endpoint takes every stage id
 * of the pipeline, so the current order is read fresh here rather than taken
 * from the form: a page left open while another admin added a stage would
 * otherwise send an incomplete list, which the endpoint refuses.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} pipelineId
 * @param {string} stageId
 * @param {'up' | 'down'} direction
 * @returns {Promise<boolean>} false when the stage is not in the pipeline or is already at that end
 */
export async function moveStage({ cookies }, pipelineId, stageId, direction) {
  const detail = await apiRequest(`/leads/pipelines/${pipelineId}/`, {}, { cookies });
  const ids = [...(detail.stages ?? [])]
    .sort((a, b) => a.order - b.order)
    .map((/** @type {any} */ s) => String(s.id));
  const from = ids.indexOf(stageId);
  const to = direction === 'up' ? from - 1 : from + 1;
  if (from === -1 || to < 0 || to >= ids.length) return false;
  [ids[from], ids[to]] = [ids[to], ids[from]];
  await apiRequest(
    `/leads/pipelines/${pipelineId}/stages/reorder/`,
    { method: 'POST', body: { stage_ids: ids } },
    { cookies }
  );
  return true;
}
