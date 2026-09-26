<script>
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import SectionTabs from '$lib/v2/components/SectionTabs.svelte';
  import FilterBar from '$lib/v2/components/FilterBar.svelte';
  import StatCard from '$lib/v2/components/StatCard.svelte';
  import CurrencySwitch from '$lib/v2/components/CurrencySwitch.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import EmptyState from '$lib/v2/components/EmptyState.svelte';
  import { money, count, shortDate, daysSince } from '$lib/v2/format.js';
  import { INVOICE_STATUS_TONE, invoiceStatusLabel } from '$lib/v2/enums.js';
  import { enhance } from '$app/forms';
  import { Download, Plus, Receipt } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  let invoices = $derived(data.invoices);
  let totals = $derived(data.totals);

  /**
   * The money is per currency: there are no exchange rates, so the header
   * shows one currency at a time with a switch when there are several. The
   * picked one, else the org's own, else the first one invoiced in.
   *
   * @type {string | null}
   */
  let picked = $state(null);
  let cur = $derived(
    totals.currencies.includes(picked)
      ? picked
      : totals.currencies.includes(data.org.currency)
        ? data.org.currency
        : (totals.currencies[0] ?? data.org.currency)
  );
  let m = $derived(totals.byCurrency[cur] ?? totals.blank);

  /** Rows with a send in flight, so the button can show it is working. */
  let sending = $state(/** @type {Record<string, boolean>} */ ({}));

  /**
   * Days past due, or days remaining. A settled invoice has no age, once it
   * is Paid or Cancelled the due date stops meaning anything, and showing
   * "4d late" against a paid invoice is just wrong.
   */
  const SETTLED = ['Paid', 'Cancelled'];

  function ageLabel(inv) {
    if (SETTLED.includes(inv.status)) return '—';
    if (!inv.due_date) return '—';
    const n = daysSince(inv.due_date);
    if (n > 0) return `${n}d late`;
    if (n === 0) return 'due today';
    return `${Math.abs(n)}d left`;
  }

  const isLate = (inv) =>
    !SETTLED.includes(inv.status) && inv.due_date && daysSince(inv.due_date) > 0;
</script>

<PageHeader title="Invoices">
  {#snippet sub()}
    <!--
      These aggregates come from the API over the whole result set. v1 summed
      the loaded page, so a 50-row list showed pills adding up to 10.
    -->
    <span class="v2-num">{count(totals.count)}</span> invoices ·
    <span class="v2-num">{money(m.outstanding, cur)}</span> outstanding
  {/snippet}
  {#snippet actions()}
    <!-- The page's own query string: the export rebuilds the same API query
         from it, so the file holds every row this list would page through. -->
    <a
      class="v2-btn"
      href="{resolve('/api/invoices/export')}?{page.url.searchParams}"
      data-sveltekit-reload
    >
      <Download />Export
    </a>
    <a class="v2-btn v2-btn-primary" href={resolve('/invoices/new')}><Plus />New invoice</a>
  {/snippet}
</PageHeader>

<!-- Estimates and Recurring were buttons in this header that went nowhere,
     while /invoices/estimates existed in v1 and was reachable only from a
     dropdown elsewhere. They are sibling pages; a tab strip says so. -->
<SectionTabs set="invoices" />

<div class="v2-pad" style="padding-top:16px;flex:none">
  <CurrencySwitch currencies={totals.currencies} current={cur} onpick={(c) => (picked = c)} />
  <div class="v2-stats">
    <StatCard
      label="Overdue"
      value={money(m.overdue, cur)}
      tone="rust"
      detail="Chase these first"
    />
    <StatCard label="Due this month" value={money(m.due_this_month, cur)} tone="clay" />
    <StatCard label="Paid this quarter" value={money(m.paid_this_quarter, cur)} tone="moss" />
    <StatCard label="Draft" value={money(m.draft, cur)} tone="slate" detail="Not sent yet" />
  </div>
</div>

<!-- No "filtered list" line here, unlike the other v2 list pages: the pills
     above come from `totals`, which the API deliberately computes over the
     whole role-scoped queryset regardless of the active filter (see the note
     on `listInvoices` in `$lib/server/v2/invoices.js`). Saying "these numbers
     describe the filtered list" would be false for this page. -->
<FilterBar
  page="invoices"
  url={page.url}
  people={data.people}
  accounts={data.accounts}
  meId={data.meId}
  meta="Oldest due first"
/>

{#if form?.error}
  <p class="v2-sub v2-pad" style="color:var(--v2-rust);font-size:12.5px;padding-top:10px">
    {form.error}
  </p>
{/if}

<div class="v2-scroll">
  {#if invoices.length === 0}
    <EmptyState
      title="Nothing billed yet"
      body="Invoices show up here once you raise one. A won deal is usually the place to start. The amount and the account are already there."
    >
      {#snippet icon()}<Receipt size={21} />{/snippet}
      {#snippet actions()}
        <a class="v2-btn v2-btn-primary" href={resolve('/invoices/new')}>New invoice</a>
        <a class="v2-btn" href={resolve('/pipeline')}>Go to pipeline</a>
      {/snippet}
    </EmptyState>
  {:else}
    <div class="v2-table-wrap">
      <table class="v2-table">
        <thead>
          <tr>
            <th>Invoice</th>
            <th>Account</th>
            <th>Status</th>
            <th class="v2-r">Amount</th>
            <th>Due</th>
            <th class="v2-r">Age</th>
            <th style="width:130px"></th>
          </tr>
        </thead>
        <tbody>
          {#each invoices as inv (inv.id)}
            {@const late = isLate(inv)}
            <tr>
              <td>
                <a class="v2-row-link" href={resolve(`/invoices/${inv.id}`)}>
                  <span class="v2-table-primary v2-num" style="font-size:13px"
                    >{inv.invoice_number}</span
                  >
                </a>
              </td>
              <td>{inv.account.name}</td>
              <td>
                <Pill tone={INVOICE_STATUS_TONE[inv.status]}>{invoiceStatusLabel(inv.status)}</Pill>
              </td>
              <td class="v2-r v2-num" style="font-weight:600"
                >{money(inv.total_amount, inv.currency)}</td
              >
              <td>{shortDate(inv.due_date)}</td>
              <td
                class="v2-r v2-num"
                class:v2-muted={!late}
                style={late ? 'color:var(--v2-rust);font-weight:600' : ''}
              >
                {ageLabel(inv)}
              </td>
              <td>
                {#if inv.status === 'Overdue' || inv.status === 'Draft'}
                  <form
                    method="POST"
                    action="?/send"
                    use:enhance={() => {
                      sending[inv.id] = true;
                      return async ({ update }) => {
                        await update();
                        sending[inv.id] = false;
                      };
                    }}
                  >
                    <input type="hidden" name="id" value={inv.id} />
                    <button class="v2-btn v2-btn-sm" disabled={sending[inv.id]}>
                      {#if sending[inv.id]}Sending…{:else if inv.status === 'Overdue'}Send a
                        reminder{:else}Send{/if}
                    </button>
                  </form>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <p class="v2-sub v2-pad" style="font-size:12px;padding-bottom:24px">
      Showing <span class="v2-num">{invoices.length}</span> of
      <span class="v2-num">{count(totals.count)}</span>
    </p>
  {/if}
</div>
