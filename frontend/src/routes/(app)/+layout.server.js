import { isOrgAdmin } from '$lib/admin.js';
import { listLeads } from '$lib/server/v2/leads.js';
import { listDeals } from '$lib/server/v2/deals.js';
import { listTickets, OPEN_STATUSES } from '$lib/server/v2/tickets.js';
import { listTasks } from '$lib/server/v2/tasks.js';
import { listInvoices } from '$lib/server/v2/invoices.js';
import { countAwaitingApprovals } from '$lib/server/v2/approvals.js';
import { countUnread } from '$lib/server/v2/notifications.js';
import { getOrgTerminology } from '$lib/server/v2/organization.js';

/**
 * Counts for migrated modules, fetched for real and merged over the fixtures.
 *
 * Leaving a migrated module on its fixture count puts "Leads 24" in the
 * sidebar beside "18 open" in the page, and a nav badge that disagrees with
 * the page it links to is worse than no badge.
 *
 * Add a line here in the same change that adds a prefix to `migrated.js`.
 */
const LIVE_COUNTS = {
  /** @param {any} event */
  leads: async (event) =>
    (await listLeads(event, new URLSearchParams({ limit: '1' }))).totals.count,
  /**
   * Open deals only, same rule as tickets and tasks below: a badge counting
   * closed deals would never go down. `listDeals` no longer assumes this on
   * its own (`$lib/server/v2/deals.js`'s FILTER_FIELDS note), so it is spelled
   * out here.
   *
   * @param {any} event
   */
  pipeline: async (event) =>
    (await listDeals(event, new URLSearchParams({ open: 'true', limit: '1' }))).totals.count,
  /**
   * Open tickets only. A support badge counting closed ones would never go
   * down, which is the same objection as the accounts note below.
   *
   * @param {any} event
   */
  tickets: async (event) => {
    const params = new URLSearchParams({ limit: '1' });
    for (const status of OPEN_STATUSES) params.append('status', status);
    return (await listTickets(event, params)).totals.open;
  },
  /**
   * Open tasks only, and the API already counts them per requester. An
   * admin's badge covers the org, a member's covers their own list. Same
   * argument as tickets: a badge counting completed tasks would only ever go
   * up.
   *
   * @param {any} event
   */
  tasks: async (event) => (await listTasks(event, new URLSearchParams({ limit: '1' }))).totals.open,
  /**
   * Invoices waiting on this person: drafts to send plus anything overdue to
   * chase, counted by the API per requester. Not the total invoice count.
   * That is inventory, the same objection as the accounts note below, and not
   * the outstanding *amount*, which the pills on the page already show. The
   * shell has always carried a needs-action invoice badge; this keeps it real.
   *
   * @param {any} event
   */
  invoices: async (event) =>
    (await listInvoices(event, new URLSearchParams({ limit: '1' }))).totals.action_needed,
  /**
   * Approvals waiting on *this* person: the ones only they can clear (the
   * inbox scopes `mine=true&state=pending` to the actionable set, and a
   * requester's own rows are excluded by the self-approval rule). This is work,
   * not inventory: it goes down as they decide, so it earns a badge.
   *
   * @param {any} event
   */
  approvals: async (event) => await countAwaitingApprovals(event),
  /**
   * Unread notifications waiting on this person; `GET /notifications/` scopes
   * to `recipient=request.profile` and reports `unread_count` over the whole
   * feed, so the badge matches the page it links to (which was the point of
   * this map). This is work, not inventory: it goes to zero as you read, so it
   * earns a badge. Was the fixture `notificationTotals.unread` until now.
   *
   * @param {any} event
   */
  notifications: async (event) => await countUnread(event)
  /*
   * No `accounts` entry, deliberately, even though the module is wired.
   *
   * These badges count work: leads to call, deals to move, tickets to answer.
   * "Accounts 10" counts inventory. It never goes down, nothing about it is
   * ever waiting on you, and a badge you learn to ignore teaches you to ignore
   * the ones beside it. The nav has never had an accounts count and should not
   * gain one just because the number became available.
   *
   * No `team` entry either. A people count is the same inventory objection, and
   * the one actionable number there, live tokens on deactivated accounts, is
   * admin-only (the count endpoint 403s a member) and the page already surfaces
   * it prominently. A badge only some roles can compute does not fit a shell
   * that is the same for everyone.
   */
};

/**
 * The shell: nav counts, the org name, and vertical-pack terminology.
 *
 * The org name is display-only and comes from the JWT via `locals.org`, never
 * the client, never a fixture. Everything below runs concurrently (in sequence
 * it would add a round trip per module to every page) and starts empty: every
 * badge the sidebar renders is in LIVE_COUNTS, so a count that fails just shows
 * no badge rather than a stale number. `terminology` joins the same fan-out.
 * A failed fetch there just leaves the sidebar's hard-coded labels in place
 * (every consumer reads it through `$lib/terminology.js#t()`, which always
 * takes an explicit fallback), the same "missing, not stale or broken" contract
 * as a missing count badge.
 *
 * Still worth fixing: each count builds an entire page context: accounts,
 * tags, users, industries, to answer a question about one integer. One counts
 * endpoint covering every module is the right shape; N list calls is not.
 *
 * @type {import('./$types').LayoutServerLoad}
 */
export async function load(event) {
  const shell = {
    counts: /** @type {Record<string, number>} */ ({}),
    org: {
      name: event.locals.org?.name || 'BottleCRM',
      terminology: /** @type {Record<string, string> | undefined} */ (undefined),
      // The currency for figures that are sums rather than one record: pipeline
      // totals, invoice ageing, goal progress. A per-record currency cannot
      // label a sum, and `money()` falls back to a hardcoded USD when it is not
      // told, which printed a dollar sign over a euro figure on every page that
      // forgot. It lives here rather than in each page loader because 17 of
      // them needed it and copying the same line 17 times is how the first one
      // fell out of step.
      //
      // This does not make a mixed-currency sum correct: the API adds `amount`
      // across rows regardless of currency, so an org running more than one
      // gets an addition that should not have happened, and no symbol repairs
      // it. Read from the JWT's org settings, which is also where a new deal's
      // currency comes from.
      currency: /** @type {any} */ (event.locals).org_settings?.default_currency || 'USD',
      // The org's IANA zone, from the same claim, for a form's "today"
      // default (`todayIn`, `$lib/v2/dates.js`). UTC only for a token minted
      // before the claim existed; the next refresh carries it.
      timezone: /** @type {any} */ (event.locals).org_settings?.timezone || 'UTC'
    },
    // Server-derived from the JWT (never the client), and read only through
    // `isOrgAdmin` (`$lib/admin.js`). Display-only: it lets the shell hide
    // destinations a member can only reach to be turned away. The backend
    // still enforces every one of those gates, so this is UX, not a security
    // control. The non-admin view when the claim is absent.
    is_organization_admin: isOrgAdmin(event.locals.profile)
  };

  // countKeys' fetches and the terminology fetch are pushed into ONE
  // Promise.allSettled call so they all fire in the same network wave. The
  // terminology lookup is not a second round trip, it rides the wave that was
  // already here for the badges. `results` is indexed by position: the count
  // keys first (in `countKeys` order), terminology last.
  const countKeys = Object.keys(LIVE_COUNTS);
  const results = await Promise.allSettled([
    ...countKeys.map((key) => LIVE_COUNTS[/** @type {keyof typeof LIVE_COUNTS} */ (key)](event)),
    getOrgTerminology(event)
  ]);

  countKeys.forEach((key, index) => {
    const result = results[index];
    if (result.status === 'fulfilled') shell.counts[key] = result.value;
  });

  const terminologyResult = results[countKeys.length];
  if (terminologyResult.status === 'fulfilled') {
    shell.org.terminology = terminologyResult.value.terminology;
  }

  return shell;
}
