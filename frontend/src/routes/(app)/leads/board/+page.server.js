import { fail } from '@sveltejs/kit';
import { getLeadBoard, moveLead } from '$lib/server/v2/lead-board.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/**
 * Leads grouped by the stages of one lead pipeline. `?pipeline=<id>` picks
 * which; see `getLeadBoard` for how an unknown id is handled.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, url }) {
  return await getLeadBoard({ cookies }, url.searchParams.get('pipeline'));
}

export const actions = {
  /**
   * Move a lead into a stage, or back to "No stage" (`stage_id=unstaged`,
   * which `moveLead` sends as `null`). The board reorders optimistically and
   * calls this without waiting, so the job here is to persist and to hand back
   * a refusal the page can show while the card snaps back. The move endpoint
   * answers 404 for a lead the caller may not edit, so that stays a 404.
   */
  move: async ({ request, cookies }) => {
    const form = await request.formData();
    const id = String(form.get('id') || '');
    const stageId = String(form.get('stage_id') || '');
    const aboveId = String(form.get('above_id') || '');
    const belowId = String(form.get('below_id') || '');
    if (!id || !stageId) return fail(400, { error: 'Missing lead or stage.' });
    try {
      await moveLead({ cookies }, id, { stageId, aboveId, belowId });
      return { success: true };
    } catch (err) {
      if (/** @type {any} */ (err)?.status === 404) {
        return fail(404, { error: 'That lead is not one you can move.' });
      }
      return fail(400, { error: readableError(err, 'Could not move the lead.') });
    }
  }
};
