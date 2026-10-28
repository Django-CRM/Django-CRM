import { describe, it, expect, vi, beforeEach } from 'vitest';

const getOutcomes = vi.fn();
vi.mock('#lib/server/v2/leads.js', () => ({
  getOutcomes: (...a: unknown[]) => getOutcomes(...a),
  responseDays: (raw: string | null) => ([7, 30, 90].includes(Number(raw)) ? Number(raw) : 30),
  RESPONSE_DAYS: [7, 30, 90]
}));
vi.mock('#lib/server/v2/organization.js', () => ({ viewerIsAdmin: () => true }));

const { load } = await import('./+page.server.js');

describe('outcomes page load', () => {
  beforeEach(() => {
    getOutcomes.mockReset();
  });

  it('returns the period, the measuring dates, the rows and the totals', async () => {
    const rows = [
      {
        profile: 'p1',
        name: 'A',
        won: 1,
        lost: 0,
        win_rate: 100,
        median_seconds_to_win: 60,
        new_leads: 2
      }
    ];
    const totals = { won: 1, lost: 0, win_rate: 100, median_seconds_to_win: 60, new_leads: 2 };
    getOutcomes.mockResolvedValueOnce({
      since: '2026-10-01',
      measured_since: '2026-10-10',
      rows,
      totals
    });
    const cookies = {} as never;
    const out = await (load as any)({ cookies, url: new URL('http://t/leads/outcomes?days=7') });
    expect(getOutcomes).toHaveBeenCalledWith({ cookies }, 7);
    expect(out).toEqual({
      days: 7,
      periods: [7, 30, 90],
      since: '2026-10-01',
      measured_since: '2026-10-10',
      rows,
      totals,
      isAdmin: true
    });
  });
});
