/**
 * SLA fields on the ticket row.
 *
 * The rail renders three states off these: on track, at risk (amber), and
 * breached (rust). At-risk is the one the backend computes, because the band
 * is a fraction of the ticket's own configured target and the client does not
 * know that target's business-hours window.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { getTicket } = await import('./tickets.js');

const cookies = /** @type {any} */ ({ get: () => 'token' });
const event = /** @type {any} */ ({ cookies });

/** A detail payload with no account, so `getTicket` makes exactly one call. */
function detail(overrides = {}) {
  return {
    cases_obj: {
      id: 'c1',
      name: 'Printer down',
      status: 'New',
      priority: 'Low',
      created_at: '2026-08-16T00:00:00Z',
      ...overrides
    },
    comment_permission: false
  };
}

describe('SLA state on the ticket row', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('carries the at-risk flags through from the API', async () => {
    apiRequest.mockResolvedValue(
      detail({ is_sla_first_response_at_risk: true, is_sla_resolution_at_risk: false })
    );
    const { ticket } = await getTicket(event, 'c1');
    expect(ticket.first_response_at_risk).toBe(true);
    expect(ticket.resolution_at_risk).toBe(false);
  });

  it('defaults both flags to false when the API omits them', async () => {
    apiRequest.mockResolvedValue(detail());
    const { ticket } = await getTicket(event, 'c1');
    expect(ticket.first_response_at_risk).toBe(false);
    expect(ticket.resolution_at_risk).toBe(false);
  });

  it('exposes the resolution target hours the rail labels the row with', async () => {
    apiRequest.mockResolvedValue(detail({ sla_resolution_hours: 72 }));
    const { ticket } = await getTicket(event, 'c1');
    expect(ticket.resolution_hours).toBe(72);
  });

  it('never reports at-risk and breached together for one target', async () => {
    /* The backend makes these exclusive; this pins that the mapping does not
       invent an overlap by, say, deriving at-risk from a missing deadline. */
    apiRequest.mockResolvedValue(
      detail({
        is_sla_first_response_breached: true,
        is_sla_first_response_at_risk: false
      })
    );
    const { ticket } = await getTicket(event, 'c1');
    expect(ticket.first_response_breached).toBe(true);
    expect(ticket.first_response_at_risk).toBe(false);
  });
});
