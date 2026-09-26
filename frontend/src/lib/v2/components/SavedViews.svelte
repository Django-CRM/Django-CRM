<script>
  /**
   * Saved views (G29): save the list's current filters under a name, open one
   * with a tap, rename or delete it. One menu, the same on all six lists that
   * take one; `FilterBar` renders it when the page passes `saved`.
   *
   * Opening a view is a plain link to the page with the view's filters, so it
   * works like every other filter in the bar: in the URL, shareable, undone by
   * the back button. Save, rename and delete post to the page's form actions
   * (`$lib/server/v2/saved-views.js`), which return to this same URL.
   *
   * @type {{
   *   saved: { spec: import('$lib/v2/saved-views.js').Spec, views: { id: string, name: string, filters: Record<string, string[]> }[], limit: number | null },
   *   url: URL
   * }}
   */
  import { enhance } from '$app/forms';
  import { page } from '$app/state';
  import { resolve } from '$app/paths';
  import { Bookmark, ChevronDown, Pencil } from '@lucide/svelte';
  import { asInternalPath } from '$lib/utils/paths.js';
  import ConfirmAction from '$lib/v2/components/ConfirmAction.svelte';
  import { droppedValues, isShowing, viewHref } from '$lib/v2/saved-views.js';

  let { saved, url } = $props();

  /** @type {string | null} */
  let renaming = $state(null);
  let busy = $state(false);

  let views = $derived(saved.views ?? []);
  let active = $derived(views.find((v) => isShowing(url.searchParams, v.filters, saved.spec)));
  let full = $derived(saved.limit !== null && views.length >= saved.limit);
  let error = $derived(/** @type {any} */ (page.form)?.savedViewError ?? null);
  let query = $derived(url.searchParams.toString());

  /** Busy while posting; the form resets and the rename closes on success. */
  const submit = () => {
    busy = true;
    return async (/** @type {any} */ { update, result }) => {
      await update();
      busy = false;
      if (result.type === 'redirect' || result.type === 'success') renaming = null;
    };
  };
</script>

<details class="v2-saved">
  <summary class="v2-view">
    <Bookmark size={13} style="color:var(--v2-slate)" />
    <span class="v2-saved-label">{active ? active.name : 'Saved views'}</span>
    <ChevronDown size={13} style="color:var(--v2-slate)" />
  </summary>
  <div class="v2-saved-menu">
    {#if error}
      <p class="v2-saved-error" role="alert">{error}</p>
    {/if}

    {#if views.length === 0}
      <p class="v2-sub v2-saved-empty">No saved views yet. Filter the list, then save it here.</p>
    {:else}
      <ul class="v2-saved-list">
        {#each views as view (view.id)}
          {@const dropped = droppedValues(view.filters, saved.spec)}
          <li class="v2-saved-row">
            {#if renaming === view.id}
              <form class="v2-saved-form" method="POST" action="?/renameView" use:enhance={submit}>
                <input type="hidden" name="id" value={view.id} />
                <input type="hidden" name="query" value={query} />
                <input
                  class="v2-input"
                  name="name"
                  value={view.name}
                  maxlength="100"
                  required
                  aria-label="New name for {view.name}"
                />
                <span class="v2-saved-actions">
                  <button class="v2-btn v2-btn-sm v2-btn-primary" type="submit" disabled={busy}>
                    Rename
                  </button>
                  <button class="v2-btn v2-btn-sm" type="button" onclick={() => (renaming = null)}>
                    Cancel
                  </button>
                </span>
              </form>
            {:else}
              <a
                class="v2-saved-open"
                class:v2-menu-item-on={view.id === active?.id}
                href={resolve(asInternalPath(viewHref(url, view.filters, saved.spec)))}
              >
                {view.name}
                {#if dropped > 0}
                  <!-- Counted in values, not keys: three statuses where this
                       page reads one is two it leaves out. -->
                  <span class="v2-sub v2-saved-note">
                    {dropped === 1 ? '1 filter value' : `${dropped} filter values`} this page cannot apply
                  </span>
                {/if}
              </a>
              <span class="v2-saved-actions">
                <button
                  class="v2-btn v2-btn-sm"
                  type="button"
                  aria-label="Rename {view.name}"
                  onclick={() => (renaming = view.id)}
                >
                  <Pencil size={13} />
                </button>
                <ConfirmAction
                  action="?/deleteView"
                  label="Delete"
                  confirmLabel="Delete view"
                  hidden={{ id: view.id, query }}
                />
              </span>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}

    {#if full}
      <p class="v2-sub v2-saved-empty">
        This list keeps at most {saved.limit} saved views. Delete one to save another.
      </p>
    {:else}
      <form class="v2-saved-form" method="POST" action="?/saveView" use:enhance={submit}>
        <label class="v2-saved-field">
          <span class="v2-label">Save the current filters as</span>
          <input class="v2-input" name="name" maxlength="100" required placeholder="View name" />
        </label>
        <input type="hidden" name="query" value={query} />
        <button class="v2-btn v2-btn-sm v2-btn-primary" type="submit" disabled={busy}>
          Save view
        </button>
      </form>
    {/if}
  </div>
</details>

<style>
  .v2-saved {
    position: relative;
  }
  .v2-saved summary {
    list-style: none;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .v2-saved summary::-webkit-details-marker {
    display: none;
  }
  .v2-saved-label {
    max-width: 180px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .v2-saved-menu {
    position: absolute;
    top: calc(100% + 5px);
    left: 0;
    z-index: 20;
    display: flex;
    width: 300px;
    flex-direction: column;
    gap: 9px;
    padding: 9px;
    border: 1px solid var(--v2-line);
    border-radius: var(--v2-radius);
    background: var(--v2-card);
    box-shadow: 0 6px 18px rgb(0 0 0 / 8%);
  }
  .v2-saved-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .v2-saved-row {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .v2-saved-open {
    display: flex;
    min-width: 0;
    flex: 1;
    flex-direction: column;
    padding: 6px 9px;
    border-radius: 5px;
    color: inherit;
    font-size: 12.5px;
    text-decoration: none;
    overflow-wrap: anywhere;
  }
  .v2-saved-open:hover {
    background: var(--v2-wash);
  }
  .v2-menu-item-on {
    font-weight: 600;
  }
  .v2-saved-note {
    font-size: 11px;
    font-weight: 400;
  }
  .v2-saved-actions {
    display: inline-flex;
    flex-shrink: 0;
    align-items: center;
    gap: 4px;
  }
  .v2-saved-form {
    display: flex;
    width: 100%;
    flex-direction: column;
    gap: 6px;
  }
  .v2-saved-field {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .v2-saved-empty {
    margin: 0;
    font-size: 12px;
  }
  .v2-saved-error {
    margin: 0;
    color: var(--v2-rust);
    font-size: 12px;
  }

  /* Same move as FilterBar's menus: below the phone breakpoint an absolutely
     placed 300px menu runs off a 390px screen when its trigger sits mid-row,
     so it is pinned to both edges instead, and every control in it gets a
     fingertip-sized target. */
  @media (max-width: 768px) {
    .v2-saved-menu {
      position: fixed;
      top: auto;
      bottom: 60px;
      left: 8px;
      right: 8px;
      z-index: 40;
      width: auto;
      max-height: calc(100dvh - 120px);
      overflow-y: auto;
    }
    .v2-saved-open,
    .v2-saved-actions :global(.v2-btn),
    .v2-saved-form :global(.v2-btn),
    .v2-saved-form :global(input) {
      min-height: 44px;
    }
    .v2-saved-actions :global(.v2-btn) {
      min-width: 44px;
    }
    .v2-saved-row {
      flex-wrap: wrap;
    }
  }
</style>
