<script>
  import { resolve } from '$app/paths';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import Avatar from '$lib/v2/components/Avatar.svelte';
  import EmptyState from '$lib/v2/components/EmptyState.svelte';
  import { count } from '$lib/v2/format.js';
  import { Columns3, List, TriangleAlert } from '@lucide/svelte';
  import { flip } from 'svelte/animate';
  import { dndzone } from 'svelte-dnd-action';
  import { invalidateAll } from '$app/navigation';
  import { deserialize } from '$app/forms';
  import { t } from '$lib/terminology.js';

  /** @type {{ data: any }} */
  let { data } = $props();

  const FLIP_MS = 160;

  /** HOT/WARM/COLD, the three ratings a lead can carry. */
  const RATING_TONE = /** @type {Record<string, 'rust'|'clay'|'slate'>} */ ({
    HOT: 'rust',
    WARM: 'clay',
    COLD: 'slate'
  });

  /* A vertical pack renames the module ("Enquiries"), and the list page
     already follows it; a board headed "Leads" beside it reads as a bug. */
  let plural = $derived(t(data.org?.terminology, 'lead.plural', 'Leads'));

  /* `dndzone` reorders the array it is handed, so the board renders from a
     local copy that is rebuilt whenever the server sends new lanes. That
     rebuild is what makes a refused move snap back. */
  let boardLanes = $state(/** @type {any[]} */ ([]));
  $effect(() => {
    boardLanes = data.lanes.map((/** @type {any} */ lane) => ({ ...lane, rows: [...lane.rows] }));
  });

  /** The stages a card can be sent to: every lane except "No stage". */
  let stages = $derived(data.lanes.filter((/** @type {any} */ lane) => !lane.unstaged));
  let inPipeline = $derived(
    stages.reduce((/** @type {number} */ n, /** @type {any} */ lane) => n + lane.count, 0)
  );
  let unstagedLane = $derived(data.lanes.find((/** @type {any} */ lane) => lane.unstaged));
  let unstagedCount = $derived(unstagedLane?.count ?? 0);

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
    // A reorder inside "No stage" has no stage to store the order against, so
    // the cards go back rather than pretend it saved. A card dropped in from a
    // stage is a real move: it leaves the pipeline.
    if (lane.unstaged && unstagedLane?.rows.some((/** @type {any} */ r) => r.id === movedId)) {
      await invalidateAll();
      return;
    }
    await persistMove(
      movedId,
      lane.id,
      e.detail.items[index - 1]?.id,
      e.detail.items[index + 1]?.id
    );
  }

  /**
   * Both neighbours are sent when they exist; the server resolves them inside
   * the destination stage and ignores any it cannot find there.
   */
  async function persistMove(
    /** @type {string} */ id,
    /** @type {string} */ stageId,
    /** @type {string | undefined} */ aboveId,
    /** @type {string | undefined} */ belowId
  ) {
    const body = new FormData();
    body.set('id', id);
    body.set('stage_id', stageId);
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
        'Could not move the lead, so it went back.';
    } catch {
      moveError = 'Could not move the lead, so it went back.';
    }
    await invalidateAll();
    return false;
  }

  /**
   * The "Move to" select on each card. Dragging between lanes that snap one
   * to a screen is not a gesture anyone lands on a phone, and a keyboard
   * cannot drag at all, so every card also carries this. It appends to the
   * stage, then reloads so the card shows where the server put it.
   */
  async function moveBySelect(/** @type {string} */ id, /** @type {Event} */ e) {
    const select = /** @type {HTMLSelectElement} */ (e.currentTarget);
    const stageId = select.value;
    if (!stageId) return;
    busy = true;
    const moved = await persistMove(id, stageId, undefined, undefined);
    if (moved) await invalidateAll();
    busy = false;
  }
</script>

<PageHeader title={data.pipeline ? data.pipeline.name : `${plural} board`}>
  {#snippet sub()}
    {#if data.pipeline}
      <span class="v2-num">{count(inPipeline)}</span> in this pipeline ·
      <span class="v2-num">{count(unstagedCount)}</span> in no stage
    {:else}
      {plural} by pipeline stage
    {/if}
  {/snippet}
  {#snippet actions()}
    {#if data.pipelines.length > 1}
      <!-- A GET form: choosing a pipeline navigates to `?pipeline=<id>` and
           re-runs load. Same control as the tasks board picker. -->
      <form method="GET" class="lb-picker-form">
        <select
          name="pipeline"
          class="v2-btn lb-picker"
          aria-label="Pipeline"
          value={data.pipeline?.id}
          onchange={(e) => e.currentTarget.form?.requestSubmit()}
        >
          {#each data.pipelines as p (p.id)}
            <option value={p.id}>{p.name}</option>
          {/each}
        </select>
      </form>
    {/if}
    <a class="v2-btn v2-btn-quiet" href={resolve('/leads')}><List />List</a>
    <span class="v2-btn" aria-current="true"><Columns3 />Board</span>
  {/snippet}
</PageHeader>

{#if moveError}
  <div class="v2-pad" style="padding-top:12px;flex:none">
    <div class="lb-move-error" role="status">
      <TriangleAlert size={13} style="flex:none" />
      <span>{moveError}</span>
    </div>
  </div>
{/if}

{#if !data.pipeline}
  <div class="v2-scroll">
    <EmptyState
      title="No pipelines yet"
      body="A pipeline sorts {plural.toLowerCase()} into stages you move them through. An admin can create one under lead pipelines in settings, or by applying an industry pack."
    >
      {#snippet icon()}<Columns3 size={21} />{/snippet}
      {#snippet actions()}
        <a class="v2-btn" href={resolve('/settings/lead-pipelines')}>Lead pipelines</a>
        <a class="v2-btn" href={resolve('/leads')}>Back to the list</a>
      {/snippet}
    </EmptyState>
  </div>
{:else}
  <p class="v2-sub v2-pad" style="font-size:11.5px;margin:10px 0 0;flex:none">
    {#if stages.length === 0}
      This pipeline has no stages yet, so there is nowhere to move a lead.
    {:else}
      Drag a card, or use "Move to" on it, to change its stage. A lead in no stage joins this
      pipeline when you move it into one, and leaves it when you move it back to No stage.
    {/if}
  </p>
  <div class="v2-board" style="padding-top:12px">
    {#each boardLanes as lane (lane.id)}
      <section class="v2-lane">
        <div class="v2-lane-head">
          <i class="lb-dot" style="background:{lane.color}" aria-hidden="true"></i>
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
          class="v2-lane-body lb-lane-body"
          use:dndzone={{
            items: lane.rows,
            flipDurationMs: FLIP_MS,
            dragDisabled: busy || stages.length === 0
          }}
          onconsider={(e) => onConsider(lane, e)}
          onfinalize={(e) => onFinalize(lane, e)}
        >
          {#each lane.rows as lead (lead.id)}
            <div class="v2-deal-card lb-card" animate:flip={{ duration: FLIP_MS }}>
              <a
                href={resolve(`/leads/${lead.id}`)}
                style="font-weight:600;letter-spacing:-0.012em;line-height:1.3;color:inherit;text-decoration:none"
                >{lead.name}</a
              >
              {#if lead.company}<div class="v2-sub" style="margin-top:2px">{lead.company}</div>{/if}
              <div class="v2-deal-card-foot">
                {#if lead.owner}<Avatar name={lead.owner} size={21} />{/if}
                {#if lead.rating}
                  <Pill tone={RATING_TONE[lead.rating] ?? 'slate'}>{lead.rating}</Pill>
                {/if}
                {#if lead.overdue}<Pill tone="rust" dot>Follow-up due</Pill>{/if}
              </div>
              {#if stages.length > 0}
                <select
                  class="v2-input lb-move"
                  aria-label="Move {lead.name} to stage"
                  disabled={busy}
                  onchange={(e) => moveBySelect(lead.id, e)}
                >
                  <option value="">Move to…</option>
                  {#each stages as stage (stage.id)}
                    {#if stage.id !== lane.id}
                      <option value={stage.id}>{stage.name}</option>
                    {/if}
                  {/each}
                  {#if unstagedLane && !lane.unstaged}
                    <option value={unstagedLane.id}>{unstagedLane.name}</option>
                  {/if}
                </select>
              {/if}
            </div>
          {:else}
            <p class="v2-sub" style="padding:10px 2px;font-size:12px">Nothing in this stage.</p>
          {/each}
        </div>
      </section>
    {/each}
  </div>
{/if}

<style>
  .lb-picker-form {
    display: contents;
  }
  .lb-picker {
    padding-right: 8px;
    cursor: pointer;
    max-width: 16rem;
  }
  .lb-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex: none;
  }
  /* An empty stage still has to be a drop target. */
  .lb-lane-body {
    min-height: 44px;
  }
  .lb-card {
    cursor: grab;
  }
  .lb-move {
    margin-top: 10px;
    width: 100%;
    min-height: 36px;
    font-size: 12.5px;
  }
  .lb-move-error {
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
    .lb-move {
      min-height: 44px;
    }
  }
</style>
