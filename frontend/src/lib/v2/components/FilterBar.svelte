<script>
  import { resolve } from '$app/paths';
  import { asInternalPath } from '$lib/utils/paths.js';
  /**
   * One filter system, for every v2 list page.
   *
   * A preset picker, then one removable chip per active filter, then Add.
   * Nothing hidden in a dropdown that the row of chips does not already show.
   *
   * Everything here is a link or a `method="GET"` form, which is the browser's
   * native way to write URL params. So the filter bar works on a cold page and
   * survives a failed hydration, and there is no state to keep in sync with the
   * address bar.
   *
   * The component derives its own chips from the URL rather than taking a
   * prebuilt list. That is what makes every chip removable: a chip exists only
   * when its param is actually set. The previous version took a `filters` array
   * that pages built by hand, and several passed chips describing an implicit
   * default, so the X had nothing to remove and did nothing.
   *
   * @type {{
   *   page: string,
   *   url: URL,
   *   people?: {id: string, name: string}[],
   *   tags?: {id: string, name: string}[],
   *   accounts?: {id: string, name: string}[],
   *   stages?: {id: string, name: string}[],
   *   meId?: string | null,
   *   meta?: string | null,
   *   onlyFields?: string[],
   *   onlyPresets?: string[]
   * }}
   */
  import { X, Plus, ChevronDown } from '@lucide/svelte';
  import { FILTERS, activeChips, activePresetKey, withParams } from '$lib/v2/filters.js';
  import { invoiceStatusLabel } from '$lib/v2/enums.js';

  let {
    page,
    url,
    people = [],
    tags = [],
    accounts = [],
    stages = [],
    meId = null,
    meta = null,
    onlyFields = undefined,
    onlyPresets = undefined
  } = $props();

  let descriptor = $derived(FILTERS[page] ?? { presets: [], fields: [] });

  /**
   * Some pages run a second query engine alongside the main one, with a
   * narrower filter vocabulary than the page's own descriptor. Pipeline's
   * board is the first: it renders from `/opportunities/kanban/`, which reads
   * only a few of the params `/opportunities/` does. `onlyFields`/
   * `onlyPresets` name the subset that view can actually run; when passed,
   * the bar offers, and only offers, controls and chips for that subset. A
   * chip for a param the current view cannot honour would sit above rows it
   * did not filter, while a header total computed with the full param set
   * would disagree with what the rows show.
   */
  let visibleFields = $derived(
    onlyFields ? descriptor.fields.filter((f) => onlyFields.includes(f.key)) : descriptor.fields
  );
  let visiblePresets = $derived(
    onlyPresets ? descriptor.presets.filter((p) => onlyPresets.includes(p.key)) : descriptor.presets
  );

  let chips = $derived(
    activeChips(page, url, { people, tags, accounts, stages }).filter(
      (chip) => !onlyFields || onlyFields.includes(chip.key)
    )
  );
  // meId is passed so a "@me" preset matches only the viewer's own id. Without
  // it, filtering by a colleague would light up "Mine".
  let activeKey = $derived(activePresetKey(page, url, meId));
  let activeLabel = $derived(
    descriptor.presets.find((/** @type {any} */ p) => p.key === activeKey)?.label ?? 'All'
  );

  /**
   * A preset's href. Every param the preset does not set is cleared, so
   * switching view is a fresh start rather than a merge with whatever chips
   * were up.
   *
   * A preset naming `@me` is dropped entirely when the viewer's profile could
   * not be resolved: a "Mine" link that filters to nobody looks like the viewer
   * owns nothing.
   */
  const presetHref = (/** @type {any} */ preset) => {
    /** @type {Record<string, string|null>} */
    const params = {};
    for (const field of descriptor.fields) {
      if (field.type === 'date-range' || field.type === 'number-range') {
        params[field.gteKey] = null;
        params[field.lteKey] = null;
      } else params[field.key] = null;
    }
    for (const other of descriptor.presets) {
      for (const key of Object.keys(other.params)) params[key] = null;
    }
    for (const [key, value] of Object.entries(preset.params)) {
      params[key] = value === '@me' ? meId : /** @type {string} */ (value);
    }
    return withParams(url, params);
  };

  const usable = (/** @type {any} */ preset) =>
    !Object.values(preset.params).includes('@me') || Boolean(meId);

  const optionsFor = (/** @type {any} */ field) => {
    if (field.type === 'person') return people;
    if (field.type === 'tag') return tags;
    if (field.type === 'account') return accounts;
    if (field.type === 'stage') return stages;
    return (field.options ?? []).map((/** @type {string} */ v) => ({
      id: v,
      name: field.labelFor ? field.labelFor(v) : invoiceStatusLabel(v)
    }));
  };
</script>

<div class="v2-filters">
  <details class="v2-view-menu">
    <summary class="v2-view">
      {activeLabel}
      <ChevronDown size={13} style="color:var(--v2-slate)" />
    </summary>
    <div class="v2-menu">
      {#each visiblePresets.filter(usable) as preset (preset.key)}
        <a
          class="v2-menu-item"
          class:v2-menu-item-on={preset.key === activeKey}
          href={resolve(asInternalPath(presetHref(preset)))}
        >
          {preset.label}
        </a>
      {/each}
    </div>
  </details>

  {#each chips as chip (chip.key)}
    <span class="v2-chip">
      <b>{chip.label}</b>
      {chip.value}
      <a href={resolve(asInternalPath(chip.href))} aria-label="Remove the {chip.label} filter"
        ><X size={12} /></a
      >
    </span>
  {/each}

  {#if visibleFields.length > 0}
    <details class="v2-filter-menu">
      <summary class="v2-chip v2-chip-add">
        <Plus size={12} />
        Filter
      </summary>
      <form class="v2-menu v2-filter-form" method="GET">
        <!-- Params the form does not own (the preset's own, plus paging) would
             be dropped by a GET submit, which resets the whole URL. Carrying
             them as hidden inputs keeps the active view when a filter is
             added.

             This checks against the FULL descriptor, not `visibleFields`, on
             purpose: a param that belongs to a field this view is not
             offering (a Stage value left over on the board, say) is exactly
             the kind of param this view cannot honour, so submitting Apply
             here drops it rather than carrying it along as a hidden input. -->
        {#each [...url.searchParams] as [key, value] (key)}
          {#if !descriptor.fields.some((/** @type {any} */ f) => f.key === key || f.gteKey === key || f.lteKey === key)}
            <input type="hidden" name={key} {value} />
          {/if}
        {/each}

        {#each visibleFields as field (field.key)}
          <label class="v2-filter-row">
            <span class="v2-label">{field.label}</span>
            {#if field.type === 'date-range'}
              <span class="v2-filter-dates">
                <input
                  class="v2-input"
                  type="date"
                  name={field.gteKey}
                  value={url.searchParams.get(field.gteKey) ?? ''}
                />
                <input
                  class="v2-input"
                  type="date"
                  name={field.lteKey}
                  value={url.searchParams.get(field.lteKey) ?? ''}
                />
              </span>
            {:else if field.type === 'number-range'}
              <span class="v2-filter-dates">
                <input
                  class="v2-input"
                  type="number"
                  min="0"
                  name={field.gteKey}
                  value={url.searchParams.get(field.gteKey) ?? ''}
                  placeholder="Min"
                />
                <input
                  class="v2-input"
                  type="number"
                  min="0"
                  name={field.lteKey}
                  value={url.searchParams.get(field.lteKey) ?? ''}
                  placeholder="Max"
                />
              </span>
            {:else if field.type === 'boolean'}
              <select class="v2-input" name={field.key}>
                <option value="">Any</option>
                <option value="true" selected={url.searchParams.get(field.key) === 'true'}
                  >Yes</option
                >
                <option value="false" selected={url.searchParams.get(field.key) === 'false'}
                  >No</option
                >
              </select>
            {:else if field.type === 'text'}
              <input
                class="v2-input"
                type="text"
                name={field.key}
                value={url.searchParams.get(field.key) ?? ''}
                placeholder="Any"
              />
            {:else}
              <select class="v2-input" name={field.key}>
                <option value="">Any</option>
                {#each optionsFor(field) as option (option.id)}
                  <option
                    value={option.id}
                    selected={url.searchParams.get(field.key) === option.id}
                  >
                    {option.name}
                  </option>
                {/each}
              </select>
            {/if}
          </label>
        {/each}

        <div class="v2-filter-actions">
          <button class="v2-btn v2-btn-primary v2-btn-sm" type="submit">Apply</button>
          <a class="v2-btn v2-btn-sm" href={resolve(asInternalPath(url.pathname))}>Clear all</a>
        </div>
      </form>
    </details>
  {/if}

  {#if meta}
    <span class="v2-sub" style="margin-left:auto">{meta}</span>
  {/if}
</div>

<style>
  .v2-view-menu,
  .v2-filter-menu {
    position: relative;
  }
  .v2-view-menu summary,
  .v2-filter-menu summary {
    list-style: none;
    cursor: pointer;
  }
  .v2-view-menu summary::-webkit-details-marker,
  .v2-filter-menu summary::-webkit-details-marker {
    display: none;
  }
  .v2-menu {
    position: absolute;
    top: calc(100% + 5px);
    left: 0;
    z-index: 20;
    min-width: 190px;
    padding: 5px;
    border: 1px solid var(--v2-line);
    border-radius: var(--v2-radius);
    background: var(--v2-card);
    box-shadow: 0 6px 18px rgb(0 0 0 / 8%);
  }
  .v2-menu-item {
    display: block;
    padding: 6px 9px;
    border-radius: 5px;
    color: inherit;
    font-size: 12.5px;
    text-decoration: none;
  }
  .v2-menu-item:hover {
    background: var(--v2-wash);
  }
  .v2-menu-item-on {
    font-weight: 600;
  }
  .v2-filter-form {
    display: flex;
    min-width: 260px;
    flex-direction: column;
    gap: 9px;
    padding: 11px;
  }
  .v2-filter-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .v2-filter-dates {
    display: flex;
    gap: 6px;
  }
  .v2-filter-actions {
    display: flex;
    gap: 6px;
    padding-top: 3px;
  }
  .v2-chip a {
    display: inline-flex;
    color: inherit;
  }

  /* The lists switch to cards below 768px and the header actions go full
     width, but an absolutely positioned menu does not: at 260px min-width it
     runs off a 320px viewport when its trigger sits mid-row. Anchoring it to
     the left edge and letting it use the full width keeps it on screen. */
  @media (max-width: 768px) {
    .v2-menu {
      position: fixed;
      left: 8px;
      right: 8px;
      min-width: 0;
    }
  }
</style>
