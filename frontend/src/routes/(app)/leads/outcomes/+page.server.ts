import { getOutcomes, responseDays, RESPONSE_DAYS } from '#lib/server/v2/leads.js';
import { viewerIsAdmin } from '#lib/server/v2/organization.js';
import type { PageServerLoad } from './$types';

/**
 * Won, lost and time to win per person. The API narrows the rows: an admin
 * gets every active member and a totals row, anyone else only themselves.
 * `isAdmin` only words the page.
 */
export const load: PageServerLoad = async ({ cookies, url }) => {
  const days = responseDays(url.searchParams.get('days'));
  const { since, measured_since, rows, totals } = await getOutcomes({ cookies }, days);
  return {
    days,
    periods: RESPONSE_DAYS,
    since,
    measured_since,
    rows,
    totals,
    isAdmin: viewerIsAdmin(cookies)
  };
};
