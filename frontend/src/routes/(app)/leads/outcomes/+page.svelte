<script lang="ts">
  import { resolve } from '$app/paths';
  import { asInternalPath } from '#lib/utils/paths.js';
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import EmptyState from '#lib/v2/components/EmptyState.svelte';
  import { shortDate } from '#lib/v2/format.js';
  import { spanText } from '#lib/v2/interactions.js';
  import { t } from '#lib/terminology.js';
  import { ChevronRight, Timer, Trophy } from '@lucide/svelte';
  import type { PageProps } from './$types';

  let { data }: PageProps = $props();

  let singular = $derived(t(data.org?.terminology, 'lead.singular', 'Lead'));
  let plural = $derived(t(data.org?.terminology, 'lead.plural', 'Leads'));
  let empty = $derived(data.rows.every((r) => !r.won && !r.lost && !r.new_leads));
  let measuringLater = $derived(
    !!data.since && !!data.measured_since && data.measured_since > data.since
  );

  const rate = (n: number | null) => (n === null ? '—' : `${n}%`);
  const span = (n: number | null) => (n === null ? '—' : spanText(n));
</script>

<PageHeader title="Outcomes">
  {#snippet crumb()}
    <a href={resolve('leads')}>{plural}</a>
    <ChevronRight size={12} />
    <span>Outcomes</span>
  {/snippet}
  {#snippet sub()}
    Who turns {plural.toLowerCase()} into wins, and how long it takes, per person{data.since
      ? `, since ${shortDate(data.since)}`
      : ''}.
    {#if measuringLater}
      <span class="measuring">Measuring since {shortDate(data.measured_since)}</span>
    {/if}
  {/snippet}
  {#snippet actions()}
    <a class="v2-btn" href={resolve('leads/response-times')}><Timer />Response time</a>
    <nav class="periods" aria-label="Period">
      {#each data.periods as n (n)}
        <a
          href={resolve(asInternalPath(`/leads/outcomes?days=${n}`))}
          aria-current={n === data.days ? 'page' : undefined}>{n} days</a
        >
      {/each}
    </nav>
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  {#if empty}
    <EmptyState
      title="No outcomes yet"
      body="Outcomes appear once a lead is won or lost, or a new one arrives, in this period."
    >
      {#snippet icon()}<Trophy size={21} />{/snippet}
    </EmptyState>
  {:else}
    <div class="v2-table-wrap" style="margin-top:14px">
      <table class="v2-table">
        <thead>
          <tr>
            <th>Person</th>
            <th class="v2-r">Won</th>
            <th class="v2-r">Lost</th>
            <th class="v2-r">Win rate</th>
            <th class="v2-r">Median time to win</th>
            <th class="v2-r">New leads</th>
          </tr>
        </thead>
        <tbody>
          {#each data.rows as row (row.profile || row.name)}
            <tr>
              <td><div class="v2-table-primary">{row.name}</div></td>
              <td class="v2-r v2-num" data-l="Won">{row.won}</td>
              <td class="v2-r v2-num" data-l="Lost">{row.lost}</td>
              <td class="v2-r v2-num" data-l="Win rate">{rate(row.win_rate)}</td>
              <td class="v2-r v2-num" data-l="Median time to win">
                {span(row.median_seconds_to_win)}
              </td>
              <td class="v2-r v2-num" data-l="New leads">{row.new_leads}</td>
            </tr>
          {/each}
          {#if data.totals}
            <tr class="totals">
              <td><div class="v2-table-primary">Everyone</div></td>
              <td class="v2-r v2-num" data-l="Won">{data.totals.won}</td>
              <td class="v2-r v2-num" data-l="Lost">{data.totals.lost}</td>
              <td class="v2-r v2-num" data-l="Win rate">{rate(data.totals.win_rate)}</td>
              <td class="v2-r v2-num" data-l="Median time to win">
                {span(data.totals.median_seconds_to_win)}
              </td>
              <td class="v2-r v2-num" data-l="New leads">{data.totals.new_leads}</td>
            </tr>
          {/if}
        </tbody>
      </table>
    </div>
  {/if}

  <div class="v2-pad notes">
    <p>
      Wins and losses count for whoever the {singular.toLowerCase()} is assigned to now, and one with
      two owners counts for both. Time to win runs from when the {singular.toLowerCase()} arrived to when
      it was won. Win rate is wins out of wins and losses.
      {data.isAdmin ? '' : 'You see your own row; an admin sees everyone’s.'}
    </p>
  </div>
</div>

<style>
  .periods {
    display: inline-flex;
    border: 1px solid var(--v2-line);
    border-radius: 7px;
    overflow: hidden;
  }
  .periods a {
    display: inline-flex;
    align-items: center;
    padding: 6px 12px;
    font-size: 12.5px;
    color: var(--v2-slate);
    background: var(--v2-card);
    text-decoration: none;
  }
  .periods a + a {
    border-left: 1px solid var(--v2-line);
  }
  .periods a[aria-current='page'] {
    color: var(--v2-ink);
    background: var(--v2-hover);
    font-weight: 600;
  }
  .measuring {
    display: block;
    margin-top: 2px;
    color: var(--v2-slate);
  }
  .totals td {
    font-weight: 600;
    border-top: 1px solid var(--v2-line);
  }
  .notes {
    padding-top: 14px;
    padding-bottom: 28px;
    max-width: 680px;
  }
  .notes p {
    margin: 0 0 8px;
    font-size: 12px;
    line-height: 1.55;
    color: var(--v2-slate);
  }
  @media (max-width: 767px) {
    .periods a {
      min-height: 44px;
      padding: 8px 14px;
    }
  }
</style>
