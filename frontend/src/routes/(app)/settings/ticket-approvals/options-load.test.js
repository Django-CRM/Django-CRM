import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { load } = await import('./+page.server.js');

/**
 * A failed people/teams fetch must reach the page as a failure, not as an org
 * with no teams: the edit form keeps the stored team as its own option and
 * says the list did not load, instead of falling back to "Any team" and
 * clearing the rule's team on save.
 */
describe('approval rules load', () => {
  const rules = {
    rules: [{ id: 'r1', name: 'Urgent closes', match_team: { id: 't1', name: 'Support' } }],
    totals: { count: 1, active: 1, pending: 0 }
  };

  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('flags a failed picker fetch and still carries the stored team', async () => {
    apiRequest.mockImplementation((/** @type {string} */ path) =>
      path === '/cases/approval-rules/' ? Promise.resolve(rules) : Promise.reject(new Error('boom'))
    );
    const data = /** @type {any} */ (
      await load(/** @type {any} */ ({ cookies: { get: () => undefined } }))
    );
    expect(data.options_failed).toBe(true);
    expect(data.teams).toEqual([]);
    expect(data.rules[0].match_team).toEqual({ id: 't1', name: 'Support' });
  });

  it('does not flag an org that simply has no teams', async () => {
    apiRequest.mockImplementation((/** @type {string} */ path) =>
      Promise.resolve(path === '/cases/approval-rules/' ? rules : { profiles: [], teams: [] })
    );
    const data = /** @type {any} */ (
      await load(/** @type {any} */ ({ cookies: { get: () => undefined } }))
    );
    expect(data.options_failed).toBe(false);
  });
});
