/**
 * Approval to close, on the ticket page: the detail's `approval_rule`, the
 * per-ticket request list, and the four form actions. Authorization is the
 * API's; these check that the right endpoint is called with the right body and
 * that a refusal comes back as the API's own sentence in `approvalError`.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { getTicket } = await import('./tickets.js');
const { listTicketApprovals } = await import('./approvals.js');
const { actions } = await import('../../../routes/(app)/tickets/[id]/+page.server.js');

const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

/**
 * @param {string} action
 * @param {Record<string, string>} fields
 */
function actionEvent(action, fields) {
  const body = new FormData();
  for (const [key, value] of Object.entries(fields)) body.set(key, value);
  return /** @type {any} */ ({
    request: new Request(`http://test/tickets/a?/${action}`, { method: 'POST', body }),
    params: { id: 'a' },
    cookies: { get: () => 'token' }
  });
}

/** @param {number} status @param {string} sentence */
function apiError(status, sentence) {
  return Object.assign(new Error(`errors: ${sentence}`), {
    status,
    body: { error: true, errors: sentence }
  });
}

beforeEach(() => {
  apiRequest.mockReset();
});

describe('getTicket approvalRule', () => {
  const base = { cases_obj: { id: 'a', name: 'A', status: 'New', priority: 'Urgent' } };

  it('carries the gating rule when the API names one', async () => {
    apiRequest.mockResolvedValue({ ...base, approval_rule: { id: 'r1', name: 'Urgent close' } });
    expect((await getTicket(event, 'a')).approvalRule).toEqual({ id: 'r1', name: 'Urgent close' });
  });

  it('is null when no rule gates the ticket', async () => {
    apiRequest.mockResolvedValue({ ...base, approval_rule: null });
    expect((await getTicket(event, 'a')).approvalRule).toBeNull();
  });
});

describe('listTicketApprovals', () => {
  it('asks the inbox for every state of this ticket and keeps the server flags', async () => {
    apiRequest.mockResolvedValue({
      approvals: [
        {
          id: 'ap1',
          state: 'pending',
          can_act: true,
          can_cancel: true,
          is_own_request: false,
          requested_by: { id: 'p1', email: 'rep@example.com' },
          rule_summary: { id: 'r1', name: 'Urgent close', approvers: [] },
          case_summary: { id: 'a' }
        }
      ]
    });
    const rows = await listTicketApprovals(event, 'a');
    const q = new URLSearchParams(apiRequest.mock.calls[0][0].split('?')[1]);
    expect(apiRequest.mock.calls[0][0].startsWith('/cases/approvals/?')).toBe(true);
    expect(q.get('case')).toBe('a');
    expect(q.get('state')).toBe('all');
    // Absent means no: an older server without the flag offers no Withdraw.
    apiRequest.mockResolvedValueOnce({ approvals: [{ id: 'ap2', state: 'pending' }] });
    expect((await listTicketApprovals(event, 'a'))[0].can_cancel).toBe(false);
    expect(rows[0]).toMatchObject({
      id: 'ap1',
      can_act: true,
      can_cancel: true,
      is_own_request: false,
      requested_by: 'rep@example.com',
      rule: { id: 'r1', name: 'Urgent close' }
    });
  });
});

describe('approval actions', () => {
  it('requests approval on this ticket with the note', async () => {
    apiRequest.mockResolvedValue({});
    const out = await actions.requestApproval(actionEvent('requestApproval', { note: ' soon ' }));
    expect(apiRequest).toHaveBeenCalledWith(
      '/cases/a/request-approval/',
      { method: 'POST', body: { note: 'soon' } },
      expect.anything()
    );
    expect(out).toEqual({ approvalRequested: true });
  });

  it('shows the API’s refusal of a request as it wrote it', async () => {
    apiRequest.mockRejectedValue(apiError(409, 'An approval is already pending for this case.'));
    const out = /** @type {any} */ (
      await actions.requestApproval(actionEvent('requestApproval', {}))
    );
    expect(out.status).toBe(400);
    expect(out.data.approvalError).toBe('An approval is already pending for this case.');
  });

  it('approves the given request', async () => {
    apiRequest.mockResolvedValue({});
    await actions.approveApproval(actionEvent('approveApproval', { approval_id: 'ap1' }));
    expect(apiRequest.mock.calls[0][0]).toBe('/cases/approvals/ap1/approve/');
  });

  it('surfaces a refused approval', async () => {
    apiRequest.mockRejectedValue(apiError(403, 'You are not an approver for this rule.'));
    const out = /** @type {any} */ (
      await actions.approveApproval(actionEvent('approveApproval', { approval_id: 'ap1' }))
    );
    expect(out.data.approvalError).toBe('You are not an approver for this rule.');
  });

  it('will not reject without a reason, and never asks the API', async () => {
    const out = /** @type {any} */ (
      await actions.rejectApproval(
        actionEvent('rejectApproval', { approval_id: 'ap1', reason: ' ' })
      )
    );
    expect(out.data.approvalError).toBe('A rejection needs a reason.');
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('rejects with the reason', async () => {
    apiRequest.mockResolvedValue({});
    await actions.rejectApproval(
      actionEvent('rejectApproval', { approval_id: 'ap1', reason: 'Not yet' })
    );
    expect(apiRequest).toHaveBeenCalledWith(
      '/cases/approvals/ap1/reject/',
      { method: 'POST', body: { reason: 'Not yet' } },
      expect.anything()
    );
  });

  it('withdraws through the cancel endpoint', async () => {
    apiRequest.mockResolvedValue({});
    const out = await actions.withdrawApproval(
      actionEvent('withdrawApproval', { approval_id: 'ap1' })
    );
    expect(apiRequest.mock.calls[0][0]).toBe('/cases/approvals/ap1/cancel/');
    expect(out).toEqual({ approvalDecided: 'cancelled' });
  });

  it('refuses an action with no approval id before calling the API', async () => {
    for (const name of ['approveApproval', 'rejectApproval', 'withdrawApproval']) {
      const out = /** @type {any} */ (await actions[name](actionEvent(name, { reason: 'x' })));
      expect(out.data.approvalError).toBe('Which approval? None was given.');
    }
    expect(apiRequest).not.toHaveBeenCalled();
  });
});
