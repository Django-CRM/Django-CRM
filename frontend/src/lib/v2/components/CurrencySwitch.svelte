<script>
  /**
   * Which currency a page's money is shown in. There are no exchange rates,
   * so figures in different currencies are never combined: a page with more
   * than one gets these buttons, and a page with one renders nothing here.
   *
   * @type {{ currencies: string[], current: string, onpick: (code: string) => void }}
   */
  let { currencies, current, onpick } = $props();
</script>

{#if currencies.length > 1}
  <div class="cur-switch" role="group" aria-label="Currency">
    {#each currencies as code (code)}
      <button
        type="button"
        class="v2-view"
        aria-pressed={code === current}
        onclick={() => onpick(code)}>{code}</button
      >
    {/each}
    <span class="v2-sub"
      >Each currency on its own; there are no exchange rates to combine them.</span
    >
  </div>
{/if}

<style>
  .cur-switch {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 7px;
    margin-bottom: 14px;
  }
  .cur-switch button[aria-pressed='true'] {
    background: var(--v2-ink);
    border-color: var(--v2-ink);
    color: var(--v2-card);
  }
  @media (max-width: 768px) {
    .cur-switch button {
      min-width: 44px;
      min-height: 44px;
      justify-content: center;
    }
  }
</style>
