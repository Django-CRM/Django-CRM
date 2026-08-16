import { getToday } from '$lib/server/v2/today.js';
import { getCurrentGoals } from '$lib/server/v2/goals.js';

/**
 * Today, plus the goals running today.
 *
 * Two requests in parallel rather than one: `/dashboard/today/` builds the
 * queue and `/opportunities/goals/` the strip, and neither waits on the other.
 * `getCurrentGoals` swallows its own failure, so a goals outage costs the strip
 * and not the page.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load(event) {
  const [today, goals] = await Promise.all([getToday(event), getCurrentGoals(event)]);
  return { ...today, goals };
}
