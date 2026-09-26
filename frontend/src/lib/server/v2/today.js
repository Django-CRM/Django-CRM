/**
 * The Today queue: the wiring behind the v2 home (`/`).
 *
 * Server-only. Reads one endpoint, `GET /dashboard/today/`, which rolls four
 * sources into a single prioritised action list: support cases still awaiting a
 * first reply, invoices past due, deals that have gone quiet (stage-aging), and
 * tasks overdue or due today. The endpoint is org-scoped from the JWT and, for a
 * member, narrowed to rows whose action they can take (a ticket they only watch
 * is left out, since they cannot reply to it), so nothing here decides
 * visibility; the API does.
 *
 * This is read-only: the home page links out to each item's own module, where
 * the writes already live. There is no write path to wire here.
 *
 * The response shape IS the page's shape (`{ queue, summary, later }`), so
 * there is nothing to reshape; the fixture that preceded it was built to match
 * this contract. `queue[].href` is always an internal `/...` path the server
 * built (never a stored value), so there is no untrusted-link concern here.
 */
import { apiRequest } from '$lib/api-helpers.js';

/**
 * The current user's Today queue, shaped for the page.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @returns {Promise<{ queue: any[], summary: any, later: any[] }>}
 */
export async function getToday({ cookies }) {
  const resp = await apiRequest('/dashboard/today/', {}, { cookies });
  const queue = resp.queue ?? [];
  const summary = resp.summary ?? {};
  return {
    queue,
    summary: {
      count: summary.count ?? 0,
      // `shown` is what the page is about to render and `count` is the org's
      // real total; the page compares them before it can claim to have shown
      // everything. Defaulting `shown` to the queue length rather than 0 keeps
      // that comparison honest if the field is ever missing.
      shown: summary.shown ?? queue.length,
      sources: summary.sources ?? [],
      quiet_deals: summary.quiet_deals ?? 0,
      quiet_value: summary.quiet_value ?? 0,
      cleared_yesterday: summary.cleared_yesterday ?? 0
    },
    later: resp.later ?? []
  };
}
