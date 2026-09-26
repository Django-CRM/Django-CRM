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

describe('linking a ticket under a parent', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('posts the parent id to link/', async () => {
    apiRequest.mockResolvedValue({ id: 't', parent: { id: 'p' } });
    const { linkTicketParent } = await import('./tickets.js');
    await linkTicketParent(event, 't', 'p');

    expect(apiRequest).toHaveBeenCalledWith(
      '/cases/t/link/',
      { method: 'POST', body: { parent_id: 'p' } },
      { cookies: event.cookies }
    );
  });

  it('detaches with an explicit null, never an absent key', async () => {
    apiRequest.mockResolvedValue({ id: 't', parent: null });
    const { linkTicketParent } = await import('./tickets.js');
    await linkTicketParent(event, 't', null);

    expect(apiRequest.mock.calls[0][1]).toEqual({ method: 'POST', body: { parent_id: null } });
  });

  it('carries is_problem onto the ticket', async () => {
    apiRequest.mockResolvedValue({
      cases_obj: { ...row('a', null), is_problem: true },
      comment_permission: true
    });
    const { ticket } = await getTicket(event, 'a');
    expect(ticket.is_problem).toBe(true);
  });
});

describe('a ticket the viewer cannot open', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('is a not-found page, the same one a missing ticket gets', async () => {
    apiRequest.mockRejectedValue(
      Object.assign(new Error('No such case.'), {
        status: 404,
        body: { detail: 'No such case.' }
      })
    );
    await expect(getTicket(event, 'hidden')).rejects.toMatchObject({
      status: 404,
      body: { message: 'That ticket does not exist, or you do not have access to it.' }
    });
  });
});

describe('what the page may offer', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  // `canReply` gates replying, status changes, logging time, the timer and
  // linking a parent: the API's write rule, never a guess from the role.
  it.each([
    [true, true],
    [false, false],
    [undefined, false]
  ])('comment_permission %s reads as canReply %s', async (flag, expected) => {
    apiRequest.mockResolvedValue({ cases_obj: row('a', null), comment_permission: flag });
    const { canReply } = await getTicket(event, 'a');
    expect(canReply).toBe(expected);
  });
});
