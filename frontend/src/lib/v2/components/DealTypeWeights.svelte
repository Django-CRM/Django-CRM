<script>
  /**
   * Optional per-deal-type multipliers for a revenue or deals-closed goal.
   *
   * A rep carrying a renewal book and a rep opening new logos are not doing the
   * same work for the same number, so a goal can weigh each deal type. A blank
   * box means "count this type at full value", which is why nothing is
   * pre-filled with 1: an untouched form has to store an empty map, or every
   * goal in the org would grow five redundant weights the first time anyone
   * opened its edit page.
   *
   * ACTIVITIES goals have no deal type at all, so the whole section disappears
   * for one and the inputs are removed from the DOM rather than hidden, since a
   * disabled-but-present input still submits its name on some browsers and the
   * backend refuses weights on an activities goal with a 400.
   *
   * Validation here is a hint. `SalesGoalCreateSerializer.validate_type_weights`
   * is what actually refuses an unknown type, a negative number or a word.
   */
  import { untrack } from 'svelte';
  import { DEAL_TYPE_LABEL } from '$lib/v2/enums.js';

  /** @type {{ weights?: Record<string, any>, goalType: string }} */
  let { weights = {}, goalType } = $props();

  // Open when the goal already carries weights, so an existing weighting is
  // visible rather than hidden behind a control nobody thought to click.
  // `untrack` because this is the initial state only: reopening the section
  // every time a weight changes would fight whoever collapsed it.
  let open = $state(untrack(() => Object.keys(weights ?? {}).length > 0));

  /** Current value for one type, as the string an input binds to. */
  const valueFor = (type) => {
    const w = weights?.[type];
    return w === undefined || w === null ? '' : String(w);
  };

  let weighted = $derived(Object.entries(weights ?? {}).filter(([, w]) => Number(w) !== 1).length);
</script>

{#if goalType !== 'ACTIVITIES'}
  <div class="v2-field">
    <button type="button" class="toggle" onclick={() => (open = !open)} aria-expanded={open}>
      <span>Weight by deal type</span>
      <span class="v2-sub" style="font-size:11.5px">
        {#if weighted}
          {weighted} adjusted
        {:else}
          Optional
        {/if}
        · {open ? 'Hide' : 'Show'}
      </span>
    </button>

    <!-- Collapsing hides the inputs, it does not remove them. The edit action
         sends the weight map on every save so a cleared box actually clears a
         weight, which means an input that left the DOM would read as cleared:
         collapsing the section would silently wipe the goal's weighting. -->
    <div class="weights" hidden={!open}>
      {#each Object.entries(DEAL_TYPE_LABEL) as [type, label] (type)}
        <div class="row">
          <label for="w-{type}">{label}</label>
          <input
            id="w-{type}"
            name="weight_{type}"
            class="v2-input v2-num"
            type="text"
            inputmode="decimal"
            value={valueFor(type)}
            placeholder="1"
          />
        </div>
      {/each}
    </div>
    {#if open}
      <p class="v2-hint">
        A multiplier on each closed-won deal of that type. Leave a box empty to count that type in
        full. At 0.5 a 20,000 renewal counts as 10,000; at 0 it does not count at all.
      </p>
    {/if}
  </div>
{/if}

<style>
  .toggle {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    width: 100%;
    /* 44px so the row is a real tap target on a phone, where this sits between
       two other controls and is easy to miss by a few pixels. */
    min-height: 44px;
    padding: 0;
    background: none;
    border: none;
    font: inherit;
    font-weight: 550;
    color: var(--v2-ink);
    cursor: pointer;
    text-align: left;
  }

  .weights {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px 14px;
    margin-top: 4px;
  }

  .weights[hidden] {
    display: none;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .row label {
    flex: 1;
    min-width: 0;
    font-size: 12.5px;
    color: var(--v2-slate);
  }

  .row input {
    width: 78px;
    flex: none;
    text-align: right;
  }

  @media (max-width: 768px) {
    .weights {
      grid-template-columns: 1fr;
    }
  }
</style>
