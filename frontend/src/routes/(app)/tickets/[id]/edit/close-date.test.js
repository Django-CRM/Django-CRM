/**
 * The close date on the edit form is optional. Left empty, the save sends no
 * `closed_on` at all and the API dates the close today in the org's timezone;
 * a date the person picked is sent as picked. An empty string is never sent.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { actions } = await import('./+page.server.js');

/** @param {Record<string, string>} fields */
function saveEvent(fields) {
  const body = new FormData();
  for (const [key, value] of Object.entries(fields)) body.set(key, value);
  return /** @type {any} */ ({
    request: new Request('http://test/tickets/a/edit?/save', { method: 'POST', body }),
    params: { id: 'a' },
    cookies: { get: () => 'token' }
  });
}

/** Run the save, which redirects on success, and hand back the PATCH body. */
async function savedBody(/** @type {Record<string, string>} */ fields) {
  await expect(actions.save(saveEvent(fields))).rejects.toMatchObject({ status: 303 });
  const [path, options] = apiRequest.mock.calls[0];
  expect(path).toBe('/cases/a/');
  expect(options.method).toBe('PATCH');
  return options.body;
}

beforeEach(() => {
  apiRequest.mockReset();
  apiRequest.mockResolvedValue({});
});

describe('closing from the edit form', () => {
  it('sends no closed_on when the date is left empty', async () => {
    const body = await savedBody({ name: 'Printer', status: 'Closed', closed_on: '' });
    expect(body).toEqual({ name: 'Printer', status: 'Closed' });
    expect('closed_on' in body).toBe(false);
  });

  it('sends a picked date as picked', async () => {
    const body = await savedBody({ name: 'Printer', status: 'Closed', closed_on: '2026-01-01' });
    expect(body.closed_on).toBe('2026-01-01');
  });

  it('still clears other emptied fields to null', async () => {
    const body = await savedBody({ name: 'Printer', status: 'New', case_type: '', closed_on: '' });
    expect(body).toEqual({ name: 'Printer', status: 'New', case_type: null });
  });
});
