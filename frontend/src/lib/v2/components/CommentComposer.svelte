<script lang="ts">
  import { enhance } from '$app/forms';
  import { Paperclip, X } from '@lucide/svelte';

  /**
   * Add a comment, a file, or both, to the record on this page through its
   * `?/comment` action (`commentAction`, `#lib/server/v2/record-notes.js`).
   * A failed save keeps the words and the file; a saved one clears both.
   */
  let {
    error = null,
    placeholder = 'Write a comment…',
    files = true,
    maxlength = undefined
  }: {
    error?: string | null;
    placeholder?: string;
    files?: boolean;
    maxlength?: number;
  } = $props();

  let text = $state('');
  let fileName = $state('');
  let fileInput: HTMLInputElement | undefined = $state();
  let saving = $state(false);

  function pickFile(e: Event) {
    fileName = (e.currentTarget as HTMLInputElement).files?.[0]?.name ?? '';
  }
  function clearFile() {
    if (fileInput) fileInput.value = '';
    fileName = '';
  }
</script>

<form
  method="POST"
  action="?/comment"
  enctype="multipart/form-data"
  class="cc"
  use:enhance={() => {
    saving = true;
    return async ({ result, update }) => {
      saving = false;
      if (result.type === 'success') {
        text = '';
        clearFile();
      }
      await update({ reset: false });
    };
  }}
>
  <textarea name="comment" rows="2" bind:value={text} {maxlength} class="cc-input" {placeholder}
  ></textarea>
  <div class="cc-actions">
    <button
      class="v2-btn v2-btn-primary"
      type="submit"
      disabled={saving || (!text.trim() && !fileName)}
    >
      {saving ? 'Saving…' : 'Add'}
    </button>
    {#if files}
      <label class="v2-btn cc-attach" class:has-file={fileName}>
        <Paperclip size={14} />
        <span class="cc-file">{fileName || 'Attach file'}</span>
        <input bind:this={fileInput} type="file" name="attachment" onchange={pickFile} hidden />
      </label>
      {#if fileName}
        <button
          type="button"
          class="v2-btn-quiet cc-clear"
          onclick={clearFile}
          title="Remove file"
          aria-label="Remove file"
        >
          <X size={13} />
        </button>
      {/if}
    {/if}
    {#if error}
      <span class="cc-err" role="alert">{error}</span>
    {/if}
  </div>
</form>

<style>
  .cc {
    margin-bottom: 16px;
  }
  .cc-input {
    width: 100%;
    padding: 9px 11px;
    font: inherit;
    font-size: 13px;
    color: var(--v2-ink);
    background: var(--v2-card);
    border: 1px solid var(--v2-line);
    border-radius: 8px;
    resize: vertical;
    line-height: 1.5;
  }
  .cc-input:focus {
    outline: 2px solid var(--v2-ember);
    outline-offset: -1px;
  }
  .cc-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 8px;
    flex-wrap: wrap;
  }
  .cc-attach {
    cursor: pointer;
    min-width: 0;
  }
  .cc-file {
    max-width: 190px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .has-file {
    border-color: var(--v2-slate);
    color: var(--v2-ink);
  }
  .cc-clear {
    padding: 5px 7px;
  }
  .cc-err {
    color: var(--v2-rust);
    font-size: 12px;
    overflow-wrap: anywhere;
  }
  @media (max-width: 768px) {
    .cc-actions .v2-btn,
    .cc-clear {
      min-height: 44px;
    }
  }
</style>
