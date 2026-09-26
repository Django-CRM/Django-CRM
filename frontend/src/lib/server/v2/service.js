/**
 * Service analytics: the wiring behind /v2/tickets/analytics.
 *
 * Server-only. One admin-only backend call, `/cases/analytics/service/`,
 * returns the "service health" page shape; this layer re-keys the top level
 * from the backend's snake_case (`first_response`, `by_type`, `by_agent`) to
 * the camelCase the page reads (`firstResponse`, `byType`, `byAgent`). The
 * inner field names are already what the page reads.
 *
 * Once that call has answered (so the caller is an admin), two more run in
 * parallel: `/cases/csat/aggregate/` (`csat`) and `/cases/analytics/nrt/`
 * (`nextResponse`, its `by_priority` rows). Both take `from` = the service
 * window's first day, which the backend reads as the org's midnight, and no
 * `to` (now), so every figure on the page covers the same days. They run
 * after, not beside, the service call because that call is what tells us the
 * window's first day, and what keeps a non-admin from firing them at all.
 *
 * WHY THIS IS ADMIN-ONLY
 * These are org-wide support KPIs: opened/closed volume, first-response
 * attainment across every priority, the case-type mix, and a leaderboard of
 * who is carrying the queue. The per-metric analytics endpoints narrow a
 * non-admin to their own cases; showing a rep those same figures under
 * org-wide headings would misrepresent them. The backend 403s a non-admin and
 * this layer surfaces `can_view` so the page can say so plainly rather than
 * render a dashboard built from a personal slice.
 */
import { apiRequest } from '$lib/api-helpers.js';

/** A fully-shaped but empty payload, so a non-admin render never throws. */
const EMPTY = {
  totals: {
    opened: 0,
    closed: 0,
    open_now: 0,
    median_resolution_hours: 0,
    window_days: 14,
    business_hours_applied: false,
    calendar_name: null
  },
  volume: [],
  firstResponse: [],
  byType: [],
  byAgent: [],
  csat: { average: null, count: 0, distribution: { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 } },
  nextResponse: []
};

/**
 * The service-analytics dashboard, shaped for the page. Admin-only: a non-admin
 * gets `{ can_view: false }` and the empty-but-valid shape above.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string | number} [days] optional window length in days (backend clamps 1-90)
 */
export async function getServiceAnalytics({ cookies }, days) {
  const query = days ? `?days=${encodeURIComponent(days)}` : '';
  let data;
  try {
    data = await apiRequest(`/cases/analytics/service/${query}`, {}, { cookies });
  } catch (/** @type {any} */ err) {
    if (err?.status === 403) return { can_view: false, ...EMPTY };
    throw err;
  }
  const volume = data.volume ?? [];
  const from = volume[0]?.date;
  const windowQuery = from ? `?from=${encodeURIComponent(from)}` : '';
  const [csat, nrt] = await Promise.all([
    apiRequest(`/cases/csat/aggregate/${windowQuery}`, {}, { cookies }),
    apiRequest(`/cases/analytics/nrt/${windowQuery}`, {}, { cookies })
  ]);
  return {
    can_view: true,
    totals: data.totals ?? EMPTY.totals,
    volume,
    firstResponse: data.first_response ?? [],
    byType: data.by_type ?? [],
    byAgent: data.by_agent ?? [],
    csat: {
      average: csat.average ?? null,
      count: csat.count ?? 0,
      distribution: { ...EMPTY.csat.distribution, ...csat.distribution }
    },
    nextResponse: nrt.by_priority ?? []
  };
}
