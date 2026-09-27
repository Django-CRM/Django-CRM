import { describe, it, expect } from 'vitest';
import { todayIn, addDays } from '$lib/v2/dates.js';

describe('todayIn', () => {
  // 23:30 UTC on 31 Dec is 05:00 on 1 Jan in Kolkata (UTC+5:30).
  const lateNewYearsEve = new Date('2025-12-31T23:30:00Z');

  it('is the org’s day across a UTC midnight boundary', () => {
    expect(todayIn('Asia/Kolkata', lateNewYearsEve)).toBe('2026-01-01');
  });

  it('is still the old day west of UTC', () => {
    expect(todayIn('America/New_York', lateNewYearsEve)).toBe('2025-12-31');
    expect(todayIn('UTC', lateNewYearsEve)).toBe('2025-12-31');
  });

  it('is the prior day in the Americas after UTC midnight', () => {
    expect(todayIn('America/Los_Angeles', new Date('2026-03-01T03:00:00Z'))).toBe('2026-02-28');
  });

  it('reads a legacy zone name as its IANA target', () => {
    expect(todayIn('US/Eastern', lateNewYearsEve)).toBe('2025-12-31');
  });

  it('falls back to UTC for a zone the runtime does not know', () => {
    expect(todayIn('Not/AZone', lateNewYearsEve)).toBe('2025-12-31');
  });
});

describe('addDays', () => {
  it('crosses a month and a year', () => {
    expect(addDays('2026-12-15', 30)).toBe('2027-01-14');
    expect(addDays('2026-01-31', 1)).toBe('2026-02-01');
  });

  it('is not moved by a DST change', () => {
    expect(addDays('2026-03-07', 2)).toBe('2026-03-09');
  });
});
