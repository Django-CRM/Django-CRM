/**
 * The ticket forms never offer Duplicate.
 *
 * Duplicate is reached only by merging, and the API refuses it from a create
 * or an edit. A ticket that already is one keeps it in its edit select, so the
 * select has a matching option and a save does not quietly change the status.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { getTicketFormOptions, getTicketForEdit } = await import('./tickets.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

const STATUS_CHOICE = [
  ['New', 'New'],
  ['Assigned', 'Assigned'],
  ['Pending', 'Pending'],
  ['Closed', 'Closed'],
  ['Rejected', 'Rejected'],
  ['Duplicate', 'Duplicate']
];

/** @param {string} status */
function answer(status) {
  apiRequest.mockImplementation(async (/** @type {string} */ path) => {
    return path.startsWith('/cases/?')
      ? { status: STATUS_CHOICE, priority: [], type_of_case: [] }
      : { cases_obj: { id: 'c1', name: 'Printer', status, priority: 'Normal' } };
  });
}

/** @param {{ statuses: { value: string }[] }} data */
const values = (data) => data.statuses.map((s) => s.value);

describe('ticket status choices', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('the create form leaves Duplicate out', async () => {
    answer('New');
    const data = await getTicketFormOptions(event);
    expect(values(data)).toEqual(['New', 'Assigned', 'Pending', 'Closed', 'Rejected']);
  });

  it('the edit form leaves Duplicate out for an ordinary ticket', async () => {
    answer('Pending');
    const data = await getTicketForEdit(event, 'c1');
    expect(values(data)).not.toContain('Duplicate');
  });

  it('the edit form keeps Duplicate for a ticket that already is one', async () => {
    answer('Duplicate');
    const data = await getTicketForEdit(event, 'c1');
    expect(values(data)).toContain('Duplicate');
    expect(data.form.status).toBe('Duplicate');
  });
});
