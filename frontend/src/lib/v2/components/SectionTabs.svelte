<script>
  import { resolve } from '$app/paths';
  import { asInternalPath } from '$lib/utils/paths.js';
  import { page } from '$app/state';
  import { TAB_SETS } from '$lib/v2/tabs.js';
  import { isOrgAdmin } from '$lib/admin.js';

  /**
   * Second-level navigation within one destination. Tickets has a queue, an
   * approval list and a measurement page, and all three are still "Tickets".
   *
   * This exists so the sidebar stays one level deep. A sidebar that expands
   * into sub-trees turns "where am I" into a puzzle.
   *
   * Counts come from the layout's shell data, so a badge here and the badge on
   * the sidebar entry above it can never disagree. A count of zero renders
   * nothing: a badge reading 0 is a badge you learn to stop reading.
   *
   * @type {{ set: keyof typeof TAB_SETS }}
   */
  let { set } = $props();

  let counts = $derived(page.data.counts ?? {});
  // The admin fact rides on the app layout data (server-derived from the JWT).
  // Drop admin-only tabs for a member so the strip does not offer a page that
  // only gate-cards them; the backend still enforces the gate, so this is UX only.
  let isAdmin = $derived(isOrgAdmin(page.data));
  let tabs = $derived((TAB_SETS[set] ?? []).filter((tab) => isAdmin || !tab.admin));

  const isActive = (href, exact) =>
    exact ? page.url.pathname === href : page.url.pathname.startsWith(href);
</script>

<nav class="v2-tabs" aria-label="Section">
  {#each tabs as tab (tab.href)}
    <a
      href={resolve(asInternalPath(tab.href))}
      aria-current={isActive(tab.href, tab.exact) ? 'page' : undefined}
    >
      {tab.label}
      {#if tab.count && counts[tab.count]}
        <span class="v2-tab-count v2-num">{counts[tab.count]}</span>
      {/if}
    </a>
  {/each}
</nav>
