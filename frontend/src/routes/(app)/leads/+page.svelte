<script>
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import FilterBar from '$lib/v2/components/FilterBar.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import Avatar from '$lib/v2/components/Avatar.svelte';
  import EmptyState from '$lib/v2/components/EmptyState.svelte';
  import LeadImportDrawer from '$lib/components/leads/LeadImportDrawer.svelte';
  import { money, count, relativeDays, daysSince } from '$lib/v2/format.js';
  import { LEAD_STATUS_TONE } from '$lib/v2/enums.js';
  import { Download, Plus, Upload, Target } from '@lucide/svelte';
  import { t } from '$lib/terminology.js';

  /** @type {{ data: any }} */
  let { data } = $props();

  let leads = $derived(data.leads);
  let importOpen = $state(false);
  let totals = $derived(data.totals);

  /* A vertical pack renames this module in the sidebar. Reading the same map
     here keeps the two agreeing, a nav item saying "Enquiries" that opens a
     page headed "Leads" reads as a bug, not as configuration. `t()` falls back
     to the literal, so an org with no terminology sees exactly what it saw
     before. The values are tenant text and render as plain text. */
  let terms = $derived(data.org?.terminology);
  let plural = $derived(t(terms, 'lead.plural', 'Leads'));
  let singular = $derived(t(terms, 'lead.singular', 'lead'));

  /**
   * The same rule the API counts with, so the highlighted rows and the
   * "unworked for more than a week" figure in the header agree. If one moves,
   * move the other, `LeadListView.UNWORKED_AFTER_DAYS`.
   *
   * @param {{ last_contacted: string | null, created_at: string }} lead
   */
  const stale = (lead) => (daysSince(lead.last_contacted ?? lead.created_at) ?? 0) > 7;
</script>

<PageHeader title={plural}>
  {#snippet sub()}
    <span class="v2-num">{count(totals.count)}</span> open ·
    <span class="v2-num">{totals.unworked_over_a_week}</span> unworked for more than a week
  {/snippet}
  {#snippet actions()}
    <a class="v2-btn" href={resolve('/leads/board')}>Board</a>
    <button class="v2-btn" onclick={() => (importOpen = true)}><Upload />Import</button>
    <!-- The page's own query string: the export rebuilds the same API query
         from it, so the file holds every row this list would page through. -->
    <a
      class="v2-btn"
      href="{resolve('/api/leads/export')}?{page.url.searchParams}"
      data-sveltekit-reload
    >
      <Download />Export
    </a>
    <a class="v2-btn v2-btn-primary" href={resolve('/leads/new')}><Plus />New {singular}</a>
  {/snippet}
</PageHeader>

<FilterBar
  page="leads"
  url={page.url}
  people={data.people}
  tags={data.tags}
  meId={data.meId}
  meta="Least recently touched first"
/>

<LeadImportDrawer
  bind:open={importOpen}
  singular={singular.toLowerCase()}
  plural={plural.toLowerCase()}
/>

<div class="v2-scroll">
  {#if leads.length === 0}
    <EmptyState
      title="No {plural.toLowerCase()} yet"
      body="A lead is somebody who might buy, before you know enough to call it a deal. Import a list, or add the last person who emailed you."
    >
      {#snippet icon()}<Target size={21} />{/snippet}
      {#snippet actions()}
        <a class="v2-btn v2-btn-primary" href={resolve('/leads/new')}>New {singular}</a>
        <button class="v2-btn" onclick={() => (importOpen = true)}>Import</button>
      {/snippet}
    </EmptyState>
  {:else}
    <div class="v2-table-wrap">
      <table class="v2-table">
        <thead>
          <tr>
            <th>Lead</th>
            <th>Company</th>
            <th>Status</th>
            <th>Source</th>
            <th class="v2-r">Est. value</th>
            <th>Last touch</th>
            <th>Owner</th>
          </tr>
        </thead>
        <tbody>
          {#each leads as l (l.id)}
            <tr>
              <td>
                <a class="v2-row-link" href={resolve(`/leads/${l.id}`)}>
                  <div class="v2-table-primary">{l.first_name} {l.last_name}</div>
                  <div class="v2-table-secondary">{l.job_title}</div>
                </a>
              </td>
              <td>
                <div>{l.company_name}</div>
                <div class="v2-table-secondary" data-m="hide">{l.industry}</div>
              </td>
              <td data-m="tag"><Pill tone={LEAD_STATUS_TONE[l.status]}>{l.status}</Pill></td>
              <td class="v2-muted" data-m="hide" style="font-size:12.5px">{l.source}</td>
              <td class="v2-r v2-num"
                >{l.opportunity_amount ? money(l.opportunity_amount, l.currency) : '—'}</td
              >
              <!--
              `last_contacted` is the only touch the model records. Lead has
              no aging chain: rotting days live on a deal's DealStage, and
              get_aging_status() is Opportunity-only. Where it is null the
              cell says so and falls back to how long the lead has been
              sitting, rather than substituting `updated_at`: an edit is not a
              conversation, and a column that quietly counts them stops being
              worth reading.
            -->
              <td class:v2-muted={!stale(l)} class:overdue={stale(l)}>
                {#if l.last_contacted}
                  {relativeDays(l.last_contacted)}
                {:else}
                  <div>Not contacted</div>
                  <!-- Stacked, matching the Company cell. Inline, these two ran
                       together into "Not contactedadded 64 days ago". -->
                  <div class="v2-table-secondary" data-m="hide">
                    added {relativeDays(l.created_at)}
                  </div>
                {/if}
              </td>
              <td data-m="hide"><Avatar name={l.assigned_to} size={22} /></td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <p class="v2-sub v2-pad" style="font-size:12px;padding-bottom:24px">
      Showing <span class="v2-num">{leads.length}</span> of
      <span class="v2-num">{count(totals.count)}</span>
    </p>
  {/if}
</div>

<style>
  /* A colour and a weight, not an inline style. The cell already carries a
     class for the ordinary case and the two should be stated the same way. */
  .overdue {
    color: var(--v2-rust);
    font-weight: 600;
  }
</style>
