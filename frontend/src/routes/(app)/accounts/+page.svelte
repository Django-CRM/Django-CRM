<script>
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import FilterBar from '$lib/v2/components/FilterBar.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import EmptyState from '$lib/v2/components/EmptyState.svelte';
  import { moneyEach, count } from '$lib/v2/format.js';
  import { activeChips, activePresetKey } from '$lib/v2/filters.js';
  import { Download, Plus, Building2 } from '@lucide/svelte';

  /** @type {{ data: any }} */
  let { data } = $props();

  let accounts = $derived(data.accounts);
  let totals = $derived(data.totals);

  /**
   * Whether the view is actually narrowed, rather than merely carrying a query
   * string. `page.url.search` alone is the wrong test: it fires on any param,
   * including ones that are not filters at all. `'all'` is this page's
   * empty-params preset, its declared default, so any other preset counts as
   * filtered even when it sets no field a chip would show.
   */
  let isFiltered = $derived(
    activeChips('accounts', page.url, { people: data.people, tags: data.tags }).length > 0 ||
      activePresetKey('accounts', page.url, data.meId) !== 'all'
  );
</script>

<PageHeader title="Accounts">
  {#snippet sub()}
    <!-- The count is the size of the whole result set, not of this page. -->
    <span class="v2-num">{count(totals.count)}</span> accounts
    <!-- `customers` is counted from the rows actually loaded, because the
         accounts endpoint returns no aggregate for it (see the note in
         `lib/server/v2/accounts.js`). Printing it beside a whole-set count
         when only one page is loaded states a smaller number as though it
         covered everything, so it is shown only when this page IS the whole
         set. A figure that disappears beats a figure that is wrong. -->
    {#if (totals.shown ?? 0) >= (totals.count ?? 0)}
      · <span class="v2-num">{count(totals.customers)}</span> with a deal won
    {/if}
  {/snippet}
  {#snippet actions()}
    <!-- The page's own query string: the export rebuilds the same API query
         from it, so the file holds every row this list would page through. -->
    <a
      class="v2-btn"
      href="{resolve('/api/accounts/export')}?{page.url.searchParams}"
      data-sveltekit-reload
    >
      <Download />Export
    </a>
    <a class="v2-btn v2-btn-primary" href={resolve('/accounts/new')}><Plus />New account</a>
  {/snippet}
</PageHeader>

{#if isFiltered}
  <p class="v2-sub" style="font-size:11.5px;margin:8px 0 0">
    These numbers describe the filtered list.
  </p>
{/if}

<FilterBar
  page="accounts"
  url={page.url}
  people={data.people}
  tags={data.tags}
  meId={data.meId}
  meta="Sorted by revenue won"
/>

<div class="v2-scroll">
  {#if accounts.length === 0}
    <EmptyState
      title="No accounts yet"
      body="An account is a company you sell to. One appears automatically the first time you convert a lead, or you can add one directly."
    >
      {#snippet icon()}<Building2 size={21} />{/snippet}
      {#snippet actions()}
        <a class="v2-btn v2-btn-primary" href={resolve('/accounts/new')}>New account</a>
        <a class="v2-btn" href={resolve('/leads')}>Go to leads</a>
      {/snippet}
    </EmptyState>
  {:else}
    <div class="v2-table-wrap">
      <table class="v2-table">
        <thead>
          <tr>
            <th>Account</th>
            <th>Industry</th>
            <th class="v2-r">Won</th>
            <th class="v2-r">Open pipeline</th>
            <th class="v2-r">Past due</th>
            <th>Tickets</th>
          </tr>
        </thead>
        <tbody>
          {#each accounts as a (a.id)}
            <tr>
              <td>
                <a class="v2-row-link" href={resolve(`/accounts/${a.id}`)}>
                  <div class="v2-table-primary">{a.name}</div>
                  <div class="v2-table-secondary">
                    {[a.city, a.country_display].filter(Boolean).join(', ') || 'No address'}
                  </div>
                </a>
              </td>
              <td class="v2-muted" data-m="hide" style="font-size:12.5px">
                {a.industry || '—'}
              </td>
              <!-- All four figures are computed by the API over the whole
                   related set, never derived from the rows on screen. Money is
                   one amount per currency, never added across currencies. -->
              <td class="v2-r v2-num" data-m="hide">
                {a.won_by_currency?.length ? moneyEach(a.won_by_currency) : '—'}
              </td>
              <!-- Labelled on a phone: without the header row, two money
                   columns side by side are two unattributed numbers. -->
              <td class="v2-r v2-num" data-l="Pipeline">
                {a.pipeline_by_currency?.length ? moneyEach(a.pipeline_by_currency) : '—'}
              </td>
              <td
                class="v2-r v2-num"
                data-l="Past due"
                style={a.overdue_by_currency?.length ? 'color:var(--v2-rust);font-weight:600' : ''}
              >
                {a.overdue_by_currency?.length ? moneyEach(a.overdue_by_currency) : '—'}
              </td>
              <td>
                {#if a.open_tickets}
                  <Pill tone="slate">{a.open_tickets} open</Pill>
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
      {#if totals.shown < totals.count}
        Showing <span class="v2-num">{totals.shown}</span> of
        <span class="v2-num">{count(totals.count)}</span>
      {:else}
        Showing all <span class="v2-num">{count(totals.count)}</span>
      {/if}
      {#if totals.inactive}
        · <span class="v2-num">{totals.inactive}</span> inactive not shown
      {/if}
    </p>
  {/if}
</div>
