import { getGoalHistory } from '$lib/server/v2/goals.js';

/**
 * Attainment across finished periods.
 *
 * Open to any member; the endpoint narrows a non-admin to their own goals and
 * their teams' with the same predicate the list and the leaderboard use, so
 * there is nothing to gate here.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load(event) {
  return await getGoalHistory(event);
}
