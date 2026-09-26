import { describe, it, expect } from 'vitest';
import { dealNextStep } from '$lib/v2/deal-next-step.js';

const now = new Date('2026-09-26T12:00:00');
const task = { id: 't1', title: 'Send proposal', due_date: '2026-10-01' };

describe('dealNextStep', () => {
  it('flags an open deal with no next step', () => {
    expect(dealNextStep({ stage_kind: 'open', next_activity: null }, now)).toEqual({
      text: 'No next step',
      late: true
    });
  });

  it('shows the task on an open deal, late only when overdue', () => {
    expect(dealNextStep({ stage_kind: 'open', next_activity: task }, now)).toEqual({
      text: 'Send proposal · 1 Oct',
      late: false
    });
    const overdue = { ...task, due_date: '2026-09-20' };
    expect(dealNextStep({ stage_kind: 'open', next_activity: overdue }, now)?.late).toBe(true);
    const undated = { ...task, due_date: null };
    expect(dealNextStep({ stage_kind: 'open', next_activity: undated }, now)).toEqual({
      text: 'Send proposal · no date',
      late: false
    });
  });

  it.each(['won', 'lost'])('shows nothing on a %s deal, flag or task', (kind) => {
    expect(dealNextStep({ stage_kind: kind, next_activity: null }, now)).toBeNull();
    expect(dealNextStep({ stage_kind: kind, next_activity: task }, now)).toBeNull();
  });

  it('reads the kind, not the code: a won stage named anything is still won', () => {
    const deal = { stage: 'SIGNED', stage_kind: 'won', next_activity: null };
    expect(dealNextStep(deal, now)).toBeNull();
  });

  it('shows nothing when the response did not compute it', () => {
    expect(dealNextStep({ stage_kind: 'open' }, now)).toBeNull();
  });
});
