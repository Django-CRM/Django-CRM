<script>
  import { resolve } from '$app/paths';
  /**
   * Where the money is.
   *
   * The one decision that shapes this page: **"not yet due" is not aging.**
   * The API returns it in the same aging response, and v1 draws it as the
   * first bucket, where it is by far the biggest bar, so the chart reads as
   * mostly healthy while $80,150 is genuinely late. Here the not-yet-due
   * figure is stated once, as context, and the aging chart contains only money
   * that is actually overdue.
   *
   * No chart library. Two bar shapes and a table do everything this page
   * needs, and shipping a charting dependency to draw sixteen rectangles costs
   * more than it explains.
   *
   * Every figure is server-computed over the whole window, nothing here is
   * summed from a page of invoices.
   *
   * Money is shown one currency at a time. There are no exchange rates, so the
   * server never adds two currencies together and neither does this page: an
   * org with invoices in several currencies gets a switcher, and every figure,
   * bar and row below it is in the chosen one.
   */
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import SectionTabs from '$lib/v2/components/SectionTabs.svelte';
  import StatCard from '$lib/v2/components/StatCard.svelte';
  import CurrencySwitch from '$lib/v2/components/CurrencySwitch.svelte';
  import { money, count } from '$lib/v2/format.js';

  /** @type {{ data: any }} */
  let { data } = $props();

  let canView = $derived(data.can_view);

  /** @type {string | null} */
  let picked = $state(null);
  /** The picked currency, else the org's own, else the first one invoiced in. */
  let cur = $derived(
    data.currencies.includes(picked)
      ? picked
      : data.currencies.includes(data.org.currency)
        ? data.org.currency
        : (data.currencies[0] ?? data.org.currency)
  );
  let v = $derived(data.byCurrency[cur] ?? data.blank);
  let d = $derived(v.dashboard);
  let aging = $derived(v.aging);

  let peak = $derived(Math.max(...v.revenue.flatMap((r) => [r.invoiced, r.paid])));
  let agingPeak = $derived(Math.max(...aging.buckets.map((b) => b.amount)));

  /** "2026-07" → "Jul". The year only appears where it changes. */
  const MONTH = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec'
  ];
  const monthLabel = (period) => MONTH[Number(period.slice(5, 7)) - 1];

  let collected = $derived(
    d.total_invoiced ? Math.round((d.total_paid / d.total_invoiced) * 100) : 0
  );
</script>

<PageHeader title="Invoices">
  {#snippet sub()}
    {#if canView}
      Last <span class="v2-num">{data.window_months}</span> months ·
      <span class="v2-num">{count(data.invoice_count)}</span> invoices
    {:else}
      Financial reports
    {/if}
  {/snippet}
</PageHeader>

<SectionTabs set="invoices" />

{#if !canView}
  <div class="v2-pad" style="padding-top:40px">
    <div class="v2-card rep-locked">
      <strong>These reports are for administrators.</strong>
      <p>
        Invoiced and collected totals, revenue by month and accounts-receivable aging cover the
        whole organisation's money, so they are limited to admins. Your own invoices and estimates
        are on the <a href={resolve('/invoices')}>Invoices</a> and
        <a href={resolve('/invoices/estimates')}>Estimates</a> tabs.
      </p>
    </div>
  </div>
{:else}
  <div class="v2-pad" style="padding-top:16px;flex:none">
    <CurrencySwitch currencies={data.currencies} current={cur} onpick={(c) => (picked = c)} />
    <div class="v2-stats">
      <StatCard label="Invoiced" value={money(d.total_invoiced, cur)} tone="ink" />
      <StatCard
        label="Collected"
        value={money(d.total_paid, cur)}
        tone="moss"
        detail="{collected}% of invoiced"
      />
      <StatCard
        label="Overdue"
        value={money(d.overdue_amount, cur)}
        tone={d.overdue_amount > 0 ? 'rust' : 'slate'}
        detail="{count(aging.overdue_count)} invoices past their due date"
      />
      <StatCard
        label="Average time to pay"
        value={`${data.average_days_to_pay}d`}
        tone="slate"
        detail="From issue to payment"
      />
    </div>
  </div>

  <div class="v2-scroll">
    <div class="v2-pad" style="padding-bottom:32px">
      <div class="v2-split-wide">
        <div>
          <div class="v2-label" style="margin-bottom:12px">Invoiced and collected, by month</div>
          <div class="v2-card" style="padding:16px 18px 14px">
            <!-- Side by side, never stacked: a stacked column's height would
               read as invoiced + paid, which is not a quantity anyone has. -->
            <div class="v2-cols">
              {#each v.revenue as r (r.period)}
                <div
                  class="v2-col"
                  title="{monthLabel(r.period)}: {money(r.invoiced, cur)} invoiced, {money(
                    r.paid,
                    cur
                  )} collected"
                >
                  <i class="in" style="height:{(r.invoiced / peak) * 100}%"></i>
                  <i class="out" style="height:{(r.paid / peak) * 100}%"></i>
                </div>
              {/each}
            </div>
            <div class="v2-cols-axis">
              {#each v.revenue as r (r.period)}
                <span>{monthLabel(r.period)}</span>
              {/each}
            </div>

            <div class="v2-sub" style="font-size:11.5px;margin-top:12px">
              <i class="v2-swatch v2-swatch-in"></i>invoiced
              <i class="v2-swatch" style="margin-left:10px"></i>collected
            </div>

            <!-- Said once, here, rather than left to be misread every month. -->
            <p class="v2-sub" style="font-size:11.5px;margin:11px 0 0;line-height:1.5">
              Each is counted in the month it happened, so they do not line up: the gap in the most
              recent month is earlier invoices still being paid, not a drop in sales.
            </p>
          </div>
        </div>

        <div>
          <div class="v2-label" style="margin-bottom:12px">How late the overdue money is</div>
          <div class="v2-card" style="padding:16px 18px">
            <div style="display:flex;flex-direction:column;gap:13px">
              {#each aging.buckets as b (b.key)}
                <div>
                  <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:5px">
                    <span style="font-size:12.5px">{b.label}</span>
                    <span class="v2-sub" style="font-size:11px">
                      <span class="v2-num">{count(b.count)}</span>
                      {b.count === 1 ? 'invoice' : 'invoices'}
                    </span>
                    <span class="v2-num" style="margin-left:auto;font-size:13px;font-weight:600">
                      {money(b.amount, cur)}
                    </span>
                  </div>
                  <div class="v2-bar">
                    <i
                      style="width:{(b.amount / agingPeak) * 100}%;{b.key === '90_plus'
                        ? 'background:var(--v2-rust)'
                        : ''}"
                    ></i>
                  </div>
                </div>
              {/each}
            </div>

            <!--
            Not-yet-due money stated as context, deliberately outside the
            chart. In the API response it is just another bucket, and drawn as
            one it dwarfs the rest and makes late money look small.
          -->
            <div class="v2-aging-note">
              <span class="v2-sub">Not yet due</span>
              <span class="v2-num" style="font-size:13px"
                >{money(aging.not_yet_due.amount, cur)}</span
              >
              <span class="v2-sub" style="font-size:11px">
                across {count(aging.not_yet_due.count)} invoices, not late, not shown above
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- Full width, below the split. The chart and the aging bars are close
         enough in height to sit side by side; adding the table to either
         column left the other one blank for a third of the page. -->
      <div>
        <div class="v2-label" style="margin:22px 0 10px">Who owes it</div>
        <div class="v2-table-wrap">
          <table class="v2-table">
            <thead>
              <tr>
                <th>Account</th>
                <th style="text-align:right">Invoices</th>
                <th style="text-align:right">Oldest</th>
                <th style="text-align:right">Overdue</th>
              </tr>
            </thead>
            <tbody>
              {#each v.overdueByAccount as a (a.id)}
                <tr>
                  <td class="v2-table-primary">
                    <a
                      href={resolve(`/accounts/${a.id}`)}
                      style="color:inherit;text-decoration:none"
                    >
                      {a.name}
                    </a>
                  </td>
                  <td class="v2-num" style="text-align:right">{a.count}</td>
                  <td
                    class="v2-num"
                    style="text-align:right;{a.oldest_days > 90 ? 'color:var(--v2-rust)' : ''}"
                  >
                    {a.oldest_days}d
                  </td>
                  <td class="v2-num" style="text-align:right;font-weight:600"
                    >{money(a.amount, cur)}</td
                  >
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <p class="v2-sub" style="font-size:11.5px;margin-top:10px">
          These {v.overdueByAccount.length} accounts are the whole overdue balance, not the top few of
          a longer list.
        </p>
      </div>
    </div>
  </div>
{/if}

<style>
  .rep-locked {
    max-width: 560px;
    /* Centre it: this card is the whole page for a non-admin, so left-hugging
       it strands the rest of a wide screen. Matches the tickets/analytics gate. */
    margin-inline: auto;
    padding: 20px 22px;
  }
  .rep-locked p {
    margin: 8px 0 0;
    font-size: 13px;
    line-height: 1.55;
    color: var(--v2-slate);
  }
  .rep-locked a {
    color: var(--v2-ink);
  }
  .v2-aging-note {
    display: flex;
    align-items: baseline;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 16px;
    padding-top: 13px;
    border-top: 1px solid var(--v2-line-soft);
    font-size: 12.5px;
  }
</style>
