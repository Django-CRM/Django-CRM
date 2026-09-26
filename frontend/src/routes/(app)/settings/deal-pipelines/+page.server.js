import { fail, redirect } from '@sveltejs/kit';
import {
  getDealPipelines,
  createPipeline,
  renamePipeline,
  deletePipeline,
  createStage,
  updateStage,
  deleteStage,
  moveStage
} from '$lib/server/v2/deal-pipelines.js';
import { readableError } from '$lib/server/v2/form-errors.js';

const ADMIN_ONLY = 'Only an admin can change deal pipelines.';
const KINDS = ['open', 'won', 'lost'];
const DAYS = /^\d{1,4}$/;

/**
 * `?pipeline=<id>` picks whose stages are shown; see `getDealPipelines`.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, url }) {
  return getDealPipelines({ cookies }, url.searchParams.get('pipeline'));
}

/** @param {FormData} form */
function readStage(form) {
  return {
    label: form.get('label')?.toString().trim() ?? '',
    kind: form.get('kind')?.toString() ?? 'open',
    expectedDays: form.get('expected_days')?.toString().trim() ?? '',
    warningDays: form.get('warning_days')?.toString().trim() ?? ''
  };
}

/**
 * The form's own checks, a UX hint: the serializer applies the same rules and
 * is what decides.
 *
 * @param {ReturnType<typeof readStage>} values
 * @returns {string | null}
 */
function stageProblem(values) {
  if (!values.label) return 'Give the stage a name.';
  if (!KINDS.includes(values.kind)) return 'Pick open, won or lost.';
  for (const days of [values.expectedDays, values.warningDays]) {
    if (days !== '' && (!DAYS.test(days) || Number(days) < 1 || Number(days) > 3650)) {
      return 'Days are a whole number from 1 to 3650, or blank.';
    }
  }
  return null;
}

/**
 * Run one write and turn its refusal into a sentence under `key`. Every write
 * here is admin-only in `opportunity/views/pipeline_views.py`; `can_edit` only
 * hides the controls, so a 403 is still answered with a sentence rather than
 * the raw backend string.
 *
 * @param {string} key
 * @param {string} fallback
 * @param {() => Promise<any>} write
 */
async function attempt(key, fallback, write) {
  try {
    await write();
  } catch (/** @type {any} */ err) {
    if (err?.status === 403) return fail(403, { [key]: { error: ADMIN_ONLY } });
    return fail(400, { [key]: { error: readableError(err, fallback) } });
  }
  return null;
}

/** @type {import('./$types').Actions} */
export const actions = {
  async createPipeline(event) {
    const form = await event.request.formData();
    const name = form.get('name')?.toString().trim() ?? '';
    if (!name) return fail(400, { createPipeline: { error: 'Give the pipeline a name.' } });
    /** @type {any} */
    let created = null;
    const refused = await attempt('createPipeline', 'Could not create the pipeline.', async () => {
      created = await createPipeline(event, { name });
    });
    if (refused) return refused;
    // Straight to the new pipeline, so its seeded stages are what is on screen.
    redirect(303, `/settings/deal-pipelines?pipeline=${encodeURIComponent(created.id)}`);
  },

  async renamePipeline(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    const name = form.get('name')?.toString().trim() ?? '';
    if (!id || !name) return fail(400, { renamePipeline: { error: 'Give the pipeline a name.' } });
    return (
      (await attempt('renamePipeline', 'Could not rename the pipeline.', () =>
        renamePipeline(event, id, { name })
      )) ?? { renamed: true }
    );
  },

  async deletePipeline(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    if (!id)
      return fail(400, { deletePipeline: { error: 'That pipeline could not be identified.' } });
    const refused = await attempt('deletePipeline', 'Could not delete the pipeline.', () =>
      deletePipeline(event, id)
    );
    if (refused) return refused;
    // Its `?pipeline=` id is gone now, so land on the default without it.
    redirect(303, '/settings/deal-pipelines');
  },

  async createStage(event) {
    const form = await event.request.formData();
    const pipelineId = form.get('pipeline_id')?.toString() ?? '';
    const values = readStage(form);
    const problem = pipelineId ? stageProblem(values) : 'That pipeline could not be identified.';
    if (problem) return fail(400, { stage: { error: problem } });
    return (
      (await attempt('stage', 'Could not add the stage.', () =>
        createStage(event, pipelineId, values)
      )) ?? { saved: true }
    );
  },

  async updateStage(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    const values = readStage(form);
    const problem = id ? stageProblem(values) : 'That stage could not be identified.';
    if (problem) return fail(400, { stage: { error: problem } });
    return (
      (await attempt('stage', 'Could not save the stage.', () =>
        updateStage(event, id, values)
      )) ?? {
        saved: true
      }
    );
  },

  async deleteStage(event) {
    const form = await event.request.formData();
    const id = form.get('id')?.toString() ?? '';
    if (!id) return fail(400, { stageList: { error: 'That stage could not be identified.' } });
    return (
      (await attempt('stageList', 'Could not delete the stage.', () => deleteStage(event, id))) ?? {
        deleted: true
      }
    );
  },

  async moveStage(event) {
    const form = await event.request.formData();
    const pipelineId = form.get('pipeline_id')?.toString() ?? '';
    const id = form.get('id')?.toString() ?? '';
    const direction = form.get('direction')?.toString();
    if (!pipelineId || !id || (direction !== 'up' && direction !== 'down')) {
      return fail(400, { stageList: { error: 'That stage could not be moved.' } });
    }
    return (
      (await attempt('stageList', 'Could not reorder the stages.', () =>
        moveStage(event, pipelineId, id, direction)
      )) ?? { moved: true }
    );
  }
};
