import { fail } from '@sveltejs/kit';
import { getLeadBoard, moveLead, UNSTAGED } from '$lib/server/v2/lead-board.js';
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
   * Move a lead into a stage. The board reorders optimistically and calls this
   * without waiting, so the job here is to persist and to hand back a refusal
   * the page can show while the card snaps back. A 403 stays a 403: the move
   * endpoint refuses a lead the caller neither created nor is assigned to.
   *
   * "No stage" is not a destination. The move endpoint takes a lead out of a
   * pipeline only alongside a status change, which this board does not offer,
   * so it is refused here with a sentence rather than sent to come back as a
   * validation error.
   */
  move: async ({ request, cookies }) => {
    const form = await request.formData();
    const id = String(form.get('id') || '');
    const stageId = String(form.get('stage_id') || '');
    const aboveId = String(form.get('above_id') || '');
    const belowId = String(form.get('below_id') || '');
    if (!id || !stageId) return fail(400, { error: 'Missing lead or stage.' });
    if (stageId === UNSTAGED) {
      return fail(400, { error: 'A lead in a pipeline stays in one of its stages.' });
    }
    try {
      await moveLead({ cookies }, id, { stageId, aboveId, belowId });
      return { success: true };
    } catch (err) {
      const status = /** @type {any} */ (err)?.status === 403 ? 403 : 400;
      return fail(status, { error: readableError(err, 'Could not move the lead.') });
    }
  }
};
