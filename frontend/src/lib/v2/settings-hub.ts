/**
 * The settings hub's rows. Browser-safe, so the derivation can be tested.
 *
 * A switched-off module's rows are left out, and every value read from a
 * skipped call is read null-safely: the numbers and the module list come from
 * one read, but a row must still never throw on a missing total.
 */
import { isOrgAdmin } from '#lib/admin.js';
import { hubValue, showAutomationSetting } from '#lib/v2/automation.js';
import { BILLING_HREF } from '#lib/v2/billing.js';
import { CONNECTED_APPS_HREF } from '#lib/v2/connected-apps.js';
import { showObjectsSetting } from '#lib/v2/custom-objects.js';
import { isOn, productsOn, wordsOn, type ModuleRow } from '#lib/v2/modules.js';
import type { Billing } from '#lib/server/v2/billing.js';
import type { ObjectList } from '#lib/server/v2/custom-objects.js';
import type { getSettingsHub } from '#lib/server/v2/settings.js';

/** The hub load (`getSettingsHub`) plus the shell's facts. */
type HubData = Awaited<ReturnType<typeof getSettingsHub>> & {
  modules: ModuleRow[] | null;
  customObjects: ObjectList | null;
  billing: Billing | null;
  is_organization_admin?: boolean;
};

/** @param data the hub load plus the shell's admin fact */
export function hubGroups(data: HubData): {
  label: string;
  items: { href: string; title: string; body: string; value: string | null; warn: boolean }[];
}[] {
  const org = data.org;
  const ticketsOn = isOn(data.modules, 'tickets');
  const switchedOff = Array.isArray(data.modules)
    ? data.modules.filter((m) => !m.enabled).length
    : null;

  /**
   * Weekday hours as one line. When the days do not all match it says so
   * rather than printing the first day's hours; "09:00-17:00" beside a
   * calendar where four days run to 17:30 is a summary that is simply wrong,
   * and this is the number an SLA is measured against.
   */
  const hoursSummary = (() => {
    if (!data.calendar) return '';
    const open: { open: unknown; close: unknown }[] = data.calendar.days.filter(
      (d: { open: unknown }) => d.open
    );
    if (!open.length) return 'No open hours set';
    const first = open[0];
    const uniform = open.every((d) => d.open === first.open && d.close === first.close);
    return uniform
      ? `${open.length} days, ${first.open}-${first.close}`
      : `${open.length} days, hours vary`;
  })();

  const formMakes = wordsOn(
    data.modules,
    [
      ['leads', 'a lead'],
      ['tickets', 'a ticket']
    ],
    'or'
  );

  /** An approval rule set to MANAGER with no named approvers matches nobody. */
  const stuckApprovalRules = (data.approvalRules ?? []).filter(
    (r) => r.is_active && r.approver_role === 'MANAGER' && !r.approvers.length
  ).length;

  // Webhooks are admins' to see; a member's row says only what the page holds.
  const intakes = data.leadIntakes;
  const leadCaptureValue = intakes
    ? intakes.count
      ? `${intakes.active} of ${intakes.count} webhooks on`
      : 'No webhooks yet'
    : null;

  // Filed with the ticket rows, or with the lead rows while Tickets is off
  // and the addresses can only make leads.
  const leadAddresses = data.mailboxLeads === true && isOn(data.modules, 'leads');
  const inboundEmail = {
    href: '/settings/inbound-email',
    title: 'Inbound email',
    body: !ticketsOn
      ? 'The addresses that turn email into leads.'
      : leadAddresses
        ? 'The addresses that turn email into tickets or leads.'
        : 'The addresses that turn email into tickets.',
    value: data.mailboxTotals
      ? `${data.mailboxTotals.active} of ${data.mailboxTotals.count} active`
      : null,
    // Off AND still receiving, not merely off. An address switched off and
    // left alone is a decision; one still getting mail and creating nothing
    // is a customer being ignored.
    warn: (data.mailboxTotals?.silently_dropping ?? 0) > 0
  };

  return [
    {
      label: 'People and access',
      items: [
        {
          href: '/team',
          title: 'Team and access',
          body: 'Who can sign in, and what their role lets them do.',
          // People counts are admin-only oversight; a member's fan-out gets no
          // totals (the endpoint 403s), so the row lists the destination with
          // no value rather than a misleading zero.
          value: data.peopleTotals
            ? `${data.peopleTotals.count} people · ${data.peopleTotals.admins} admins`
            : null,
          warn: data.peopleTotals ? data.peopleTotals.tokens_on_deactivated > 0 : false
        },
        {
          href: '/settings/api-tokens',
          title: 'API tokens',
          body: 'Personal access tokens for scripts, integrations and AI agents.',
          value: data.tokenTotals ? `${data.tokenTotals.live} live` : null,
          warn: data.tokenTotals
            ? data.tokenTotals.orphaned > 0 || data.tokenTotals.unused_90d > 0
            : false
        },
        // Listed for admins only: every webhooks route answers a member 403,
        // reads included, so a member would open it only to be turned away.
        // `isOrgAdmin` is a display hint; the API is the gate.
        ...(isOrgAdmin(data)
          ? [
              {
                href: '/settings/webhooks',
                title: 'Webhooks',
                body: 'Send record changes to Zapier, n8n, Slack or your own code.',
                value: null,
                warn: false
              },
              // Connected apps exist where custom objects do (enterprise), so the
              // shell's objects answer is the probe; the page answers 404 on its own.
              ...(data.customObjects !== null
                ? [
                    {
                      href: CONNECTED_APPS_HREF,
                      title: 'Connected apps',
                      body: 'Apps that read and write this organization, each with its own key.',
                      value: null,
                      warn: false
                    }
                  ]
                : []),
              // Admin-only on the server for the same reason as webhooks.
              {
                href: '/settings/audit-log',
                title: 'Audit log',
                body: 'Sign-ins, API tokens, calendar feeds, merges, refused requests and paused webhooks.',
                value: null,
                warn: false
              }
            ]
          : []),
        {
          // Filed with access rather than with the ticket channels. What a
          // published web form grants is the ability for a stranger with no
          // account to write into this org, which is an access question; what
          // it arrives as is the smaller half.
          href: '/settings/web-forms',
          title: 'Web forms',
          body: formMakes
            ? `Forms you embed on your own site. What people fill in becomes ${formMakes}.`
            : 'Forms you embed on your own site.',
          value: `${data.webFormTotals.published} published`,
          // Forms are live and nothing has arrived in a month. Usually the
          // snippet was taken off the site it was pasted onto, which nothing
          // else would ever tell you. The destination names the individual
          // forms; this only says that at least one is silent.
          warn: data.webFormTotals.published > 0 && data.webFormTotals.submissions_30d === 0
        },
        ...(isOn(data.modules, 'knowledge_base') && data.helpCenter
          ? [
              {
                // Filed beside web forms for the same reason: it is what a stranger
                // with no account can reach. Readable by every member, so the value
                // is always computable.
                href: '/settings/help-center',
                title: 'Help center',
                body: 'Your published articles, public for customers and search engines.',
                value:
                  data.helpCenter.help_center_enabled && data.helpCenter.help_center_slug
                    ? `/help-center/${data.helpCenter.help_center_slug}`
                    : 'Off',
                warn: false
              }
            ]
          : []),
        {
          href: '/settings/organization',
          title: 'Organization',
          body: isOn(data.modules, 'invoices')
            ? 'The company details printed on every invoice and estimate.'
            : 'The company details, and the defaults new records start from.',
          value: org.company_name,
          warn: false
        },
        ...(isOrgAdmin(data)
          ? [
              {
                href: '/settings/apps',
                title: 'Apps',
                body: 'Industry apps to install, and the parts of BottleCRM this organization uses.',
                value:
                  switchedOff === null
                    ? null
                    : switchedOff
                      ? `${switchedOff} ${switchedOff === 1 ? 'module' : 'modules'} off`
                      : 'All modules on',
                warn: false
              }
            ]
          : []),
        // Only where the server offers billing; a member's answer is the plan name.
        ...(data.billing
          ? [
              {
                href: BILLING_HREF,
                title: 'Billing',
                body: isOrgAdmin(data)
                  ? "This organization's plan, and how it is paid for."
                  : "This organization's plan.",
                value: data.billing.plan.name,
                warn: Boolean(data.billing.notice)
              }
            ]
          : [])
      ]
    },
    ...(isOn(data.modules, 'leads')
      ? [
          {
            label: 'How leads arrive',
            items: [
              {
                href: '/settings/lead-capture',
                title: 'Lead capture',
                body: 'Webhooks, web forms and lead addresses: every way a lead gets in.',
                value: leadCaptureValue,
                warn: false
              },
              ...(!ticketsOn && leadAddresses ? [inboundEmail] : [])
            ]
          }
        ]
      : []),
    ...(ticketsOn
      ? [
          {
            label: 'How tickets are handled',
            items: [
              {
                href: '/settings/routing',
                title: 'Ticket routing',
                body: 'Who a new ticket lands on, in the order the rules are tried.',
                value: data.routingTotals ? `${data.routingTotals.active} rules` : null,
                warn: (data.routingTotals?.unrouted_last_30d ?? 0) > 0
              },
              {
                href: '/settings/escalation',
                title: 'Escalation',
                body: 'What happens when a ticket misses its response target.',
                value: data.escalationTotals
                  ? `${data.escalationTotals.active} of ${data.escalationTotals.count} priorities`
                  : null,
                warn: (data.escalationTotals?.breaches_unhandled_30d ?? 0) > 0
              },
              {
                href: '/settings/business-hours',
                title: 'Business hours',
                body: 'The clock every response target is measured against.',
                value: data.calendar ? `${data.calendar.name} · ${hoursSummary}` : null,
                warn: false
              },
              {
                href: '/settings/ticket-approvals',
                title: 'Approval rules',
                body: 'What gates a ticket close, and who can clear it.',
                value: data.approvalTotals ? `${data.approvalTotals.active} active` : null,
                warn: stuckApprovalRules > 0
              },
              {
                href: '/settings/reopen',
                title: 'Reopen policy',
                body: 'Whether a customer reply brings a closed ticket back.',
                // Admin-only, like people and tokens above: a member's fan-out gets
                // null (the endpoint 403s), so the row lists the destination without
                // a value rather than guessing at the policy.
                value: !data.reopen
                  ? null
                  : data.reopen.is_enabled
                    ? `Within ${data.reopen.reopen_window_days} days`
                    : 'Off. Closed stays closed',
                // Replies arriving outside the window are normal for any window, so
                // that number belongs on the page, not on a warning here. Off is the
                // state worth flagging: it makes every reply to a closed ticket
                // vanish, not just the late ones.
                warn: data.reopen ? !data.reopen.is_enabled : false
              },
              inboundEmail
            ]
          }
        ]
      : []),
    {
      label: 'Shared words and fields',
      items: [
        ...(ticketsOn
          ? [
              {
                href: '/settings/macros',
                title: 'Macros',
                body: 'Canned replies, and the placeholders they substitute.',
                value: data.macroTotals ? `${data.macroTotals.org} shared` : null,
                warn: (data.macroTotals?.with_unknown_placeholders ?? 0) > 0
              }
            ]
          : []),
        {
          href: '/settings/tags',
          title: 'Tags',
          body: `Labels shared across ${wordsOn(data.modules, [
            [null, 'accounts'],
            ['leads', 'leads'],
            ['deals', 'deals'],
            ['tickets', 'tickets']
          ])}.`,
          value: `${data.tagTotals.active} in use`,
          // Unused tags are housekeeping, not a fault, the tags page lists
          // them without needing the hub to raise an alarm about tidiness.
          warn: false
        },
        ...(isOn(data.modules, 'leads')
          ? [
              {
                href: '/settings/lead-pipelines',
                title: 'Lead pipelines',
                body: 'The stages leads move through on the board.',
                value: null,
                warn: false
              }
            ]
          : []),
        // Not tied to Leads: deals pick from the same list. Hidden only where
        // the API has no list (false); a failed read still lists the
        // destination, without a number.
        ...(data.leadSources !== false
          ? [
              {
                href: '/settings/lead-sources',
                title: 'Lead sources',
                body: 'Where leads come from: the list every source picker offers.',
                value: data.leadSources ? `${data.leadSources.active} in use` : null,
                warn: false
              }
            ]
          : []),
        ...(isOn(data.modules, 'deals')
          ? [
              {
                href: '/settings/deal-pipelines',
                title: 'Deal pipelines',
                body: 'The stages deals move through, and when a deal starts rotting in one.',
                value: null,
                warn: false
              }
            ]
          : []),
        {
          href: '/settings/custom-fields',
          title: 'Custom fields',
          body: 'Fields this organisation added to records.',
          value: `${data.fieldTotals.active} across ${data.fieldTotals.models_extended} record types`,
          warn: data.fieldTotals.required_with_gaps > 0
        },
        ...(showObjectsSetting(data.customObjects, isOrgAdmin(data))
          ? [
              {
                href: '/settings/objects',
                title: 'Objects',
                body: 'Record types this organisation defined.',
                value: `${data.customObjects?.objects.length ?? 0} active`,
                warn: false
              }
            ]
          : []),
        ...(showAutomationSetting(data.automation, isOrgAdmin(data))
          ? [
              {
                href: '/settings/automation',
                title: 'Automation',
                body: 'Rules that act when records are created or change',
                ...hubValue(data.automation)
              }
            ]
          : []),
        ...(isOn(data.modules, 'invoices')
          ? [
              {
                href: '/invoices/templates',
                title: 'Invoice templates',
                body: 'How an invoice looks when a customer receives it.',
                value: 'Under Invoices',
                warn: false
              }
            ]
          : []),
        ...(productsOn(data.modules)
          ? [
              {
                href: '/invoices/products',
                title: 'Products',
                body: 'The products and prices invoices and estimates use.',
                value: null,
                warn: false
              }
            ]
          : [])
      ]
    }
  ];
}
