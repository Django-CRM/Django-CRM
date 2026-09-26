<script>
  import { resolve } from '$app/paths';
  /**
   * The account is a workspace, not a form.
   *
   * v1 rendered an account as ~28 stacked label/value rows, so answering
   * "what is going on with Northwind?" meant visiting four other pages. Here
   * the deals, people, tickets and invoices are on the page, and the next
   * action names the problem that spans them.
   *
   * All four panels come from the single detail response, the API already
   * returned them, so this costs no extra round trips.
   */
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import NextAction from '$lib/v2/components/NextAction.svelte';
  import StatCard from '$lib/v2/components/StatCard.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import Avatar from '$lib/v2/components/Avatar.svelte';
  import { money, moneyEach, shortDate, longDate } from '$lib/v2/format.js';
  import {
    CLOSED_KINDS,
    PRIORITY_TONE,
    INVOICE_STATUS_TONE,
    invoiceStatusLabel
  } from '$lib/v2/enums.js';
  import { ChevronRight, Mail, Phone } from '@lucide/svelte';

  /** @type {{ data: any }} */
  let { data } = $props();

  let { account, deals, contacts, tickets, invoices, owners } = $derived(data);

  let openDeals = $derived(
    deals.filter((/** @type {any} */ d) => !CLOSED_KINDS.includes(d.stage_kind))
  );
  let stalled = $derived(openDeals.filter((/** @type {any} */ d) => d.aging_status === 'red'));
  // `past_due` is decided by the same rule as the header figure. See
  // `isPastDue` in the data layer. The invoice's own `is_overdue` flag counts
  // drafts, and a rail that disagrees with its header discredits both.
  let pastDue = $derived(invoices.filter((/** @type {any} */ i) => i.past_due));

  /** One sentence that connects problems across objects. Every clause is a
      fact on this page: a stalled deal, or an invoice past its due date. */
  let headline = $derived(
    stalled.length && pastDue.length
      ? `${stalled[0].name} is stalled and ${pastDue[0].invoice_number} is past due, same account, two problems.`
      : stalled.length
        ? `${stalled[0].name} has not moved in ${stalled[0].days_in_current_stage} days.`
        : pastDue.length
          ? `${pastDue[0].invoice_number} is past due, ${money(pastDue[0].amount_due, pastDue[0].currency)}.`
          : null
  );
</script>

<PageHeader title={account.name} record>
  {#snippet crumb()}
    <a href={resolve('/accounts')}>Accounts</a>
    <ChevronRight size={12} />
    <span>{account.industry || 'No industry'}</span>
  {/snippet}
  {#snippet sub()}
    {[
      account.industry,
      account.number_of_employees ? `${account.number_of_employees} staff` : null,
      /* Derived: the close date of the first deal won here. There is no
         contract model, so this is what "customer since" can honestly mean. */
      account.first_won_on
        ? `customer since ${longDate(account.first_won_on)}`
        : 'No deals won yet',
      owners.length ? `owned by ${owners[0]}` : null
    ]
      .filter(Boolean)
      .join(' · ')}
  {/snippet}
  {#snippet actions()}
    <a class="v2-btn" href={resolve(`/accounts/${account.id}/edit`)}>Edit</a>
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-bottom:32px">
    <div class="v2-stats" style="margin-bottom:16px">
      <StatCard
        label="Revenue won"
        value={account.won_by_currency?.length ? moneyEach(account.won_by_currency) : '—'}
        tone={account.won_by_currency?.length ? 'moss' : 'slate'}
        detail={account.won_count
          ? `${account.won_count} deal${account.won_count === 1 ? '' : 's'} won`
          : 'Nothing won yet'}
      />
      <StatCard
        label="Open pipeline"
        value={account.pipeline_by_currency?.length ? moneyEach(account.pipeline_by_currency) : '—'}
        detail={account.open_deal_count
          ? `${account.open_deal_count} open deal${account.open_deal_count === 1 ? '' : 's'}`
          : 'No open deals'}
      />
      <StatCard
        label="Past due"
        value={account.overdue_by_currency?.length ? moneyEach(account.overdue_by_currency) : '—'}
        tone={account.overdue_by_currency?.length ? 'rust' : 'slate'}
        detail={pastDue.length
          ? pastDue.map((/** @type {any} */ i) => i.invoice_number).join(', ')
          : 'Nothing past due'}
      />
      <StatCard
        label="Open tickets"
        value={String(account.open_tickets ?? 0)}
        tone={tickets.some((/** @type {any} */ t) => t.priority === 'Urgent') ? 'rust' : 'slate'}
        detail={tickets.length ? `${tickets[0].name} · ${tickets[0].priority}` : 'None open'}
      />
    </div>

    {#if headline}
      <div style="margin-bottom:16px">
        <!--
          The invoice half of this had an `href` it should not have had: it
          pointed at `/invoices/<uuid>` while invoices is still fixtures
          keyed by slugs, so the one button on the page labelled "the thing
          that needs you" answered 404. Found by following the page's own
          outbound links rather than by reading it.

          Without an `href` the action stays a button that does nothing, which
          is the same wrong answer more quietly, so when the target is not
          wired the action is dropped entirely and the sentence stands on its
          own. It comes back when invoices is.
        -->
        <NextAction
          label="Needs you"
          text={headline}
          action={stalled.length ? 'Open the deal' : null}
          href={stalled.length ? `/pipeline/${stalled[0].id}` : null}
          tone="rust"
        />
      </div>
    {/if}

    <!-- align-items:start so each card is its own height. Stretched to match
         its neighbour, a one-row panel ends in a tall blank area that reads as
         content that failed to load. -->
    <div
      style="display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:start"
      class="v2-account-grid"
    >
      <!-- Deals -->
      <section class="v2-card" style="overflow:hidden">
        <div class="v2-card-head">
          <span class="v2-label">Deals</span>
          <a href={resolve('/pipeline')}>View all</a>
        </div>
        {#each deals as d (d.id)}
          <a
            href={resolve(`/pipeline/${d.id}`)}
            style="display:flex;gap:12px;align-items:center;padding:11px 15px;border-bottom:1px solid var(--v2-line-soft);color:inherit;text-decoration:none"
          >
            <div style="flex:1;min-width:0">
              <div style="font-weight:550;font-size:13px">{d.name}</div>
              <!-- `closed_on` is labelled "Expected Close Date" on the model
                   and means two different things depending on the stage. Bare,
                   it reads as though an open deal already closed. -->
              <div class="v2-sub" style="font-size:11.5px">
                {d.stage_label}{d.closed_on
                  ? CLOSED_KINDS.includes(d.stage_kind)
                    ? ` · closed ${shortDate(d.closed_on)}`
                    : ` · due ${shortDate(d.closed_on)}`
                  : ''}
              </div>
            </div>
            {#if d.aging_status === 'red' && !CLOSED_KINDS.includes(d.stage_kind)}
              <Pill tone="rust">{d.days_in_current_stage}d</Pill>
            {/if}
            <span class="v2-num" style="font-weight:600;font-size:13px"
              >{money(d.amount, d.currency)}</span
            >
          </a>
        {:else}
          <p class="v2-sub" style="padding:14px 15px;font-size:12.5px">
            No deals yet. Create one when there is something real to sell.
          </p>
        {/each}
      </section>

      <!-- People -->
      <section class="v2-card" style="overflow:hidden">
        <div class="v2-card-head">
          <span class="v2-label">People</span>
          <a href={resolve('/contacts')}>View all</a>
        </div>
        {#each contacts as c (c.id)}
          <div
            style="display:flex;gap:11px;align-items:center;padding:10px 15px;border-bottom:1px solid var(--v2-line-soft)"
          >
            <Avatar name="{c.first_name} {c.last_name}" size={29} />
            <!-- A link now. This was deliberately dead text while
                 `/contacts/<uuid>` answered 404, which is the reason
                 contacts was the module to wire next. -->
            <a
              href={resolve(`/contacts/${c.id}`)}
              style="flex:1;min-width:0;color:inherit;text-decoration:none"
            >
              <div style="font-weight:550;font-size:13px">{c.first_name} {c.last_name}</div>
              <!-- title and department. The mock showed a "relationship"
                   (Champion / Blocker); Contact has no such field. -->
              <div class="v2-sub" style="font-size:11.5px">
                {[c.title, c.department].filter(Boolean).join(' · ') || 'No title recorded'}
              </div>
            </a>
            {#if c.email}
              <a class="v2-btn v2-btn-sm" href="mailto:{c.email}" aria-label="Email {c.first_name}">
                <Mail size={13} />
              </a>
            {/if}
            {#if c.phone && !c.do_not_call}
              <a class="v2-btn v2-btn-sm" href="tel:{c.phone}" aria-label="Call {c.first_name}">
                <Phone size={13} />
              </a>
            {/if}
          </div>
        {:else}
          <p class="v2-sub" style="padding:14px 15px;font-size:12.5px">
            Nobody here yet. <a href={resolve(`/contacts/new?account=${account.id}`)}
              >Add the person</a
            > you actually talk to.
          </p>
        {/each}
      </section>

      <!-- Tickets -->
      <section class="v2-card" style="overflow:hidden">
        <div class="v2-card-head">
          <span class="v2-label">Tickets</span>
          <a href={resolve('/tickets')}>View all</a>
        </div>
        {#each tickets as t (t.id)}
          <!-- A link again: tickets is wired, so a real id sent to
               `/tickets/<uuid>` opens the ticket. -->
          <a
            href={resolve(`/tickets/${t.id}`)}
            style="display:flex;gap:12px;align-items:center;padding:11px 15px;border-bottom:1px solid var(--v2-line-soft);color:inherit;text-decoration:none"
          >
            <span style="flex:1;font-size:13px;min-width:0">{t.name}</span>
            <span class="v2-sub" style="font-size:11.5px">{t.status}</span>
            <Pill tone={PRIORITY_TONE[t.priority]}>{t.priority}</Pill>
          </a>
        {:else}
          <p class="v2-sub" style="padding:14px 15px;font-size:12.5px">
            No tickets. <a href={resolve(`/tickets/new?account=${account.id}`)}>Raise one</a> if something
            is wrong.
          </p>
        {/each}
      </section>

      <!-- Invoices -->
      <section class="v2-card" style="overflow:hidden">
        <div class="v2-card-head">
          <span class="v2-label">Invoices</span>
          <a href={resolve('/invoices')}>View all</a>
        </div>
        {#each invoices as inv (inv.id)}
          <!-- A link now: invoices is wired, so a real id sent to
               `/invoices/<uuid>` opens the invoice. -->
          <a
            href={resolve(`/invoices/${inv.id}`)}
            style="display:flex;gap:12px;align-items:center;padding:11px 15px;border-bottom:1px solid var(--v2-line-soft);color:inherit;text-decoration:none"
          >
            <span class="v2-num" style="font-size:12.5px">{inv.invoice_number}</span>
            <Pill tone={inv.past_due ? 'rust' : INVOICE_STATUS_TONE[inv.status]}>
              {inv.past_due ? 'Past due' : invoiceStatusLabel(inv.status)}
            </Pill>
            <span class="v2-num" style="margin-left:auto;font-weight:600;font-size:13px">
              {money(inv.past_due ? inv.amount_due : inv.total_amount, inv.currency)}
            </span>
          </a>
        {:else}
          <p class="v2-sub" style="padding:14px 15px;font-size:12.5px">Nothing billed yet.</p>
        {/each}
      </section>
    </div>
  </div>
</div>

<style>
  @media (max-width: 1080px) {
    .v2-account-grid {
      grid-template-columns: 1fr !important;
    }
  }
</style>
