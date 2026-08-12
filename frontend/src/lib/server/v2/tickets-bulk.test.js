import { describe, it, expect } from 'vitest';
import { summarizeBulk } from '$lib/server/v2/tickets.js';

describe('summarizeBulk', () => {
  it('counts each outcome status', () => {
    const s = summarizeBulk([
      { id: '1', status: 'updated' },
      { id: '2', status: 'updated' },
      { id: '3', status: 'no_access' },
      { id: '4', status: 'approval_required' },
      { id: '5', status: 'closed_on_required' },
      { id: '6', status: 'invalid' }
    ]);
    expect(s.updated).toBe(2);
    expect(s.no_access).toBe(1);
    expect(s.approval_required).toBe(1);
    expect(s.closed_on_required).toBe(1);
    expect(s.invalid).toBe(1);
  });

  it('handles an empty list', () => {
    const s = summarizeBulk([]);
    expect(s.updated).toBe(0);
    expect(s.no_access).toBe(0);
  });
});
