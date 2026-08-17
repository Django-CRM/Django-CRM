<script>
  import { resolve } from '$app/paths';
  import { asInternalPath } from '$lib/utils/paths.js';
  /**
   * The settings hub.
   *
   * v1 has thirteen settings routes reachable only from a dropdown, so nobody
   * could tell what was configurable without opening each one. This lists them
   * with the current value beside each. A settings index that does not tell
   * you the current state is a table of contents, not a screen.
   *
   * Grouped by what a setting decides, not by which Django app owns it:
   * "who a ticket lands on" and "what closes it" belong together whether or
   * not they live in the same models file.
   *
   * `warn` is the second reason this page exists. Each destination reports
   * whether something there needs attention, so the hub is worth opening even
   * when you did not come to change anything. A warning here always has a
   * matching explanation on the page it points to, never a badge that leads
   * to a screen with nothing on it.
   *
   * Destinations v2 has not built are listed anyway and link to v1, marked as
   * such. An index that quietly omits settings is worse than one that admits
   * where they live: people go looking, find nothing, and conclude the feature
   * does not exist.
   */
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import { count, shortDate } from '$lib/v2/format.js';
  import { ChevronRight, ShieldAlert } from '@lucide/svelte';

  /** @type {{ data: any }} */
  let { data } = $props();

  let org = $derived(data.org);

  /**
   * Weekday hours as one line. When the days do not all match it says so
   * rather than printing the first day's hours; "09:00-17:00" beside a
   * calendar where four days run to 17:30 is a summary that is simply wrong,
   * and this is the number an SLA is measured against.
   */
  let hoursSummary = $derived.by(() => {
    const open = data.calendar.days.filter((d) => d.open);
    if (!open.length) return 'No open hours set';
    const first = open[0];
    const uniform = open.every((d) => d.open === first.open && d.close === first.close);
    return uniform
      ? `${open.length} days, ${first.open}-${first.close}`
      : `${open.length} days, hours vary`;
  });

  /** An approval rule set to MANAGER with no named approvers matches nobody. */
  let stuckApprovalRules = $derived(
    data.approvalRules.filter(
      (r) => r.is_active && r.approver_role === 'MANAGER' && !r.approvers.length
    ).length
  );

  let groups = $derived([
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
        {
          // Filed with access rather than with the ticket channels, though a
          // form makes leads and not tickets. What a published web form grants
          // is the ability for a stranger with no account to write into this
          // org, which is an access question; that it happens to arrive as a
          // lead is the smaller half.
          href: '/settings/web-forms',
          title: 'Web forms',
          body: 'Forms you embed on your own site. What people fill in becomes a lead.',
          value: `${data.webFormTotals.published} published`,
          // Forms are live and nothing has arrived in a month. Usually the
          // snippet was taken off the site it was pasted onto, which nothing
          // else would ever tell you. The destination names the individual
          // forms; this only says that at least one is silent.
          warn: data.webFormTotals.published > 0 && data.webFormTotals.submissions_30d === 0
        },
        {
          href: '/settings/organization',
          title: 'Organization',
          body: 'The company details printed on every invoice and estimate.',
          value: org.company_name,
          warn: false
        }
      ]
    },
    {
      label: 'How tickets are handled',
      items: [
        {
          href: '/settings/routing',
          title: 'Ticket routing',
          body: 'Who a new ticket lands on, in the order the rules are tried.',
          value: `${data.routingTotals.active} rules`,
          warn: data.routingTotals.unrouted_last_30d > 0
        },
        {
          href: '/settings/escalation',
          title: 'Escalation',
          body: 'What happens when a ticket misses its response target.',
          value: `${data.escalationTotals.active} of ${data.escalationTotals.count} priorities`,
          warn: data.escalationTotals.breaches_unhandled_30d > 0
        },
        {
          href: '/settings/business-hours',
          title: 'Business hours',
          body: 'The clock every response target is measured against.',
          value: `${data.calendar.name} · ${hoursSummary}`,
          warn: false
        },
        {
          href: '/settings/ticket-approvals',
          title: 'Approval rules',
          body: 'What gates a ticket close, and who can clear it.',
          value: `${data.approvalTotals.active} active`,
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
        {
          href: '/settings/inbound-email',
          title: 'Inbound email',
          body: 'The addresses that turn email into tickets.',
          value: `${data.mailboxTotals.active} of ${data.mailboxTotals.count} active`,
          // Off AND still receiving, not merely off. An address switched off
          // and left alone is a decision; one still getting mail and creating
          // nothing is a customer being ignored.
          warn: data.mailboxTotals.silently_dropping > 0
        }
      ]
    },
    {
      label: 'Shared words and fields',
      items: [
        {
          href: '/settings/macros',
          title: 'Macros',
          body: 'Canned replies, and the placeholders they substitute.',
          value: `${data.macroTotals.org} shared`,
          warn: data.macroTotals.with_unknown_placeholders > 0
        },
        {
          href: '/settings/tags',
          title: 'Tags',
          body: 'Labels shared across accounts, leads, deals and tickets.',
          value: `${data.tagTotals.active} in use`,
          // Unused tags are housekeeping, not a fault, the tags page lists
          // them without needing the hub to raise an alarm about tidiness.
          warn: false
        },
        {
          href: '/settings/custom-fields',
          title: 'Custom fields',
          body: 'Fields this organisation added to records.',
          value: `${data.fieldTotals.active} across ${data.fieldTotals.models_extended} record types`,
          warn: data.fieldTotals.required_with_gaps > 0
        },
        {
          href: '/invoices/templates',
          title: 'Invoice templates',
          body: 'How an invoice looks when a customer receives it.',
          value: 'Under Invoices',
          warn: false
        }
      ]
    }
  ]);

  let warnings = $derived(groups.flatMap((g) => g.items).filter((i) => i.warn).length);
</script>

<PageHeader title="Settings">
  {#snippet sub()}
    {org.name} · <span class="v2-num">{count(org.member_count)}</span> members · since
    {shortDate(org.created_at)}
    {#if warnings}
      · <span class="v2-num">{count(warnings)}</span> need a look
    {/if}
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:18px;padding-bottom:32px">
    {#each groups as g (g.label)}
      <div class="v2-label" style="margin-bottom:10px">{g.label}</div>
      <div class="v2-card" style="overflow:hidden;margin-bottom:22px">
        {#each g.items as s (s.href)}
          <a class="v2-setting" href={resolve(asInternalPath(s.href))}>
            <div class="v2-setting-body">
              <b>{s.title}</b>
              <span class="v2-sub" style="font-size:11.5px">{s.body}</span>
            </div>
            {#if s.warn}
              <ShieldAlert size={15} style="color:var(--v2-clay);flex:none" />
            {/if}
            {#if s.value}
              <span class="v2-sub v2-setting-value">{s.value}</span>
            {/if}
            <ChevronRight size={15} style="color:var(--v2-slate);flex:none" />
          </a>
        {/each}
      </div>
    {/each}

    <!--
      The org API key is deliberately absent from this page. It is a
      credential, it was once exposed through nested serializers, and a
      settings screen that renders it is how the next leak happens. Rotating
      or revealing it belongs behind an explicit, audited action, not on an
      index anyone with the URL can load.
    -->
    <p class="v2-sub" style="font-size:11.5px;margin-top:18px;max-width:64ch">
      The organisation API key is not shown here. Credentials are never rendered on a page you can
      arrive at by browsing. See
      <a href={resolve('/settings/api-tokens')} style="color:inherit">API tokens</a> for how token values
      are handled.
    </p>
  </div>
</div>

<style>
  .v2-setting-value {
    font-size: 12px;
    text-align: right;
  }

  /* At 414px the value column squeezes the title to a couple of words per
     line. Drop it. The destination and what it does are what you navigate
     by, and every value is repeated on the page it points to. */
  @media (max-width: 640px) {
    .v2-setting-value {
      display: none;
    }
  }
</style>
