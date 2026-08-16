/**
 * What the rail reads off a ticket: the activity feed, and the time totals.
 *
 * Both are plain passthroughs of `ActivitySerializer` and `CaseSerializer`
 * keys, and both were wrong in the same way at some point: a key that the API
 * does not emit renders as an empty string rather than as an error, so the
 * page looks fine and says nothing. These pin the key names.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { getTicket } = await import('./tickets.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

/** A detail payload with no account, so `getTicket` makes exactly one call. */
function detail(extra = {}) {
  return {
    cases_obj: { id: 'c1', name: 'Printer down', status: 'New', priority: 'Low' },
    comment_permission: false,
    ...extra
  };
}

describe('the activity feed', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('reads the time, the verb and the actor from the keys the API sends', async () => {
    apiRequest.mockResolvedValue(
      detail({
        activities: [
          {
            id: 'a1',
            action: 'TIME_LOGGED',
            action_display: 'Time Logged',
            created_at: '2026-08-16T09:00:00Z',
            user: { id: 'p1', user_details: { email: 'agent@example.com' } }
          }
        ]
      })
    );

    const { activity } = await getTicket(event, 'c1');

    expect(activity).toEqual([
      {
        id: 'a1',
        action: 'TIME_LOGGED',
        label: 'Time Logged',
        at: '2026-08-16T09:00:00Z',
        by: 'agent@example.com'
      }
    ]);
  });

  it('falls back to the raw verb, and calls an actorless row nobody', async () => {
    // A Celery task or a management command writes rows with no user; the
    // page renders "System" for a null `by`, which is the truth there.
    apiRequest.mockResolvedValue(
      detail({
        activities: [
          { id: 'a2', action: 'ESCALATED', created_at: '2026-08-16T09:00:00Z', user: null }
        ]
      })
    );

    const { activity } = await getTicket(event, 'c1');

    expect(activity[0].label).toBe('ESCALATED');
    expect(activity[0].by).toBe(null);
    expect(activity[0].at).toBe('2026-08-16T09:00:00Z');
  });
});

describe('the time totals on the ticket', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('carries the whole-ticket summary through for the time panel', async () => {
    apiRequest.mockResolvedValue(
      detail({
        cases_obj: {
          id: 'c1',
          name: 'Printer down',
          status: 'New',
          priority: 'Low',
          time_summary: { total_minutes: 105, billable_minutes: 45, by_profile: [] }
        }
      })
    );

    const { ticket } = await getTicket(event, 'c1');

    expect(ticket.time_summary.total_minutes).toBe(105);
  });

  it('is null on a row from the list endpoint, which does not carry it', async () => {
    apiRequest.mockResolvedValue(detail());

    const { ticket } = await getTicket(event, 'c1');

    expect(ticket.time_summary).toBe(null);
  });
});
