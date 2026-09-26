<script>
  /**
   * One segment per open stage of the deal's own pipeline. A closed deal (a
   * won or lost stage, whatever an admin named it) leaves the meter entirely
   * and becomes a line of text. A won deal is not "100% through a funnel", it
   * is done.
   *
   * `steps` are the pipeline's open stages in board order, `{code}` each. A
   * code the pipeline does not list still renders its name, with no segment
   * lit.
   *
   * @type {{ code: string, name: string, kind: string | null, steps: { code: string }[], label?: boolean }}
   */
  let { code, name, kind, steps, label = true } = $props();

  let closed = $derived(kind === 'won' || kind === 'lost');
  let index = $derived(steps.findIndex((s) => s.code === code));
</script>

{#if !closed}
  <div class="v2-meter" role="img" aria-label="Stage: {name}">
    {#each steps as step, i (step.code)}
      <i class={i <= index ? 'on' : ''}></i>
    {/each}
  </div>
  {#if label}
    <div class="v2-table-secondary" style="margin-top:4px">{name}</div>
  {/if}
{:else}
  <span class="v2-sub">{name}</span>
{/if}
