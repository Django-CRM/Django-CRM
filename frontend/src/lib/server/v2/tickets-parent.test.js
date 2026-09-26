/**
 * The parent a ticket sits under, as the list and the detail load read it.
 *
 * `parent_summary` names the parent only when the viewer may open it. A hidden
 * parent arrives as `{ id, name: null, status: null, restricted: true }` (D51,
 * the same redaction `/tree/` applies to a hidden node), and must read as the
 * same phrase the tree uses rather than as an empty or null name.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { RESTRICTED_TICKET_NAME } from '$lib/v2/enums.js';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { getTicket, listTickets } = await import('./tickets.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

const HIDDEN = { id: 'p-hidden', name: null, status: null, restricted: true };
const READABLE = { id: 'p-open', name: 'Outage umbrella', status: 'Pending', restricted: false };

/** @param {any} parent_summary */
function row(id, parent_summary) {
  return { id, name: `Ticket ${id}`, status: 'New', priority: 'Low', parent_summary };
}

describe('a ticket parent', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('maps each list row on its own parent', async () => {
    apiRequest.mockResolvedValue({
      cases: [row('a', HIDDEN), row('b', READABLE), row('c', null)]
    });
    const { results } = await listTickets(event);

    expect(results.map((t) => t.parent)).toEqual([
      { id: 'p-hidden', name: RESTRICTED_TICKET_NAME, status: null, restricted: true },
      { id: 'p-open', name: 'Outage umbrella', status: 'Pending', restricted: false },
      null
    ]);
  });

  it('never renders a hidden parent as a blank name on the detail', async () => {
    apiRequest.mockResolvedValue({ cases_obj: row('a', HIDDEN), comment_permission: false });
    const { ticket } = await getTicket(event, 'a');

    expect(ticket.parent).toEqual({
      id: 'p-hidden',
      name: RESTRICTED_TICKET_NAME,
      status: null,
      restricted: true
    });
  });

  it('is the phrase the tree uses for a hidden node', () => {
    expect(RESTRICTED_TICKET_NAME).toBe('A ticket you cannot open');
  });
});
