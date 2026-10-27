<script lang="ts">
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { asInternalPath } from '#lib/utils/paths.js';
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import EmptyState from '#lib/v2/components/EmptyState.svelte';
  import FilterBar from '#lib/v2/components/FilterBar.svelte';
  import LeadImportDrawer from '#lib/components/leads/LeadImportDrawer.svelte';
  import { objectImportConfig } from '#lib/import/entities.js';
  import { objectDescriptor } from '#lib/v2/object-filters.js';
  import { count } from '#lib/v2/format.js';
  import { Boxes, Columns3, Download, List, Plus, Upload } from '@lucide/svelte';
  import type { PageProps } from './$types';

  let { data }: PageProps = $props();

  let def = $derived(data.definition);
  let rows = $derived(data.page.results);
  let canCreate = $derived(data.customObjects?.has_custom_objects === true);
  let hasNext = $derived(data.pageNumber * 20 < data.page.count);
  let importOpen = $state(false);
  let canImport = $derived(canCreate && data.canImport);
  let descriptor = $derived(objectDescriptor(def.fields));
  let importConfig = $derived(objectImportConfig(def));
  // Anything filtering the list, so the search box and the pager keep it.
  let filtered = $derived(
    data.q !== '' ||
      [...page.url.searchParams.keys()].some((k) => k !== 'page' && k !== 'q' && k !== 'limit')
  );

  // The board takes the list's filters, so the switch carries them over.
  let boardHref = $derived.by(() => {
    const params = new URLSearchParams(page.url.search);
    params.delete('page');
    const qs = params.toString();
    return `/objects/${def.key}/board${qs ? `?${qs}` : ''}`;
  });

  function pageQuery(n: number) {
    const params = new URLSearchParams(page.url.search);
    params.delete('page');
    if (n > 1) params.set('page', String(n));
    const qs = params.toString();
    return `/objects/${def.key}${qs ? `?${qs}` : ''}`;
  }
</script>

<PageHeader title={def.label_plural}>
  {#snippet sub()}
    {#if def.description}{def.description}{:else}<span class="v2-num">{count(data.page.count)}</span
      >
      {data.page.count === 1 ? def.label.toLowerCase() : def.label_plural.toLowerCase()}{/if}
  {/snippet}
  {#snippet actions()}
    {#if data.hasBoard}
      <span class="v2-btn" aria-current="true"><List />List</span>
      <a class="v2-btn v2-btn-quiet" href={resolve(asInternalPath(boardHref))}><Columns3 />Board</a>
    {/if}
    {#if canImport}
      <button class="v2-btn" onclick={() => (importOpen = true)}><Upload />Import</button>
    {/if}
    <a
      class="v2-btn"
      href="{resolve(asInternalPath(`/api/objects/${def.key}/export`))}?{page.url.searchParams}"
      data-sveltekit-reload
    >
      <Download />Export
    </a>
    {#if canCreate}
      <a class="v2-btn v2-btn-primary" href={resolve(asInternalPath(`/objects/${def.key}/new`))}
        ><Plus />New {def.label}</a
      >
    {/if}
  {/snippet}
</PageHeader>

<FilterBar
  page="objects"
  {descriptor}
  url={page.url}
  people={data.people}
  tags={data.tags}
  meId={data.meId}
  saved={data.savedViews}
/>

{#if canImport}
  <LeadImportDrawer
    bind:open={importOpen}
    singular={importConfig.singular}
    plural={importConfig.plural}
    config={importConfig}
  />
{/if}

<div class="v2-scroll">
  <form class="ol-search" method="GET" role="search">
    {#each [...page.url.searchParams] as [name, value], i (`${i}:${name}`)}
      {#if name !== 'q' && name !== 'page'}<input type="hidden" {name} {value} />{/if}
    {/each}
    <input
      class="v2-input"
      type="search"
      name="q"
      value={data.q}
      maxlength="100"
      placeholder="Search by {def.name_label.toLowerCase()}"
      aria-label="Search {def.label_plural}"
    />
    <button class="v2-btn" type="submit">Search</button>
  </form>

  {#if rows.length === 0}
    <EmptyState
      title={filtered ? 'No matches' : `No ${def.label_plural.toLowerCase()} yet`}
      body={filtered
        ? 'Nothing matches these filters. Try fewer, or a shorter search.'
        : `Records of this kind will be listed here.`}
    >
      {#snippet icon()}<Boxes size={21} />{/snippet}
      {#snippet actions()}
        {#if canCreate && !filtered}
          <a class="v2-btn v2-btn-primary" href={resolve(asInternalPath(`/objects/${def.key}/new`))}
            >New {def.label}</a
          >
        {/if}
      {/snippet}
    </EmptyState>
  {:else}
    <div class="v2-table-wrap">
      <table class="v2-table">
        <thead>
          <tr>
            <th>{def.name_label}</th>
            {#each data.columns as c (c.key)}<th>{c.label}</th>{/each}
            <th>Assigned to</th>
            <th>Tags</th>
          </tr>
        </thead>
        <tbody>
          {#each rows as r (r.id)}
            <tr>
              <td data-m="title">
                <a
                  class="v2-row-link"
                  href={resolve(asInternalPath(`/objects/${def.key}/${r.id}`))}
                >
                  <div class="v2-table-primary">{r.name}</div>
                </a>
              </td>
              {#each r.cells as cell, i (data.columns[i].key)}
                <td data-m="meta" data-l={data.columns[i].label}>{cell}</td>
              {/each}
              <td class="v2-muted" data-m="hide">{r.assignees.join(', ')}</td>
              <td class="v2-muted" data-m="meta" data-l={r.tags.length ? 'Tags' : undefined}
                >{r.tags.join(', ')}</td
              >
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <p class="v2-sub v2-pad ol-foot">
      Showing <span class="v2-num">{rows.length}</span> of
      <span class="v2-num">{count(data.page.count)}</span>
    </p>
    {#if data.pageNumber > 1 || hasNext}
      <nav class="v2-pad ol-pager" aria-label="Pages">
        {#if data.pageNumber > 1}
          <a class="v2-btn" href={resolve(asInternalPath(pageQuery(data.pageNumber - 1)))}
            >Previous</a
          >
        {/if}
        {#if hasNext}
          <a class="v2-btn" href={resolve(asInternalPath(pageQuery(data.pageNumber + 1)))}>Next</a>
        {/if}
      </nav>
    {/if}
  {/if}
</div>

<style>
  .ol-search {
    display: flex;
    gap: 8px;
    padding: 12px var(--v2-pad, 24px) 4px;
  }
  .ol-search .v2-input {
    flex: 1;
    min-width: 0;
    min-height: 44px;
  }
  .ol-search .v2-btn {
    min-height: 44px;
  }
  .ol-foot {
    font-size: 12px;
    padding-bottom: 12px;
  }
  .ol-pager {
    display: flex;
    gap: 9px;
    padding-bottom: 24px;
  }
</style>
