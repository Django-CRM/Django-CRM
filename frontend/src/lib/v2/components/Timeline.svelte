<script lang="ts">
  import { relativeTime } from '#lib/v2/format.js';
  import { enhance } from '$app/forms';
  import ConfirmAction from '#lib/v2/components/ConfirmAction.svelte';

  /**
   * Newest first. The filled dot marks the latest event only. Everything
   * else is a hairline, so the eye lands on "what happened last".
   *
   * An event with `canDelete` is a logged interaction the caller may remove,
   * through the page's `?/deleteActivity`.
   *
   * An event with `note` is a note on a record that edits and deletes by id,
   * through the page's `?/editNote` and `?/deleteNote`.
   */
  let {
    events = [],
    noteFailure = null,
    canWriteNotes = true
  }: {
    /** A failed edit or delete: shown beside the note it belongs to. */
    noteFailure?: { id: string; message: string } | null;
    /** False hides Edit (the plan no longer allows writes); Delete stays. */
    canWriteNotes?: boolean;
    events: Array<{
      id: string;
      body: string;
      at: string | null;
      by?: string | null;
      type?: string;
      detail?: string;
      canDelete?: boolean;
      interaction?: string;
      note?: { id: string; canEdit: boolean; canDelete: boolean };
    }>;
  } = $props();

  let editing = $state<string | null>(null);
  // Cancel hides the last failed edit's message; the next failure is a new object and shows.
  let dismissed = $state.raw<{ id: string; message: string } | null>(null);
  const failure = $derived(noteFailure === dismissed ? null : noteFailure);
</script>

<div class="v2-timeline">
  {#each events as e, i (e.id)}
    <div class="v2-event" class:latest={i === 0}>
      {#if e.note && editing === e.note.id}
        <form
          method="POST"
          action="?/editNote"
          class="edit"
          use:enhance={() =>
            async ({ result, update }) => {
              if (result.type === 'success') editing = null;
              await update({ reset: false });
            }}
        >
          <input type="hidden" name="note" value={e.note.id} />
          <textarea name="comment" rows="3" class="v2-input" maxlength={10000} required
            >{e.body}</textarea
          >
          {#if failure?.id === e.note.id}
            <p class="v2-error" role="alert">{failure.message}</p>
          {/if}
          <div class="row">
            <button class="v2-btn v2-btn-primary" type="submit">Save</button>
            <button
              class="v2-btn"
              type="button"
              onclick={() => {
                editing = null;
                dismissed = noteFailure;
              }}>Cancel</button
            >
          </div>
        </form>
      {:else}
        <div style="font-weight:500;white-space:pre-wrap;overflow-wrap:anywhere">{e.body}</div>
      {/if}
      {#if e.detail}<div class="detail">{e.detail}</div>{/if}
      <div class="v2-event-meta">{relativeTime(e.at) + (e.by ? ` · ${e.by}` : '')}</div>
      {#if e.note && editing !== e.note.id && failure?.id === e.note.id}
        <p class="v2-error" role="alert" style="margin:4px 0 0">{failure.message}</p>
      {/if}
      {#if e.note && editing !== e.note.id && ((e.note.canEdit && canWriteNotes) || e.note.canDelete)}
        <div class="row" style="margin-top:4px">
          {#if e.note.canEdit && canWriteNotes}
            <button
              class="v2-btn v2-btn-quiet"
              type="button"
              onclick={() => (editing = e.note?.id ?? null)}>Edit</button
            >
          {/if}
          {#if e.note.canDelete}
            <ConfirmAction
              action="?/deleteNote"
              hidden={{ note: e.note.id }}
              label="Delete"
              confirmLabel="Delete"
              explain="Deletes this note for good."
            />
          {/if}
        </div>
      {/if}
      {#if e.canDelete && e.interaction}
        <div style="margin-top:4px">
          <ConfirmAction
            action="?/deleteActivity"
            hidden={{ interaction: e.interaction }}
            label="Delete"
            confirmLabel="Delete for good"
            explain="Removes it from every timeline it is on."
          />
        </div>
      {/if}
    </div>
  {/each}
</div>

<style>
  .edit textarea {
    width: 100%;
    font-size: 13px;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    margin-top: 6px;
  }
  @media (max-width: 768px) {
    .row :global(.v2-btn) {
      min-height: 44px;
    }
  }
  .detail {
    font-size: 12.5px;
    line-height: 1.5;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    margin-top: 2px;
  }
</style>
