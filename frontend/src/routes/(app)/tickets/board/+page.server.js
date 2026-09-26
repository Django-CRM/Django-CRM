import { fail } from '@sveltejs/kit';
import { getTicketBoard, moveTicket } from '$lib/server/v2/ticket-board.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/**
 * Tickets by status, or by the stages of one case pipeline when
 * `?pipeline=<id>` names one. See `getTicketBoard` for how an unknown id is
 * handled.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, url }) {
  return await getTicketBoard({ cookies }, url.searchParams.get('pipeline'));
}

export const actions = {
  /**
   * Move a ticket into a column. The board reorders optimistically and calls
   * this without waiting, so the job here is to persist and to hand back a
   * refusal the page can show while the card snaps back: a WIP limit, the
   * close gate on a move into Closed, or a ticket the caller may read but not
   * change (403, kept a 403).
   *
   * The close gate arrives as a per-field map on `status`, which
   * `readableError` renders as "status: ..."; the field name is dropped
   * because on a board the sentence already says what was refused.
   */
  move: async ({ request, cookies }) => {
    const form = await request.formData();
    const id = String(form.get('id') || '');
    const laneId = String(form.get('lane') || '');
    const mode = String(form.get('mode') || '');
    const aboveId = String(form.get('above_id') || '');
    const belowId = String(form.get('below_id') || '');
    if (!id || !laneId) return fail(400, { error: 'Missing ticket or column.' });
    try {
      await moveTicket({ cookies }, id, { mode, laneId, aboveId, belowId });
      return { success: true };
    } catch (err) {
      const message = readableError(err, 'Could not move the ticket.').replace(/^status: /, '');
      return fail(/** @type {any} */ (err)?.status === 403 ? 403 : 400, { error: message });
    }
  }
};
