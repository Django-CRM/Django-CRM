import { fail } from '@sveltejs/kit';
import { getTimesheet, stopTimer } from '$lib/server/v2/timesheet.js';
import { readableError } from '$lib/server/v2/form-errors.js';

/**
 * `?start=&end=` (YYYY-MM-DD) pick the week; absent, the API picks this week
 * in the org's timezone. The week-nav buttons drive those params.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, url }) {
  const start = url.searchParams.get('start') || undefined;
  const end = url.searchParams.get('end') || undefined;
  return await getTimesheet({ cookies }, { start, end });
}

/** @type {import('./$types').Actions} */
export const actions = {
  async stop(event) {
    const form = await event.request.formData();
    const entryId = form.get('entry_id')?.toString() ?? '';

    try {
      await stopTimer(event, entryId);
    } catch (/** @type {any} */ err) {
      return fail(400, { error: readableError(err, 'Could not stop the timer.') });
    }

    return { stopped: true };
  }
};
