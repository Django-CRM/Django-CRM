<script>
  import { enhance } from '$app/forms';
  import { CASE_STATUSES, CASE_PRIORITIES, CASE_TYPES } from '$lib/v2/enums.js';

  /**
   * @type {{
   *   ids: string[],
   *   people: {id:string,name:string}[],
   *   tags: {id:string,name:string}[],
   *   onclear: () => void,
   *   ondone: () => void
   * }}
   */
  let { ids, people, tags, onclear, ondone } = $props();

  // Which action the agent picked in the menu. '' means the menu is showing.
  let action = $state('');
  // The status select's live value, so the Close date field can appear the
  // moment "Closed" is picked, before Apply is pressed. Starts blank so
  // Apply cannot fire on an unmade choice.
  let statusValue = $state('');
  // Delete is a two-click confirm, armed by the first click. Kept separate
  // from `action` so leaving and re-entering the delete branch through Back
  // does not skip the confirm on the next attempt.
  let armed = $state(false);
  // Today, for the Close date default. yyyy-mm-dd.
  const today = new Date().toISOString().slice(0, 10);

  const ACTIONS = [
    { key: 'assigned_to', label: 'Reassign' },
    { key: 'priority', label: 'Set priority' },
    { key: 'status', label: 'Set status' },
    { key: 'case_type', label: 'Set type' },
    { key: 'tags', label: 'Add tags' },
    { key: 'delete', label: 'Delete' }
  ];

  function reset() {
    action = '';
    armed = false;
    ondone();
  }
</script>

<div class="v2-bulkbar" role="region" aria-label="Bulk actions">
  <span class="v2-num">{ids.length} selected</span>
  <button type="button" class="v2-btn" onclick={onclear}>Clear</button>

  {#if action === ''}
    <select class="v2-input" bind:value={action} aria-label="Choose a bulk action">
      <option value="">Actions</option>
      {#each ACTIONS as a (a.key)}
        <option value={a.key}>{a.label}</option>
      {/each}
    </select>
  {:else if action === 'delete'}
    <form
      method="POST"
      action="?/bulkDelete"
      use:enhance={() =>
        async ({ update }) => {
          await update({ reset: false });
          reset();
        }}
    >
      {#each ids as id (id)}<input type="hidden" name="ids" value={id} />{/each}
      {#if !armed}
        <button type="button" class="v2-btn" onclick={() => (armed = true)}>
          Delete {ids.length}
        </button>
      {:else}
        <button type="submit" class="v2-btn v2-btn-primary">Confirm</button>
        <button type="button" class="v2-btn" onclick={() => (armed = false)}>Cancel</button>
      {/if}
      <button type="button" class="v2-btn" onclick={() => ((action = ''), (armed = false))}>
        Back
      </button>
    </form>
  {:else}
    <form
      method="POST"
      action="?/bulkUpdate"
      use:enhance={() =>
        async ({ update }) => {
          await update({ reset: false });
          reset();
        }}
    >
      {#each ids as id (id)}<input type="hidden" name="ids" value={id} />{/each}
      <input type="hidden" name="field" value={action} />

      {#if action === 'assigned_to'}
        <select class="v2-input" name="value" required>
          <option value="">Choose a person</option>
          {#each people as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
        </select>
      {:else if action === 'tags'}
        <select class="v2-input" name="value" multiple required>
          {#each tags as t (t.id)}<option value={t.id}>{t.name}</option>{/each}
        </select>
      {:else if action === 'priority'}
        <select class="v2-input" name="value" required>
          <option value="">Choose a priority</option>
          {#each CASE_PRIORITIES as v (v)}<option value={v}>{v}</option>{/each}
        </select>
      {:else if action === 'case_type'}
        <select class="v2-input" name="value" required>
          <option value="">Choose a type</option>
          {#each CASE_TYPES as v (v)}<option value={v}>{v}</option>{/each}
        </select>
      {:else if action === 'status'}
        <select class="v2-input" name="value" bind:value={statusValue} required>
          <option value="">Choose a status</option>
          {#each CASE_STATUSES as v (v)}<option value={v}>{v}</option>{/each}
        </select>
        {#if statusValue === 'Closed'}
          <input class="v2-input" type="date" name="closed_on" value={today} required />
        {/if}
      {/if}

      <button type="submit" class="v2-btn v2-btn-primary">Apply</button>
      <button type="button" class="v2-btn" onclick={() => (action = '')}>Back</button>
    </form>
  {/if}
</div>

<style>
  .v2-bulkbar {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    padding: 10px 12px;
    border: 1px solid var(--v2-line);
    border-radius: 10px;
    background: var(--v2-card);
  }
  /* Phone: dock above the tab bar, full width, comfortable tap targets. */
  @media (max-width: 768px) {
    .v2-bulkbar {
      position: fixed;
      left: 8px;
      right: 8px;
      bottom: calc(var(--v2-tabbar-h, 56px) + 8px);
      z-index: 40;
      box-shadow: 0 2px 12px rgba(0, 0, 0, 0.12);
    }
    .v2-bulkbar :global(.v2-btn),
    .v2-bulkbar .v2-input {
      min-height: 44px;
    }
  }
</style>
