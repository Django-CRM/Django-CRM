/**
 * In-app notifications: the wiring behind /v2/notifications.
 *
 * Server-only. The feed is `GET /notifications/`, which already scopes to
 * `recipient=request.profile` (a user only ever sees their own). The writes
 * are `POST /notifications/<id>/read/` and `POST /notifications/read-all/`, both
 * recipient-scoped the same way, so nothing here can touch another person's
 * feed, and the page's job is only to call them.
 *
 * The one transform that matters is the LINK. `Notification.link` is a stored
 * *client* path a reader navigates to, and it comes in two shapes: old rows from
 * before the producer fix carry `/cases/<id>` (which no client serves), new CRM
 * rows carry `/tickets/<id>`, and product-support rows carry `/help/<id>`.
 * `resolvedLink` rebuilds a fresh internal path from a recognised prefix and
 * returns '' for anything else, so a stored value can
 * never be rendered as an arbitrary href (an `http(s)://…` or `javascript:` link
 * that slipped into the column can't become a live link or an open redirect),
 * and a correctly-produced `/tickets/` link is not left pointing at the v1 route.
 */
import { apiRequest } from '#lib/api-helpers.js';

type Schemas = components['schemas'];
import { UUID, OBJECT_KEY } from '#lib/v2/custom-objects.js';
import type { Cookies } from '@sveltejs/kit';
import type { components } from '#lib/api-schema.js';

/** Verbs the backend actually dispatches. Everything else has no copy and is
 *  flagged on the page as "no producer" rather than shown as a raw identifier. */
export const PRODUCED_VERBS = [
  'case.mentioned',
  'case.commented',
  'support.replied',
  'support.status_changed',
  'automation.message',
  'automation.failed',
  'automation.paused',
  'automation.limit',
  'project.budget',
  'project.retainer_invoice',
  'estimate.accepted',
  'estimate.declined',
  'lead.assigned',
  'lead.created',
  'lead.enquired',
  'lead.waiting',
  'real_estate.match',
  'education.seat_open'
];

/**
 * A stored notification link → a safe internal destination, or '' if it is not
 * a link we recognise. Both `/cases/<id>` (pre-fix rows) and `/tickets/<id>`
 * (current rows) normalise to `/tickets/<id>`. Product support normalises to
 * `/help/<id>`, and accepts the `/support/<id>` spelling its producer wrote
 * before the page was renamed, for the same reason `/cases/` is still accepted.
 */
export function resolvedLink(link: unknown): string {
  if (typeof link !== 'string') return '';
  const m = link.match(/^\/(?:cases|tickets)\/([^/?#]+)\/?$/);
  if (m) return `/tickets/${encodeURIComponent(m[1])}`;
  const help = link.match(/^\/(?:support|help)\/([^/?#]+)\/?$/);
  if (help) return `/help/${encodeURIComponent(help[1])}`;
  return automationLink(link);
}

/**
 * Record and settings links the automation engine, project budgets and
 * estimates write; every id a UUID.
 */
function automationLink(link: string) {
  if (link === '/settings/automation' || link === '/invoices/estimates') return link;
  const rec = link.match(
    /^\/(leads|contacts|accounts|pipeline|tasks|projects|invoices|settings\/automation)\/([^/?#]+)$/
  );
  if (rec) return UUID.test(rec[2]) ? link : '';
  const obj = link.match(/^\/objects\/([^/?#]+)\/([^/?#]+)$/);
  return obj && OBJECT_KEY.test(obj[1]) && UUID.test(obj[2]) ? link : '';
}

/** One API notification → the row shape the page renders. */
function toRow(n: Schemas['Notification']) {
  return {
    ...n,
    known_verb: PRODUCED_VERBS.includes(n.verb),
    resolved_link: resolvedLink(n.link)
  };
}

const FEED_LIMIT = 50;

/** The recipient's feed, shaped for the page. */
export async function getNotifications({ cookies }: { cookies: Cookies }) {
  const resp: Schemas['NotificationListResponse'] = await apiRequest(
    `/notifications/?limit=${FEED_LIMIT}`,
    {},
    { cookies }
  );
  const results = (resp.results || []).map(toRow);
  return {
    results,
    totals: {
      count: resp.count ?? results.length,
      unread: resp.unread_count ?? results.filter((n) => n.read_at === null).length,
      // Rows still carrying the dead `/cases/` prefix. Counted within the feed
      // the page is showing, surfaced once as a footnote (a tripwire for a
      // producer regression, not a per-row badge).
      broken_links: results.filter(
        (n) => typeof n.link === 'string' && n.link.startsWith('/cases/')
      ).length
    }
  };
}

/**
 * Unread count for the nav badge. A cheap `limit=1` call that still returns the
 * authoritative `unread_count` over the whole feed.
 */
export async function countUnread({ cookies }: { cookies: Cookies }) {
  const resp: Schemas['NotificationListResponse'] = await apiRequest(
    '/notifications/?limit=1',
    {},
    { cookies }
  );
  return resp.unread_count ?? 0;
}

/** Mark one notification read. Idempotent server-side. */
export async function markNotificationRead({ cookies }: { cookies: Cookies }, id: string) {
  return apiRequest(`/notifications/${id}/read/`, { method: 'POST' }, { cookies });
}

/** Mark every unread notification read. */
export async function markAllNotificationsRead({ cookies }: { cookies: Cookies }) {
  return apiRequest('/notifications/read-all/', { method: 'POST', body: {} }, { cookies });
}
