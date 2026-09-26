<script>
  /**
   * Picks which deal pipeline the page shows, written to `?pipeline=` like
   * every other view setting, so the choice is shareable and survives reload.
   *
   * A plain GET form: every other param in the URL rides along as a hidden
   * input, and the select submits itself on change. Without script the button
   * submits it instead.
   *
   * Rendered only when the org has more than one pipeline; with one there is
   * nothing to choose.
   *
   * @type {{ url: URL, pipelines: { id: string, name: string }[], current: string | null, allLabel?: string | null }}
   */
  let { url, pipelines, current, allLabel = null } = $props();
</script>

{#if pipelines.length > 1}
  <form class="switcher" method="GET">
    {#each [...url.searchParams] as [key, value] (key)}
      {#if key !== 'pipeline'}
        <input type="hidden" name={key} {value} />
      {/if}
    {/each}
    <label class="v2-label" for="pipeline-switch">Pipeline</label>
    <select
      id="pipeline-switch"
      name="pipeline"
      class="v2-input"
      value={current ?? ''}
      onchange={(e) => e.currentTarget.form?.requestSubmit()}
    >
      {#if allLabel}<option value="">{allLabel}</option>{/if}
      {#each pipelines as p (p.id)}
        <option value={p.id}>{p.name}</option>
      {/each}
    </select>
    <noscript><button class="v2-btn" type="submit">Show</button></noscript>
  </form>
{/if}

<style>
  .switcher {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 10px 0 0;
  }
  .switcher select {
    width: auto;
    min-width: 0;
    max-width: 100%;
  }
  @media (max-width: 768px) {
    .switcher select {
      flex: 1;
      min-height: 44px;
    }
  }
</style>
