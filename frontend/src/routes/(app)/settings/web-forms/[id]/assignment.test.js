import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));
vi.mock('$lib/server/v2/organization.js', () => ({ viewerIsAdmin: () => true }));

// This route module imports nothing from `$app/*`, so the standalone vitest
// config can load it (see the note in `vitest.config.js`).
const { actions } = await import('./+page.server.js');

/** @param {[string, string][]} entries */
function saveEvent(entries) {
  const body = new FormData();
  for (const [key, value] of entries) body.append(key, value);
  return /** @type {any} */ ({
    request: new Request('http://test/settings/web-forms/f1?/save', { method: 'POST', body }),
    cookies: { get: () => 'token' },
    params: { id: 'f1' }
  });
}

/** What the save action sent to the API. */
function sentBody() {
  const [url, opts] = apiRequest.mock.calls[0];
  expect(url).toBe('/webforms/f1/');
  return opts.body;
}

describe('web form save: who new leads go to', () => {
  beforeEach(() => {
    apiRequest.mockReset();
    apiRequest.mockResolvedValue({});
  });

  it('sends a rotation with its members and cap, and keeps the hidden person', async () => {
    await actions.save(
      saveEvent([
        ['name', 'Contact us'],
        ['assignment_mode', 'rotation'],
        ['assign_to', 'p9'],
        ['rotation_members', 'p1'],
        ['rotation_members', 'p2'],
        ['rotation_cap', ' 3 ']
      ])
    );

    const body = sentBody();
    expect(body.assignment_mode).toBe('rotation');
    expect(body.rotation_members).toEqual(['p1', 'p2']);
    expect(body.rotation_cap).toBe('3');
    // The person select is only hidden in rotation mode, so switching back
    // later finds the same person rather than "Nobody".
    expect(body.assign_to).toBe('p9');
  });

  it('sends an empty cap box as no cap', async () => {
    await actions.save(
      saveEvent([
        ['name', 'Contact us'],
        ['assignment_mode', 'rotation'],
        ['rotation_members', 'p1'],
        ['rotation_cap', '']
      ])
    );

    expect(sentBody().rotation_cap).toBeNull();
  });

  it('sends none of it from a ticket form, which renders none of it', async () => {
    await actions.save(
      saveEvent([
        ['name', 'Support'],
        ['assign_to', 'p9'],
        ['ticket_priority', 'High']
      ])
    );

    const body = sentBody();
    expect('assignment_mode' in body).toBe(false);
    expect('rotation_members' in body).toBe(false);
    expect('rotation_cap' in body).toBe(false);
  });

  it('shows the server refusal rather than a generic failure', async () => {
    apiRequest.mockRejectedValue(
      Object.assign(new Error('Choose at least one member to rotate between.'), { status: 400 })
    );

    const result = /** @type {any} */ (
      await actions.save(
        saveEvent([
          ['name', 'Contact us'],
          ['assignment_mode', 'rotation']
        ])
      )
    );

    expect(result.status).toBe(400);
    expect(result.data.save.error).toContain('at least one member');
  });
});
