<script lang="ts">
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { asInternalPath } from '#lib/utils/paths.js';
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import FilterBar from '#lib/v2/components/FilterBar.svelte';
  import Avatar from '#lib/v2/components/Avatar.svelte';
  import { objectDescriptor } from '#lib/v2/object-filters.js';
  import { count } from '#lib/v2/format.js';
  import { canWriteObject } from '#lib/v2/custom-objects.js';
  import { Columns3, List, TriangleAlert } from '@lucide/svelte';
  import { flip } from 'svelte/animate';
  import { dndzone, type DndEvent } from 'svelte-dnd-action';
  import { refreshAll } from '$app/navigation';
  import { deserialize } from '$app/forms';
  import { acceptsDrops, cardControls, moveFields, moveTargets } from '#lib/v2/object-board.js';
  import type { PageData, PageProps } from './$types';

  type Lane = PageData['lanes'][number];
  type Card = Lane['rows'][number];
  type CardEvent = CustomEvent<DndEvent<Card>>;

  let { data }: PageProps = $props();

  const FLIP_MS = 160;

  let def = $derived(data.definition);
  let descriptor = $derived(objectDescriptor(def.fields));
  let writable = $derived(canWriteObject(def, data.customObjects?.has_custom_objects));
  let boardLanes: Lane[] = $state([]);
  $effect(() => {
    boardLanes = data.lanes.map((lane) => ({ ...lane, rows: [...lane.rows] }));
  });
  let total = $derived(data.lanes.reduce((n, lane) => n + lane.count, 0));

  let moveError = $state('');
  let busy = $state(false);

  /** Keeps svelte-dnd-action from starting a drag on a card the viewer may not move. */
  function lockCard(node: HTMLElement, locked: boolean) {
    const stop = (e: Event) => {
      // Only Enter and Space pick a card up, so other keys (Cmd+K, Escape) still reach the page.
      if (e instanceof KeyboardEvent && e.key !== 'Enter' && e.key !== ' ') return;
      e.stopImmediatePropagation();
    };
    const events = ['mousedown', 'touchstart', 'keydown'];
    const apply = (on: boolean) => {
      for (const name of events) {
        if (on) node.addEventListener(name, stop, { capture: true });
        else node.removeEventListener(name, stop, { capture: true });
      }
    };
    apply(locked);
    return {
      update(next: boolean) {
        apply(false);
        apply(next);
      },
      destroy: () => apply(false)
    };
  }

  const laneCount = (lane: Lane) => (lane.truncated ? lane.count : lane.rows.length);

  function onConsider(lane: Lane, e: CardEvent) {
    lane.rows = e.detail.items;
  }

  async function onFinalize(lane: Lane, e: CardEvent) {
    lane.rows = e.detail.items;
    const movedId = e.detail.info?.id;
    const index = e.detail.items.findIndex((r) => r.id === movedId);
    if (index === -1) return;
    // A lane that takes no drops has nowhere to store an order, so a card
    // reordered inside it goes back.
    if (!acceptsDrops(lane, data.required)) {
      await refreshAll();
      return;
    }
    await persistMove(movedId, lane, e.detail.items[index - 1]?.id, e.detail.items[index + 1]?.id);
  }

  async function persistMove(
    id: string,
    lane: Lane,
    aboveId: string | undefined,
    belowId: string | undefined
  ) {
    const body = new FormData();
    for (const [name, value] of Object.entries(moveFields(lane, id, aboveId, belowId))) {
      body.set(name, value);
    }
    try {
      const res = await fetch('?/move', { method: 'POST', body });
      const result = deserialize(await res.text());
      if (result.type === 'success') {
        moveError = '';
        const moved = result.data?.card as Card | undefined;
        if (moved) {
          for (const l of boardLanes) {
            const at = l.rows.findIndex((r) => r.id === moved.id);
            if (at !== -1) l.rows[at] = moved;
          }
        }
        return true;
      }
      const refusal = result.type === 'failure' ? result.data?.error : undefined;
      moveError =
        (typeof refusal === 'string' && refusal) || 'Could not move the record, so it went back.';
    } catch {
      moveError = 'Could not move the record, so it went back.';
    }
    await refreshAll();
    return false;
  }

  /** Dragging between lanes that snap one to a screen is not a phone gesture, so each card has a menu too. */
  async function moveBySelect(id: string, e: Event) {
    const select = e.currentTarget as HTMLSelectElement;
    const target = data.lanes.find((l) => l.id === select.value);
    if (!target) return;
    busy = true;
    const moved = await persistMove(id, target, undefined, undefined);
    if (moved) await refreshAll();
    else select.value = '';
    busy = false;
  }
</script>

<PageHeader title="{def.label_plural} board">
  {#snippet sub()}
    <span class="v2-num">{count(total)}</span> by {data.fieldLabel.toLowerCase()}
  {/snippet}
  {#snippet actions()}
    <a
      class="v2-btn v2-btn-quiet"
      href="{resolve(asInternalPath(`/objects/${def.key}`))}{page.url.search}"><List />List</a
    >
    <span class="v2-btn" aria-current="true"><Columns3 />Board</span>
  {/snippet}
</PageHeader>

<FilterBar
  page="objects"
  {descriptor}
  url={page.url}
  people={data.people}
  tags={data.tags}
  meId={data.meId}
/>

{#if moveError}
  <div class="v2-pad" style="padding-top:12px;flex:none">
    <div class="ob-error" role="status">
      <TriangleAlert size={13} style="flex:none" />
      <span>{moveError}</span>
    </div>
  </div>
{/if}

<p class="v2-sub v2-pad" style="font-size:11.5px;margin:10px 0 0;flex:none">
  {#if writable}
    Drag a card, or use "Move to" on it, to change its {data.fieldLabel.toLowerCase()}.
  {:else}
    This board is read-only.
  {/if}
</p>
<div class="v2-board" style="padding-top:12px">
  {#each boardLanes as lane (lane.id)}
    {@const open = acceptsDrops(lane, data.required)}
    <section class="v2-lane">
      <div class="v2-lane-head">
        <span class="v2-label">{lane.label}</span>
        <span class="v2-num">{count(laneCount(lane))}</span>
      </div>
      {#if lane.truncated}
        <p class="v2-sub" style="padding:0 2px 6px;font-size:11.5px">
          Showing the first <span class="v2-num">{lane.rows.length}</span>.
        </p>
      {/if}
      {#if lane.kind === 'other'}
        <p class="v2-sub" style="padding:0 2px 6px;font-size:11.5px">
          Values that are no longer choices. Move a card out to fix it.
        </p>
      {/if}
      <div
        class="v2-lane-body ob-body"
        use:dndzone={{
          items: lane.rows,
          flipDurationMs: FLIP_MS,
          dropFromOthersDisabled: !open,
          dragDisabled: busy
        }}
        onconsider={(e: CardEvent) => onConsider(lane, e)}
        onfinalize={(e: CardEvent) => onFinalize(lane, e)}
      >
        {#each lane.rows as card (card.id)}
          {@const controls = cardControls(card, writable)}
          <div
            class="v2-deal-card ob-card"
            class:ob-locked={!controls.drag}
            animate:flip={{ duration: FLIP_MS }}
            use:lockCard={!controls.drag}
          >
            <a
              href={resolve(asInternalPath(`/objects/${def.key}/${card.id}`))}
              style="font-weight:600;letter-spacing:-0.012em;line-height:1.3;color:inherit;text-decoration:none"
              >{card.name}</a
            >
            {#each card.cells as cell, i (i)}
              <div class="v2-sub" style="margin-top:2px">{cell}</div>
            {/each}
            {#if card.assignees.length > 0}
              <div class="v2-deal-card-foot">
                <Avatar name={card.assignees[0]} size={21} />
                {#if card.assignees.length > 1}
                  <span class="v2-sub">+{card.assignees.length - 1}</span>
                {/if}
              </div>
            {/if}
            {#if controls.moveSelect}
              <select
                class="v2-input ob-move"
                aria-label="Move {card.name} to"
                disabled={busy}
                onchange={(e) => moveBySelect(card.id, e)}
              >
                <option value="">Move to…</option>
                {#each moveTargets(data.lanes, data.required, lane.id) as target (target.id)}
                  <option value={target.id}>{target.label}</option>
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

<style>
  .ob-body {
    min-height: 44px;
  }
  .ob-card {
    cursor: grab;
  }
  /* svelte-dnd-action writes an inline cursor: grab on every card. */
  .ob-locked {
    cursor: default !important;
  }
  .ob-move {
    margin-top: 10px;
    width: 100%;
    min-height: 36px;
    font-size: 12.5px;
  }
  .ob-error {
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
    .ob-move {
      min-height: 44px;
    }
  }
</style>
