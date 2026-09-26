<script>
  import { resolve } from '$app/paths';
  import { asInternalPath } from '$lib/utils/paths.js';
  import '../../app.css';
  import '$lib/v2/styles/v2.css';
  import { page } from '$app/state';
  import { afterNavigate } from '$app/navigation';
  import Sidebar from '$lib/v2/components/Sidebar.svelte';
  import { isOrgAdmin } from '$lib/admin.js';
  import CommandPalette from '$lib/v2/components/CommandPalette.svelte';
  import { Search, Sun, Columns3, LifeBuoy, Receipt, Plus, Menu } from '@lucide/svelte';

  /** @type {{ data: { counts: Record<string, number>, org: { name: string, terminology?: Record<string, string> | null }, is_organization_admin: boolean }, children: import('svelte').Snippet }} */
  let { data, children } = $props();

  let paletteOpen = $state(false);

  // The sidebar is hidden below 768px, and the tab bar only carries four of the
  // ~16 destinations. This drawer is how a phone reaches the rest of the nav and
  // the footer: profile, notifications, help, sign out. It reuses the same
  // <Sidebar>, so the two can never drift apart. Closes itself on navigation.
  let menuOpen = $state(false);
  afterNavigate(() => (menuOpen = false));

  /** Focus the panel on open so Escape reaches it and keyboard users land inside. */
  function autofocus(/** @type {HTMLElement} */ node) {
    node.focus();
  }

  /**
   * The five things worth a thumb on a phone. Fewer than the sidebar on
   * purpose. A tab bar that scrolls is a menu wearing a tab bar's clothes.
   */
  const TABS = [
    { href: '/', label: 'Today', icon: Sun, exact: true },
    { href: '/pipeline', label: 'Pipeline', icon: Columns3 },
    { href: '/tickets', label: 'Tickets', icon: LifeBuoy },
    { href: '/invoices', label: 'Invoices', icon: Receipt }
  ];

  const isActive = (href, exact) =>
    exact ? page.url.pathname === href : page.url.pathname.startsWith(href);

  function onkeydown(e) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      paletteOpen = !paletteOpen;
    } else if (e.key === 'Escape' && menuOpen) {
      menuOpen = false;
    }
  }
</script>

<svelte:head>
  <title>BottleCRM v2</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<svelte:window {onkeydown} />

<div class="v2-root v2-shell">
  <Sidebar
    counts={data.counts}
    org={data.org}
    isAdmin={isOrgAdmin(data)}
    terminology={data.org.terminology}
    onsearch={() => (paletteOpen = true)}
  />
  <div class="v2-main">
    <!-- Phone top bar. The sidebar is hidden below 768px; this replaces the
         org mark and the search affordance it carried. -->
    <div class="v2-mobile-top">
      <button
        class="v2-btn v2-btn-quiet"
        type="button"
        onclick={() => (menuOpen = true)}
        aria-label="Open menu"
        aria-expanded={menuOpen}
      >
        <Menu />
      </button>
      <span class="v2-mark">{data.org.name.slice(0, 1)}</span>
      <h2>{data.org.name}</h2>
      <button
        class="v2-btn v2-btn-quiet"
        type="button"
        style="margin-left:auto"
        onclick={() => (paletteOpen = true)}
        aria-label="Search"
      >
        <Search />
      </button>
    </div>

    {@render children()}

    <nav class="v2-tabbar" aria-label="Sections">
      {#each TABS as tab (tab.href)}
        <a
          href={resolve(asInternalPath(tab.href))}
          aria-current={isActive(tab.href, tab.exact) ? 'page' : undefined}
        >
          <tab.icon />
          {tab.label}
        </a>
      {/each}
    </nav>
  </div>

  <!-- Both live inside .v2-root so they inherit the scoped tokens; both are
       position:fixed, so the shell's overflow:hidden does not clip them. -->
  <a class="v2-fab" href={resolve('/pipeline/new')} aria-label="New deal"><Plus size={21} /></a>

  <!-- Mobile navigation drawer. Only openable from the mobile top bar, so it
       never surfaces on desktop; a backdrop click, Escape, or navigating all
       close it. It renders the same <Sidebar> the desktop shows. -->
  {#if menuOpen}
    <div
      class="v2-drawer-scrim"
      role="presentation"
      onclick={(e) => {
        if (e.target === e.currentTarget) menuOpen = false;
      }}
    >
      <div
        class="v2-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Navigation"
        tabindex="-1"
        use:autofocus
        onkeydown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault();
            menuOpen = false;
          }
        }}
      >
        <Sidebar
          counts={data.counts}
          org={data.org}
          isAdmin={isOrgAdmin(data)}
          terminology={data.org.terminology}
          onsearch={() => {
            menuOpen = false;
            paletteOpen = true;
          }}
        />
      </div>
    </div>
  {/if}

  <CommandPalette open={paletteOpen} onclose={() => (paletteOpen = false)} />
</div>
