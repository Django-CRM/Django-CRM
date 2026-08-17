/**
 * Settings hub: the wiring behind `/settings`.
 *
 * Server-only. The hub summarises every settings destination, so it needs a
 * number from each. Rather than a bespoke `/settings/summary/` endpoint, which
 * would have to reproduce, in Python across eight apps, the per-destination
 * rollups the pages already derive (escalation's "breaches going nowhere",
 * inbound's "silently dropping"). This fans out to the same server layers each
 * sub-page uses and assembles their totals. The calls run concurrently, so the
 * hub costs one round of parallel requests, not a dozen sequential ones.
 *
 * People, API tokens and the reopen policy are admin-only: their endpoints 403
 * a member. `listTeam` / `listOrgTokens` already fold that into a `forbidden`
 * sentinel; the reopen fetch throws, so it is caught below. Either way the hub
 * still lists those destinations for a member. It just omits the value and any
 * warning it cannot compute, the same way the shell omits the team badge a
 * member cannot count. Every other destination is readable by any member, so a
 * throw from one of those is a real failure and is left to propagate.
 */
import { getBusinessHours } from './business-hours.js';
import { getCustomFields } from './custom-fields.js';
import { getEscalationPolicies } from './escalation.js';
import { getMailboxes } from './inbound-email.js';
import { getMacros } from './macros.js';
import { getOrgSettings } from './organization.js';
import { getReopenPolicy } from './reopen.js';
import { getRoutingRules } from './routing.js';
import { getTags } from './tags.js';
import { listTeam } from './team.js';
import { getApprovalRules } from './ticket-approvals.js';
import { listOrgTokens } from './tokens.js';
import { getWebForms } from './web-forms.js';

const THIRTY_DAYS_MS = 30 * 24 * 60 * 60 * 1000;

/**
 * Escalation "breaches that went nowhere", counted exactly as the escalation
 * page's `breachesGoingNowhere` does, so the hub's warning matches the page it
 * links to. A half is dead when the policy is off, reassigns to nobody, or
 * notifies nobody (no person and no team).
 * @param {any[]} policies
 */
function escalationRollup(policies) {
  let active = 0;
  let breaches = 0;
  for (const p of policies) {
    if (p.is_active) active += 1;
    /** @param {string} action @param {any} target */
    const halfDead = (action, target) => {
      if (!p.is_active) return true;
      if (action === 'reassign' && !target) return true;
      if (action === 'notify' && !target && !p.notify_team) return true;
      return false;
    };
    if (halfDead(p.first_response_action, p.first_response_target)) {
      breaches += p.breaches_last_30d?.first_response ?? 0;
    }
    if (halfDead(p.resolution_action, p.resolution_target)) {
      breaches += p.breaches_last_30d?.resolution ?? 0;
    }
  }
  return { active, count: policies.length, breaches_unhandled_30d: breaches };
}

/**
 * Mailboxes switched off that are still receiving mail, a customer ignored,
 * as opposed to an address deliberately retired. Matches the inbound page's
 * reasoning about which "off" is worth a warning.
 * @param {any[]} mailboxes @param {number} now
 */
function silentlyDropping(mailboxes, now) {
  return mailboxes.filter(
    (m) =>
      !m.is_active &&
      m.last_received_at &&
      now - new Date(m.last_received_at).getTime() < THIRTY_DAYS_MS
  ).length;
}

/**
 * Reopen policy → its `policy`, or null when the caller is a member (the
 * endpoint is admin-only and throws 403). Any other error still propagates.
 * @param {Promise<any>} promise
 */
async function reopenOrNull(promise) {
  try {
    return (await promise).policy;
  } catch (/** @type {any} */ err) {
    if (err?.status === 403) return null;
    throw err;
  }
}

/**
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function getSettingsHub(event) {
  const [
    org,
    calendar,
    tokens,
    team,
    routing,
    escalation,
    mailbox,
    reopenPolicy,
    approval,
    macro,
    tag,
    field,
    webForm
  ] = await Promise.all([
    getOrgSettings(event),
    getBusinessHours(event),
    listOrgTokens(event),
    listTeam(event),
    getRoutingRules(event),
    getEscalationPolicies(event),
    getMailboxes(event),
    reopenOrNull(getReopenPolicy(event)),
    getApprovalRules(event),
    getMacros(event),
    getTags(event),
    getCustomFields(event),
    getWebForms(event)
  ]);

  const now = Date.now();
  return {
    org: org.org,
    calendar: calendar.calendar,
    // Admin-only oversight: null for a member (endpoint 403'd), so the hub
    // shows the destination without a value it is not allowed to compute.
    tokenTotals: tokens.forbidden ? null : tokens.totals,
    peopleTotals: team.forbidden ? null : team.totals,
    routingTotals: routing.totals,
    escalationTotals: escalationRollup(escalation.policies),
    mailboxTotals: {
      ...mailbox.totals,
      silently_dropping: silentlyDropping(mailbox.mailboxes, now)
    },
    reopen: reopenPolicy,
    approvalRules: approval.rules,
    approvalTotals: approval.totals,
    macroTotals: macro.totals,
    tagTotals: tag.totals,
    fieldTotals: field.totals,
    webFormTotals: webForm.totals
  };
}
