import { describe, it, expect, vi, beforeEach } from 'vitest';

// Local mock plus a thin wrapper, matching every other test in this
// directory (business-hours.test.js, tags.test.js, etc). Destructuring
// `apiRequest` straight off `await import('$lib/api-helpers.js')` types it
// against the real module's declared signature, which has no
// `mockReset`/`mockResolvedValue`, and fails `svelte-check`.
const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { getOrgPeopleAndTeams, resolveMe } = await import('./org-people.js');

const cookies = /** @type {any} */ ({});

describe('getOrgPeopleAndTeams', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('labels a person by name when there is one, and carries the email through so @me can be resolved', async () => {
    apiRequest.mockResolvedValue({
      profiles: [{ id: 'p1', user_details: { name: 'Ada Lovelace', email: 'ada@x.io' } }],
      teams: []
    });
    const { people } = await getOrgPeopleAndTeams(cookies);
    expect(people).toEqual([{ id: 'p1', name: 'Ada Lovelace', email: 'ada@x.io' }]);
  });

  it('falls back to the email, then to Unnamed', async () => {
    apiRequest.mockResolvedValue({
      profiles: [
        { id: 'p1', user_details: { email: 'ada@x.io' } },
        { id: 'p2', user_details: {} },
        { id: 'p3' }
      ],
      teams: []
    });
    const { people } = await getOrgPeopleAndTeams(cookies);
    expect(people.map((p) => p.name)).toEqual(['ada@x.io', 'Unnamed', 'Unnamed']);
  });

  it('flattens teams to id and name', async () => {
    apiRequest.mockResolvedValue({
      profiles: [],
      teams: [{ id: 't1', name: 'Support', description: 'ignored', users: [1, 2] }]
    });
    const { teams } = await getOrgPeopleAndTeams(cookies);
    expect(teams).toEqual([{ id: 't1', name: 'Support' }]);
  });

  it('returns empty lists rather than throwing when the fetch fails', async () => {
    apiRequest.mockImplementation(() => {
      throw new Error('boom');
    });
    await expect(getOrgPeopleAndTeams(cookies)).resolves.toEqual({
      people: [],
      teams: [],
      options_failed: true
    });
  });

  it('survives a response missing both keys, and does not call that a failure', async () => {
    // An org with nobody in it is a real answer. Only a failed fetch may make
    // an edit form keep what it cannot show.
    apiRequest.mockResolvedValue({});
    await expect(getOrgPeopleAndTeams(cookies)).resolves.toEqual({
      people: [],
      teams: [],
      options_failed: false
    });
  });
});

describe('resolveMe', () => {
  const people = [
    { id: 'p1', name: 'Ada', email: 'ada@x.com' },
    { id: 'p2', name: 'Bo', email: 'bo@x.com' }
  ];

  it('matches on email, case insensitively', () => {
    expect(resolveMe(people, 'ADA@x.com')).toBe('p1');
  });

  it('returns null when nobody matches, so the caller can hide the preset', () => {
    // A "Mine" link that filters to nobody is worse than no link: it looks
    // like the viewer owns nothing.
    expect(resolveMe(people, 'nobody@x.com')).toBe(null);
  });

  it('returns null for a missing viewer email', () => {
    expect(resolveMe(people, undefined)).toBe(null);
  });
});
