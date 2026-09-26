<script>
  import { resolve } from '$app/paths';
  /**
   * Estimates, sorted so the one thing worth doing is at the top.
   *
   * Accepted-and-not-converted is money the customer has already agreed to and
   * nobody has billed. v1 gave it the same green "Accepted" pill as an
   * estimate that had become an invoice weeks ago, so the two were
   * indistinguishable and the gap only surfaced at month end. Status and
   * "has it been billed" are two separate facts and get two separate columns.
   */
  import { page } from '$app/state';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import SectionTabs from '$lib/v2/components/SectionTabs.svelte';
  import StatCard from '$lib/v2/components/StatCard.svelte';
  import FilterBar from '$lib/v2/components/FilterBar.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import EmptyState from '$lib/v2/components/EmptyState.svelte';
  import { enhance } from '$app/forms';
  import { money, moneyEach, count, daysSince } from '$lib/v2/format.js';
  import { ESTIMATE_STATUS_TONE } from '$lib/v2/enums.js';
  import { Plus, FileText } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  let totals = $derived(data.totals);

  /** Only an estimate that can still be accepted has a meaningful validity. */
  const LIVE = ['Sent', 'Viewed'];

  function validity(e) {
    if (!e.valid_until) return null;
    if (!LIVE.includes(e.status)) return null;
    const n = daysSince(e.valid_until);
    if (n > 0) return { text: `expired ${n}d ago`, urgent: true };
    if (n === 0) return { text: 'expires today', urgent: true };
    return { text: `${Math.abs(n)}d left`, urgent: Math.abs(n) <= 7 };
  }

  const needsBilling = (e) => e.status === 'Accepted' && !e.converted_invoice;

  /** `EstimateSendView`: not settled, and not past its validity date. */
  const canSend = (e) => ['Draft', 'Sent', 'Viewed'].includes(e.status) && !e.is_expired;

  /* Per currency, never added across: there are no exchange rates. */
  const perCurrency = (/** @type {any[]} */ list) => moneyEach(list) || money(0, data.org.currency);
</script>

<PageHeader title="Estimates">
  {#snippet sub()}
    <span class="v2-num">{count(totals.count)}</span> estimates ·
    <span class="v2-num">{perCurrency(totals.awaiting_reply)}</span> awaiting a reply
  {/snippet}
  {#snippet actions()}
    <a class="v2-btn v2-btn-primary" href={resolve('/invoices/estimates/new')}
      ><Plus />New estimate</a
    >
  {/snippet}
</PageHeader>

{#if page.url.search}
  <p class="v2-sub" style="font-size:11.5px;margin:8px 0 0">
    These numbers describe the filtered list.
  </p>
{/if}

<SectionTabs set="invoices" />

{#if form?.error}
  <div class="v2-pad" style="padding-top:12px;flex:none">
    <p class="est-error" role="alert">{form.error}</p>
  </div>
{:else if form?.sent}
  <div class="v2-pad" style="padding-top:12px;flex:none">
    <p class="v2-sub" role="status" style="margin:0">Estimate sent.</p>
  </div>
{/if}

<div class="v2-pad" style="padding-top:16px;flex:none">
  <div class="v2-stats">
    <StatCard
      label="Accepted, not billed"
      value={perCurrency(totals.accepted_unconverted)}
      tone="clay"
      detail="Agreed and waiting on an invoice"
    />
    <StatCard label="Awaiting a reply" value={perCurrency(totals.awaiting_reply)} tone="ink" />
    <StatCard
      label="Expiring within 7 days"
      value={count(totals.expiring_within_7d)}
      tone={totals.expiring_within_7d ? 'clay' : 'slate'}
    />
    <StatCard label="Estimates" value={count(totals.count)} tone="slate" />
  </div>
</div>

<FilterBar
  page="estimates"
  url={page.url}
  accounts={data.accounts}
  meta="Accepted but unbilled first, then most recently sent"
/>

<div class="v2-scroll">
  {#if data.estimates.length === 0}
    <EmptyState
      title="No estimates yet"
      body="An estimate is a priced proposal you can turn into an invoice once the customer accepts it. Most start from a deal that already has the amount and the account."
    >
      {#snippet icon()}<FileText size={21} />{/snippet}
      {#snippet actions()}
        <a class="v2-btn v2-btn-primary" href={resolve('/invoices/estimates/new')}>New estimate</a>
        <a class="v2-btn" href={resolve('/pipeline')}>Start from a deal</a>
      {/snippet}
    </EmptyState>
  {:else}
    <div class="v2-table-wrap">
      <table class="v2-table">
        <thead>
          <tr>
            <th>Estimate</th>
            <th>Account</th>
            <th>Status</th>
            <th>Billed</th>
            <th class="v2-r">Amount</th>
            <th class="v2-r">Valid</th>
          </tr>
        </thead>
        <tbody>
          {#each data.estimates as e (e.id)}
            {@const v = validity(e)}
            <tr>
              <td>
                <span class="v2-table-primary">{e.title}</span>
                <span class="v2-table-secondary" style="display:block">
                  <span class="v2-num">{e.estimate_number}</span> · {e.contact}
                </span>
              </td>
              <td>
                <a href={resolve(`/accounts/${e.account.id}`)} style="color:inherit"
                  >{e.account.name}</a
                >
                {#if e.opportunity}
                  <span class="v2-table-secondary" style="display:block">{e.opportunity.name}</span>
                {/if}
              </td>
              <td><Pill tone={ESTIMATE_STATUS_TONE[e.status]}>{e.status}</Pill></td>
              <td>
                {#if e.converted_invoice}
                  <a
                    href={resolve(`/invoices/${e.converted_invoice.id}`)}
                    class="v2-num"
                    style="color:inherit"
                  >
                    {e.converted_invoice.invoice_number}
                  </a>
                {:else if needsBilling(e)}
                  <!-- The one place ember belongs on this page: an accepted
                       estimate is the only row with an action nobody has taken.
                       Convert copies it to a Draft invoice and opens that. -->
                  <form method="POST" action="?/convert" use:enhance>
                    <input type="hidden" name="id" value={e.id} />
                    <button class="v2-btn v2-btn-sm v2-btn-primary" type="submit">
                      Raise invoice
                    </button>
                  </form>
                {:else if canSend(e)}
                  <form method="POST" action="?/send" use:enhance>
                    <input type="hidden" name="id" value={e.id} />
                    <button class="v2-btn v2-btn-sm" type="submit">
                      {e.status === 'Draft' ? 'Send' : 'Send again'}
                    </button>
                  </form>
                {:else}
                  <span class="v2-muted">—</span>
                {/if}
              </td>
              <td class="v2-r v2-num" style="font-weight:600"
                >{money(e.total_amount, e.currency)}</td
              >
              <td class="v2-r" style={v?.urgent ? 'color:var(--v2-rust);font-weight:600' : ''}>
                {#if v}
                  {v.text}
                {:else}
                  <span class="v2-muted">—</span>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <p class="v2-sub v2-pad" style="font-size:12px;padding-bottom:24px">
      Showing <span class="v2-num">{data.estimates.length}</span> of
      <span class="v2-num">{count(totals.count)}</span>
    </p>
  {/if}
</div>

<style>
  .est-error {
    margin: 0;
    padding: 8px 12px;
    border: 1px solid color-mix(in srgb, var(--v2-rust) 40%, transparent);
    border-radius: 6px;
    background: color-mix(in srgb, var(--v2-rust) 8%, transparent);
    color: var(--v2-rust);
    font-size: 13px;
  }
</style>
