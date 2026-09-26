import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { load } = await import('./+page.server.js');

/**
 * A failed people/teams fetch must reach the page as a failure, not as an org
 * with no teams: the edit form keeps the stored notify team as its own option
 * and says the list did not load, instead of falling back to "No team" and
 * clearing it on save.
 */
describe('escalation policies load', () => {
  const policies = [
    {
      id: 'e1',
      priority: 'Urgent',
      first_response_action: 'notify',
      resolution_action: 'notify',
      notify_team: { id: 't1', name: 'Support' },
      is_active: true
    }
  ];

  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('flags a failed picker fetch and still carries the stored team', async () => {
    apiRequest.mockImplementation((/** @type {string} */ path) =>
      path.startsWith('/cases/escalation-policies/')
        ? Promise.resolve({ policies })
        : Promise.reject(new Error('boom'))
    );
    const data = /** @type {any} */ (
      await load(/** @type {any} */ ({ cookies: { get: () => undefined } }))
    );
    expect(data.options_failed).toBe(true);
    expect(data.teams).toEqual([]);
    expect(data.policies[0].notify_team).toEqual({ id: 't1', name: 'Support' });
  });
});
