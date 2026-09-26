<script>
  /**
   * The frame for an org's public help center.
   *
   * Not `PortalShell`, on purpose: that shell marks every page `noindex`,
   * because an invoice link is unguessable rather than public. A help center is
   * the opposite case. The org switched it on so that customers, and search
   * engines, can find the answers, so these pages must stay indexable.
   *
   * Otherwise the same idea: the v2 tokens scoped under `.v2-root`, one capped
   * column, and none of the CRM's furniture. A visitor here is somebody's
   * customer, not a user of the product.
   */
  import '$lib/v2/styles/v2.css';
  import { resolve } from '$app/paths';

  /** @type {{ name: string, slug: string, children: import('svelte').Snippet }} */
  let { name, slug, children } = $props();
</script>

<div class="v2-root hc">
  <header class="hc-top">
    <a class="hc-brand" href={resolve(`/help-center/${slug}`)}>
      {name || 'Help center'}
      <span class="hc-kind">Help center</span>
    </a>
  </header>
  <main class="hc-main">
    {@render children()}
  </main>
</div>

<style>
  .hc {
    min-height: 100vh;
    background: var(--v2-paper);
    color: var(--v2-ink);
    font-family: var(--v2-sans);
    font-size: var(--v2-fs);
  }
  .hc-top {
    border-bottom: 1px solid var(--v2-rule, #e5e7eb);
  }
  .hc-brand {
    display: flex;
    align-items: baseline;
    gap: 10px;
    flex-wrap: wrap;
    max-width: 720px;
    margin: 0 auto;
    padding: 14px 20px;
    min-height: 44px;
    box-sizing: border-box;
    font-weight: 600;
    font-size: 16px;
    color: inherit;
    text-decoration: none;
    overflow-wrap: anywhere;
  }
  .hc-kind {
    font-weight: 400;
    font-size: 13px;
    color: var(--v2-slate, #6b7280);
  }
  .hc-main {
    /* A document, not an application: one column, capped and centred. */
    max-width: 720px;
    margin: 0 auto;
    padding: 32px 20px 64px;
  }
  @media (max-width: 768px) {
    .hc-brand {
      padding: 12px 16px;
    }
    .hc-main {
      padding: 20px 16px 48px;
    }
  }
</style>
