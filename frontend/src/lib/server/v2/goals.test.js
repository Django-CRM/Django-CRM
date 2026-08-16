import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));
vi.mock('./org-people.js', () => ({
  getOrgPeopleAndTeams: async () => ({ people: [], teams: [] })
}));

const { listGoals } = await import('./goals.js');

/** A JWT whose payload carries just the role claim `viewerRole` reads. */
function tokenFor(role) {
  const payload = Buffer.from(JSON.stringify({ role })).toString('base64url');
  return `header.${payload}.signature`;
}

const cookies = /** @type {any} */ ({ get: () => tokenFor('ADMIN') });

/** One `SalesGoalSerializer` row, with only the fields under test spelled out. */
function goal(over = {}) {
  return {
    id: 'g1',
    name: 'Goal',
    goal_type: 'REVENUE',
    target_value: '100',
    period_type: 'MONTHLY',
    period_start: '2026-01-01',
    period_end: '2026-12-31',
    is_active: true,
    progress_value: 10,
    progress_percent: 10,
    status: 'on_track',
    ...over
  };
}

function respond({ goals = [], leaderboard = [] }) {
  apiRequest.mockImplementation(async (url) =>
    url.includes('leaderboard') ? { leaderboard } : { goals }
  );
}

describe('listGoals totals', () => {
  beforeEach(() => {
    apiRequest.mockReset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('sums targets and attainment over the active goals only', async () => {
    vi.setSystemTime(new Date(2026, 5, 1, 12));
    respond({
      goals: [
        goal({ id: 'a', target_value: '100', progress_value: 50 }),
        goal({ id: 'b', target_value: '200', progress_value: 20 }),
        goal({ id: 'c', target_value: '999', progress_value: 999, is_active: false })
      ]
    });

    const { totals } = await listGoals({ cookies });
    expect(totals.count).toBe(3);
    expect(totals.active).toBe(2);
    expect(totals.target).toBe(300);
    expect(totals.achieved).toBe(70);
  });

  it('still counts a goal whose period ends today as behind pace', async () => {
    // The boundary this fixes. `new Date('2026-06-01')` is midnight UTC, so
    // measuring it against `Date.now()` dropped the goal out of this count
    // part-way through its own final day, for every timezone east of
    // Greenwich and for most of the day. Noon local is chosen deliberately:
    // it is past midnight UTC, which is exactly when the old code broke.
    vi.setSystemTime(new Date(2026, 5, 1, 12));
    respond({ goals: [goal({ status: 'behind', period_end: '2026-06-01' })] });

    const { totals } = await listGoals({ cookies });
    expect(totals.behind).toBe(1);
  });

  it('leaves out a goal whose period has already ended', async () => {
    vi.setSystemTime(new Date(2026, 5, 1, 12));
    respond({ goals: [goal({ status: 'behind', period_end: '2026-05-31' })] });

    const { totals } = await listGoals({ cookies });
    expect(totals.behind).toBe(0);
  });

  it('leaves out a retired goal even when it is behind and still open', async () => {
    vi.setSystemTime(new Date(2026, 5, 1, 12));
    respond({
      goals: [goal({ status: 'behind', period_end: '2026-12-31', is_active: false })]
    });

    const { totals } = await listGoals({ cookies });
    expect(totals.behind).toBe(0);
  });
});

describe('listGoals leaderboard', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('reads the name the endpoint sends, which is no longer an email', async () => {
    // The row used to carry the address twice, as `name` and as `email`, so
    // this list printed raw addresses. There is nothing to fall back to now.
    respond({
      leaderboard: [
        {
          rank: 1,
          goal_id: 'g1',
          user: { id: 'p1', name: 'Ada Lovelace' },
          target: 100,
          achieved: 104,
          percent: 104
        }
      ]
    });

    const { leaderboard } = await listGoals({ cookies });
    expect(leaderboard[0].user).toBe('Ada Lovelace');
    // Uncapped, unlike `progress_percent`, which the model caps at 100.
    expect(leaderboard[0].percent).toBe(104);
  });

  it('says Unknown rather than blank when the user block is missing', async () => {
    respond({ leaderboard: [{ rank: 1, goal_id: 'g1' }] });
    const { leaderboard } = await listGoals({ cookies });
    expect(leaderboard[0].user).toBe('Unknown');
  });

  it('is empty rather than throwing when the endpoint narrows a member to nothing', async () => {
    // An ordinary outcome now that the board is scoped like the list: somebody
    // with no current monthly goal of their own ranks against nobody.
    respond({ leaderboard: [] });
    const { leaderboard } = await listGoals({ cookies });
    expect(leaderboard).toEqual([]);
  });
});

describe('listGoals can_edit', () => {
  beforeEach(() => {
    apiRequest.mockReset();
    respond({});
  });

  it('is true for an admin and false for a member', async () => {
    const asAdmin = await listGoals({ cookies });
    expect(asAdmin.can_edit).toBe(true);

    const asMember = await listGoals({
      cookies: /** @type {any} */ ({ get: () => tokenFor('USER') })
    });
    expect(asMember.can_edit).toBe(false);
  });

  it('is false when there is no token, rather than throwing', async () => {
    const signedOut = await listGoals({
      cookies: /** @type {any} */ ({ get: () => undefined })
    });
    expect(signedOut.can_edit).toBe(false);
  });
});

describe('activity goals and deal type weights', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('offers ACTIVITIES as a goal type the backend accepts', async () => {
    const { GOAL_TYPES } = await import('./goals.js');
    expect(GOAL_TYPES).toContain('ACTIVITIES');
  });

  it('names the five deal types a weight can be set on', async () => {
    const { DEAL_TYPES } = await import('./goals.js');
    expect(DEAL_TYPES).toEqual([
      'NEW_BUSINESS',
      'EXISTING_BUSINESS',
      'RENEWAL',
      'UPSELL',
      'CROSS_SELL'
    ]);
  });

  it('reads a weight map off the form, skipping the types left blank', async () => {
    const { weightsFromForm } = await import('./goals.js');
    const form = new FormData();
    form.set('weight_RENEWAL', '0.5');
    form.set('weight_NEW_BUSINESS', '');
    form.set('weight_UPSELL', '  ');

    expect(weightsFromForm(form)).toEqual({ RENEWAL: 0.5 });
  });

  it('passes a non-numeric weight through so the backend can name it', async () => {
    const { weightsFromForm } = await import('./goals.js');
    const form = new FormData();
    form.set('weight_RENEWAL', 'heavy');

    expect(weightsFromForm(form)).toEqual({ RENEWAL: 'heavy' });
  });

  it('sends the weight map on create', async () => {
    const { createGoal } = await import('./goals.js');
    apiRequest.mockResolvedValue({});

    await createGoal({ cookies }, { name: 'Q3', type_weights: { RENEWAL: 0.5 } });

    expect(apiRequest.mock.calls[0][1].body.type_weights).toEqual({ RENEWAL: 0.5 });
  });

  it('carries the stored weights through to the list', async () => {
    respond({ goals: [goal({ type_weights: { RENEWAL: 0.5 } })] });

    const { goals } = await listGoals({ cookies });

    expect(goals[0].type_weights).toEqual({ RENEWAL: 0.5 });
  });

  it('reports an unweighted goal as an empty map rather than undefined', async () => {
    respond({ goals: [goal()] });

    const { goals } = await listGoals({ cookies });

    expect(goals[0].type_weights).toEqual({});
  });
});

describe('listGoals filters', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('asks the API for the filters the page was given', async () => {
    respond({ goals: [] });

    await listGoals({ cookies, url: new URL('http://x/goals?period_type=QUARTERLY&q=emea') });

    const listUrl = apiRequest.mock.calls.map((c) => c[0]).find((u) => !u.includes('leaderboard'));
    expect(listUrl).toContain('period_type=QUARTERLY');
    expect(listUrl).toContain('search=emea');
  });

  it('sends only current goals when the page asks for them', async () => {
    respond({ goals: [] });

    await listGoals({ cookies, url: new URL('http://x/goals?window=current') });

    const listUrl = apiRequest.mock.calls.map((c) => c[0]).find((u) => !u.includes('leaderboard'));
    expect(listUrl).toContain('current=true');
  });

  it('ignores a period type that is not one the backend accepts', async () => {
    respond({ goals: [] });

    await listGoals({ cookies, url: new URL('http://x/goals?period_type=; DROP TABLE') });

    const listUrl = apiRequest.mock.calls.map((c) => c[0]).find((u) => !u.includes('leaderboard'));
    expect(listUrl).not.toContain('period_type');
  });

  it('reports the active filters back to the page', async () => {
    respond({ goals: [] });

    const data = await listGoals({
      cookies,
      url: new URL('http://x/goals?period_type=YEARLY&q=emea&window=current')
    });

    expect(data.filters).toEqual({ period_type: 'YEARLY', q: 'emea', window: 'current' });
  });
});

describe('getGoalHistory', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('reads finished periods from the history endpoint', async () => {
    apiRequest.mockResolvedValue({
      history: [
        {
          period_start: '2026-01-01',
          period_end: '2026-01-31',
          period_type: 'MONTHLY',
          goal_type: 'REVENUE',
          goals_count: 2,
          attained_count: 1,
          target: 300,
          achieved: 240,
          percent: 80,
          goals: [goal({ name: 'Jan' })]
        }
      ]
    });

    const { getGoalHistory } = await import('./goals.js');
    const { history } = await getGoalHistory({ cookies });

    expect(apiRequest.mock.calls[0][0]).toContain('/opportunities/goals/history/');
    expect(history[0].percent).toBe(80);
    expect(history[0].goal_type).toBe('REVENUE');
    expect(history[0].attained_count).toBe(1);
    expect(history[0].goals[0].name).toBe('Jan');
  });

  it('returns an empty history rather than throwing when there is none', async () => {
    apiRequest.mockResolvedValue({});

    const { getGoalHistory } = await import('./goals.js');

    expect((await getGoalHistory({ cookies })).history).toEqual([]);
  });
});

describe('getCurrentGoals', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('asks only for goals running today that are not paused', async () => {
    apiRequest.mockResolvedValue({ goals: [] });
    const { getCurrentGoals } = await import('./goals.js');

    await getCurrentGoals({ cookies });

    const url = apiRequest.mock.calls[0][0];
    expect(url).toContain('current=true');
    expect(url).toContain('active=true');
  });

  it('maps the rows the same way the goals list does', async () => {
    apiRequest.mockResolvedValue({ goals: [goal({ name: 'Q3', progress_percent: 62 })] });
    const { getCurrentGoals } = await import('./goals.js');

    const goals = await getCurrentGoals({ cookies });

    expect(goals[0].name).toBe('Q3');
    expect(goals[0].progress_percent).toBe(62);
    expect(goals[0].type_weights).toEqual({});
  });

  it('returns nothing rather than breaking the dashboard when the call fails', async () => {
    apiRequest.mockRejectedValue(new Error('boom'));
    const { getCurrentGoals } = await import('./goals.js');

    expect(await getCurrentGoals({ cookies })).toEqual([]);
  });
});
