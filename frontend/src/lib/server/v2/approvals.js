/**
 * Case approvals: the wiring behind /v2/tickets/approvals.
 *
 * Server-only. The inbox (`GET /cases/approvals/?state=all`) and the rule list
 * (`GET /cases/approval-rules/`) are fanned into the single shape the queue
 * reads, and the three decisions (approve / reject / cancel) POST to their
 * endpoints. The inbox has no totals envelope, so the header numbers are
 * computed here from the rows, same approach as recurring invoices.
 *
 * The action guard `can_act` and `is_own_request` come straight from the
 * serializer (the server knows the rule pool and the viewer's identity); the
 * page never recomputes them, because guessing wrong means a button that 403s.
 * Since the self-approval guard landed, `can_act` is already false on a row you
 * filed yourself, so the queue's Approve/Reject offer and the endpoint agree.
 */
import { apiRequest } from '$lib/api-helpers.js';

/** A `{ id, email }` profile object (or null) → a display string. */
const label = (/** @type {any} */ p) => (p && typeof p === 'object' ? p.email || '' : p || '');

function toRow(/** @type {any} */ a) {
  const cs = a.case_summary || {};
  const rs = a.rule_summary || {};
  return {
    id: a.id,
    state: a.state,
    note: a.note || '',
    reason: a.reason || '',
    created_at: a.created_at,
    decided_at: a.decided_at,
    can_act: !!a.can_act,
    // The cancel endpoint's own rule (requester or org admin, while pending).
    can_cancel: a.can_cancel === true,
    is_own_request: !!a.is_own_request,
    requested_by: label(a.requested_by),
    approver: label(a.approver),
    case: {
      id: cs.id,
      name: cs.name,
      priority: cs.priority,
      account: cs.account ? { id: cs.account.id, name: cs.account.name } : null
    },
    rule: {
      id: rs.id,
      name: rs.name,
      approver_role: rs.approver_role || '',
      approvers: (rs.approvers || []).map(label)
    }
  };
}

function toRuleRow(/** @type {any} */ r, /** @type {Record<string, number>} */ pendingByRule) {
  return {
    id: r.id,
    name: r.name,
    match_priority: r.match_priority,
    match_case_type: r.match_case_type,
    match_team: r.match_team ? { id: r.match_team.id, name: r.match_team.name } : null,
    approver_role: r.approver_role || '',
    approvers: (r.approvers || []).map(label),
    is_active: r.is_active,
    pending_count: pendingByRule[r.id] || 0
  };
}

function computeTotals(/** @type {any[]} */ rows) {
  const now = Date.now();
  const pending = rows.filter((r) => r.state === 'pending');
  const oldest = pending.reduce((max, r) => {
    const hrs = (now - Date.parse(r.created_at)) / 3_600_000;
    return Number.isFinite(hrs) && hrs > max ? hrs : max;
  }, 0);
  const weekAgo = now - 7 * 86_400_000;
  return {
    // What is waiting on *this viewer* specifically. The only figure a rep can
    // act on. `can_act` already excludes rows they filed themselves.
    awaiting_you: rows.filter((r) => r.can_act).length,
    pending: pending.length,
    oldest_pending_hours: Math.round(oldest),
    decided_this_week: rows.filter(
      (r) => r.state !== 'pending' && r.decided_at && Date.parse(r.decided_at) >= weekAgo
    ).length
  };
}

/**
 * The approvals queue, shaped for the page: `{ approvals, totals, rules }`.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function listApprovals({ cookies }) {
  const [inbox, rulesResp] = await Promise.all([
    apiRequest('/cases/approvals/?state=all', {}, { cookies }),
    apiRequest('/cases/approval-rules/', {}, { cookies })
  ]);

  const raw = inbox.approvals || [];
  const rows = raw.map(toRow);

  /** @type {Record<string, number>} */
  const pendingByRule = {};
  for (const a of raw) {
    if (a.state === 'pending' && a.rule_summary?.id) {
      pendingByRule[a.rule_summary.id] = (pendingByRule[a.rule_summary.id] || 0) + 1;
    }
  }
  const rules = (rulesResp.rules || []).map((/** @type {any} */ r) => toRuleRow(r, pendingByRule));

  return { approvals: rows, totals: computeTotals(rows), rules };
}

/**
 * Count of pending approvals the viewer can act on, the nav badge. Lighter
 * than {@link listApprovals}: `mine=true` already scopes to the actionable set.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function countAwaitingApprovals({ cookies }) {
  const resp = await apiRequest('/cases/approvals/?mine=true&state=pending', {}, { cookies });
  return (resp.approvals || []).length;
}

/**
 * Every approval filed on one ticket, newest first, in the queue's row shape.
 * The inbox endpoint's own visibility rule decides what comes back, and the
 * viewer-relative `can_act` / `is_own_request` ride on each row.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} caseId
 */
export async function listTicketApprovals({ cookies }, caseId) {
  const query = new URLSearchParams({ case: caseId, state: 'all' });
  const inbox = await apiRequest(`/cases/approvals/?${query}`, {}, { cookies });
  return (inbox.approvals || []).map(toRow);
}

/**
 * File an approval request on a ticket. No `rule_id`: the API binds it to the
 * rule that gates closing this ticket, the one the detail names as
 * `approval_rule`.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} caseId
 * @param {string} note
 */
export async function requestApproval({ cookies }, caseId, note) {
  return apiRequest(
    `/cases/${caseId}/request-approval/`,
    { method: 'POST', body: note ? { note } : {} },
    { cookies }
  );
}

/** @param {{ cookies: import('@sveltejs/kit').Cookies }} event */
export async function approveApproval(
  { cookies },
  /** @type {string} */ id,
  /** @type {string} */ note
) {
  return apiRequest(
    `/cases/approvals/${id}/approve/`,
    { method: 'POST', body: note ? { note } : {} },
    { cookies }
  );
}

/** @param {{ cookies: import('@sveltejs/kit').Cookies }} event */
export async function rejectApproval(
  { cookies },
  /** @type {string} */ id,
  /** @type {string} */ reason
) {
  return apiRequest(
    `/cases/approvals/${id}/reject/`,
    { method: 'POST', body: { reason } },
    { cookies }
  );
}

/** @param {{ cookies: import('@sveltejs/kit').Cookies }} event */
export async function cancelApproval({ cookies }, /** @type {string} */ id) {
  return apiRequest(`/cases/approvals/${id}/cancel/`, { method: 'POST', body: {} }, { cookies });
}
