/**
 * What the ticket page's approval panel shows and offers.
 *
 * Every fact here is the server's: `rule` is the detail's `approval_rule` (the
 * rule that gates closing this ticket, the one a request binds to), `canReply`
 * is `comment_permission` (the write rule `request-approval/` takes), and each
 * approval row carries its own `can_act` and `can_cancel`. Nothing is
 * worked out from the viewer's role.
 *
 * "Request approval" is offered only where the API would take it: a rule
 * gates the ticket, the viewer may write to it, it is still open, and that
 * rule has neither a pending request (the API answers 409) nor an approved one
 * (the close is already allowed). A request on an older rule does not count,
 * because the close gate asks about the rule that matches today.
 *
 * `approvals` is null when the list could not be loaded. The panel then says
 * so and offers nothing, since it cannot tell whether a request is pending.
 *
 * @param {any[] | null} approvals newest first, as the API orders them
 * @param {{ id: string, name: string } | null} rule
 * @param {{ canReply: boolean, isOpen: boolean }} viewer
 */
export function approvalView(approvals, rule, { canReply, isOpen }) {
  const rows = approvals ?? [];
  const latest = rows[0] ?? null;
  const forRule = rule ? rows.filter((a) => a.rule?.id === rule.id) : [];
  const settled = forRule.some((a) => a.state === 'pending' || a.state === 'approved');
  return {
    show: Boolean(rule) || rows.length > 0,
    failed: approvals === null,
    latest,
    canRequest: Boolean(rule) && approvals !== null && canReply && isOpen && !settled,
    // Only the newest row carries actions, as on mobile; older ones are history.
    canDecide: latest?.state === 'pending' && latest.can_act === true,
    // `can_cancel`: the requester or an org admin, the cancel endpoint's rule.
    canWithdraw: latest?.state === 'pending' && latest.can_cancel === true
  };
}
