<script>
  import { resolve } from '$app/paths';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import Avatar from '$lib/v2/components/Avatar.svelte';
  import EmptyState from '$lib/v2/components/EmptyState.svelte';
  import { count } from '$lib/v2/format.js';
  import { PRIORITY_TONE } from '$lib/v2/enums.js';
  import { Columns3, List, TriangleAlert } from '@lucide/svelte';
  import { flip } from 'svelte/animate';
  import { dndzone } from 'svelte-dnd-action';
  import { invalidateAll } from '$app/navigation';
  import { deserialize } from '$app/forms';

  /** @type {{ data: any }} */
  let { data } = $props();

  const FLIP_MS = 160;

  /* `dndzone` reorders the array it is handed, so the board renders from a
     local copy that is rebuilt whenever the server sends new lanes. That
     rebuild is what makes a refused move snap back. */
  let boardLanes = $state(/** @type {any[]} */ ([]));
  $effect(() => {
    boardLanes = data.lanes.map((/** @type {any} */ lane) => ({ ...lane, rows: [...lane.rows] }));
  });

  let moveError = $state('');
  let busy = $state(false);

  /* A capped lane keeps the server's total; otherwise the header follows the
     cards on screen, so an optimistic move updates it without a round-trip. */
  function laneCount(/** @type {any} */ lane) {
    return lane.truncated ? lane.count : lane.rows.length;
  }

  function onConsider(/** @type {any} */ lane, /** @type {any} */ e) {
    lane.rows = e.detail.items;
  }

  async function onFinalize(/** @type {any} */ lane, /** @type {any} */ e) {
    lane.rows = e.detail.items;
    const movedId = e.detail.info?.id;
    const index = e.detail.items.findIndex((/** @type {any} */ r) => r.id === movedId);
    // Finalize fires on both lanes of a cross-lane move; only the lane now
    // holding the card persists it.
    if (index === -1) return;
    await persistMove(
      movedId,
      lane.id,
      e.detail.items[index - 1]?.id,
      e.detail.items[index + 1]?.id
    );
  }

  /**
   * Both neighbours are sent when they exist; the server resolves them inside
   * the destination column and ignores any it cannot find there. A refusal
   * (WIP limit, the close gate, no write access) reloads the board, which puts
   * the card back where the server still has it.
   */
  async function persistMove(
    /** @type {string} */ id,
    /** @type {string} */ laneId,
    /** @type {string | undefined} */ aboveId,
    /** @type {string | undefined} */ belowId
  ) {
    const body = new FormData();
    body.set('id', id);
    body.set('lane', laneId);
    body.set('mode', data.mode);
    if (aboveId) body.set('above_id', aboveId);
    if (belowId) body.set('below_id', belowId);
    try {
      const res = await fetch('?/move', { method: 'POST', body });
      const result = deserialize(await res.text());
      if (result.type === 'success') {
        moveError = '';
        return true;
      }
      moveError =
        (result.type === 'failure' && /** @type {any} */ (result.data)?.error) ||
        'Could not move the ticket, so it went back.';
    } catch {
      moveError = 'Could not move the ticket, so it went back.';
    }
    await invalidateAll();
    return false;
  }

  /**
   * The "Move to" select on each card. Dragging between lanes that snap one
   * to a screen is not a gesture anyone lands on a phone, and a keyboard
   * cannot drag at all, so every card also carries this. It appends to the
   * column, then reloads so the card shows where the server put it.
   */
  async function moveBySelect(/** @type {string} */ id, /** @type {Event} */ e) {
    const select = /** @type {HTMLSelectElement} */ (e.currentTarget);
    const laneId = select.value;
    if (!laneId) return;
    busy = true;
    const moved = await persistMove(id, laneId, undefined, undefined);
    if (moved) await invalidateAll();
    busy = false;
  }
</script>

<PageHeader title={data.pipeline ? data.pipeline.name : 'Tickets board'}>
  {#snippet sub()}
    <span class="v2-num">{count(data.total)}</span>
    {data.pipeline ? 'in this pipeline' : 'tickets by status'}
  {/snippet}
  {#snippet actions()}
    {#if data.pipelines.length > 0}
      <!-- A GET form: choosing navigates to `?pipeline=<id>` (empty for "By
           status") and re-runs load. Same control as the lead board picker. -->
      <form method="GET" class="tb-picker-form">
        <select
          name="pipeline"
          class="v2-btn tb-picker"
          aria-label="Board columns"
          value={data.pipeline?.id ?? ''}
          onchange={(e) => e.currentTarget.form?.requestSubmit()}
        >
          <option value="">By status</option>
          {#each data.pipelines as p (p.id)}
            <option value={p.id}>{p.name}</option>
          {/each}
        </select>
      </form>
    {/if}
    <a class="v2-btn v2-btn-quiet" href={resolve('/tickets')}><List />List</a>
    <span class="v2-btn" aria-current="true"><Columns3 />Board</span>
  {/snippet}
</PageHeader>

{#if moveError}
  <div class="v2-pad" style="padding-top:12px;flex:none">
    <div class="tb-move-error" role="status">
      <TriangleAlert size={13} style="flex:none" />
      <span>{moveError}</span>
    </div>
  </div>
{/if}

{#if data.lanes.length === 0}
  <div class="v2-scroll">
    <EmptyState
      title="This pipeline has no stages yet"
      body="There is nowhere on it to move a ticket. An admin can add stages to the pipeline, or you can go back to the board by status."
    >
      {#snippet icon()}<Columns3 size={21} />{/snippet}
      {#snippet actions()}
        <a class="v2-btn" href={resolve('/tickets/board')}>By status</a>
        <a class="v2-btn" href={resolve('/tickets')}>Back to the list</a>
      {/snippet}
    </EmptyState>
  </div>
{:else}
  <p class="v2-sub v2-pad" style="font-size:11.5px;margin:10px 0 0;flex:none">
    Drag a card, or use "Move to" on it, to change its {data.pipeline ? 'stage' : 'status'}. Moving
    a ticket to Closed closes it, so an approval rule can refuse it.
  </p>
  <div class="v2-board" style="padding-top:12px">
    {#each boardLanes as lane (lane.id)}
      <section class="v2-lane">
        <div class="v2-lane-head">
          <i class="tb-dot" style="background:{lane.color}" aria-hidden="true"></i>
          <span class="v2-label">{lane.name}</span>
          <span class="v2-num"
            >{count(laneCount(lane))}{lane.wipLimit ? ` / ${lane.wipLimit}` : ''}</span
          >
        </div>
        {#if lane.truncated}
          <p class="v2-sub" style="padding:0 2px 6px;font-size:11.5px">
            Showing the first <span class="v2-num">{lane.rows.length}</span>.
          </p>
        {/if}
        <div
          class="v2-lane-body tb-lane-body"
          use:dndzone={{ items: lane.rows, flipDurationMs: FLIP_MS, dragDisabled: busy }}
          onconsider={(e) => onConsider(lane, e)}
          onfinalize={(e) => onFinalize(lane, e)}
        >
          {#each lane.rows as ticket (ticket.id)}
            <div class="v2-deal-card tb-card" animate:flip={{ duration: FLIP_MS }}>
              <a class="tb-name" href={resolve(`/tickets/${ticket.id}`)}>{ticket.name}</a>
              {#if ticket.account}<div class="v2-sub" style="margin-top:2px">
                  {ticket.account}
                </div>{/if}
              <div class="v2-deal-card-foot">
                {#if ticket.owner}<Avatar name={ticket.owner} size={21} />{/if}
                {#if ticket.priority}
                  <Pill tone={/** @type {any} */ (PRIORITY_TONE)[ticket.priority] ?? 'slate'}
                    >{ticket.priority}</Pill
                  >
                {/if}
                {#if ticket.slaBreached}
                  <Pill tone="rust" dot>SLA breached</Pill>
                {:else if ticket.slaAtRisk}
                  <Pill tone="clay" dot>SLA at risk</Pill>
                {/if}
              </div>
              {#if boardLanes.length > 1}
                <select
                  class="v2-input tb-move"
                  aria-label="Move {ticket.name} to"
                  disabled={busy}
                  onchange={(e) => moveBySelect(ticket.id, e)}
                >
                  <option value="">Move to…</option>
                  {#each data.lanes as target (target.id)}
                    {#if target.id !== lane.id}
                      <option value={target.id}>{target.name}</option>
                    {/if}
                  {/each}
                </select>
              {/if}
            </div>
          {:else}
            <p class="v2-sub" style="padding:10px 2px;font-size:12px">Nothing here.</p>
          {/each}
        </div>
      </section>
    {/each}
  </div>
{/if}

<style>
  .tb-picker-form {
    display: contents;
  }
  .tb-picker {
    padding-right: 8px;
    cursor: pointer;
    max-width: 16rem;
  }
  .tb-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex: none;
  }
  /* An empty column still has to be a drop target. */
  .tb-lane-body {
    min-height: 44px;
  }
  .tb-card {
    cursor: grab;
  }
  .tb-name {
    display: block;
    font-weight: 600;
    letter-spacing: -0.012em;
    line-height: 1.3;
    color: inherit;
    text-decoration: none;
  }
  .tb-move {
    margin-top: 10px;
    width: 100%;
    min-height: 36px;
    font-size: 12.5px;
  }
  .tb-move-error {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--v2-rust);
    background: color-mix(in srgb, var(--v2-rust) 8%, transparent);
    border: 1px solid color-mix(in srgb, var(--v2-rust) 24%, transparent);
    border-radius: var(--v2-radius);
    padding: 7px 10px;
  }
  @media (max-width: 768px) {
    .tb-move {
      min-height: 44px;
    }
    /* The title is the card's link, so on a phone it gets a thumb-sized row. */
    .tb-name {
      display: flex;
      align-items: center;
      min-height: 44px;
    }
  }
</style>
