import { describe, it, expect } from 'vitest';
import { approvalView } from './approval.js';

const rule = { id: 'r1', name: 'Urgent close' };
const viewer = { canReply: true, isOpen: true };

/** @param {Record<string, any>} over */
function row(over = {}) {
  return {
    id: 'ap1',
    state: 'pending',
    can_act: false,
    can_cancel: false,
    is_own_request: false,
    rule: { id: 'r1', name: 'Urgent close' },
    ...over
  };
}

describe('approvalView', () => {
  it('shows nothing on a ticket no rule gates and nobody asked about', () => {
    expect(approvalView([], null, viewer)).toMatchObject({ show: false, canRequest: false });
  });

  it('offers a request when a rule gates the ticket and none is filed', () => {
    expect(approvalView([], rule, viewer)).toMatchObject({
      show: true,
      latest: null,
      canRequest: true
    });
  });

  it('never offers a request to someone who may not write to the ticket', () => {
    expect(approvalView([], rule, { canReply: false, isOpen: true }).canRequest).toBe(false);
  });

  it('never offers a request on a closed ticket', () => {
    expect(approvalView([], rule, { canReply: true, isOpen: false }).canRequest).toBe(false);
  });

  it('never offers a request with no rule to bind it to', () => {
    expect(approvalView([row({ state: 'rejected' })], null, viewer)).toMatchObject({
      show: true,
      canRequest: false
    });
  });

  it('does not offer a second request while one is pending for the rule', () => {
    expect(approvalView([row()], rule, viewer).canRequest).toBe(false);
  });

  it('does not offer a request once the rule is approved', () => {
    expect(approvalView([row({ state: 'approved' })], rule, viewer).canRequest).toBe(false);
  });

  it('offers it again after a rejection or a withdrawal', () => {
    expect(approvalView([row({ state: 'rejected' })], rule, viewer).canRequest).toBe(true);
    expect(approvalView([row({ state: 'cancelled' })], rule, viewer).canRequest).toBe(true);
  });

  it('counts only requests under the rule that gates the ticket today', () => {
    const old = row({ state: 'approved', rule: { id: 'r0', name: 'Old rule' } });
    expect(approvalView([old], rule, viewer).canRequest).toBe(true);
  });

  it('offers nothing when the requests could not be loaded', () => {
    expect(approvalView(null, rule, viewer)).toMatchObject({
      show: true,
      failed: true,
      canRequest: false,
      canDecide: false,
      canWithdraw: false
    });
  });

  it('offers a decision only where the server said can_act', () => {
    expect(approvalView([row({ can_act: true })], rule, viewer).canDecide).toBe(true);
    expect(approvalView([row({ can_act: false })], rule, viewer).canDecide).toBe(false);
    expect(approvalView([row({ can_act: true, state: 'approved' })], rule, viewer).canDecide).toBe(
      false
    );
  });

  it('offers a withdrawal only where the server said can_cancel', () => {
    // The requester, or an admin on someone else's request.
    expect(approvalView([row({ can_cancel: true })], rule, viewer).canWithdraw).toBe(true);
    // Filing it is not the test: the flag is.
    expect(
      approvalView([row({ is_own_request: true, can_cancel: false })], rule, viewer).canWithdraw
    ).toBe(false);
    expect(approvalView([row({ can_cancel: false })], rule, viewer).canWithdraw).toBe(false);
    expect(
      approvalView([row({ can_cancel: true, state: 'rejected' })], rule, viewer).canWithdraw
    ).toBe(false);
  });

  it('acts only on the newest request', () => {
    const view = approvalView(
      [row({ id: 'new', state: 'rejected' }), row({ id: 'old', can_act: true })],
      rule,
      viewer
    );
    expect(view.latest.id).toBe('new');
    expect(view.canDecide).toBe(false);
  });
});
