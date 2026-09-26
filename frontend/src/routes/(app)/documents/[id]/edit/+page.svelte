<script>
  import { resolve } from '$app/paths';
  import { untrack, tick } from 'svelte';
  import { SvelteSet } from 'svelte/reactivity';
  import { enhance } from '$app/forms';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import NextAction from '$lib/v2/components/NextAction.svelte';
  import { TriangleAlert, Users, Lock } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form: result } = $props();

  /**
   * Rename, archive, and re-share, the mutations that used to be reachable by
   * anyone a document was shared with. The page is only ever drawn for a writer
   * (owner or admin); the PUT enforces the same, so this is the UX side of the
   * `_may_write` fix.
   *
   * Replacing the file is optional and lives on this form rather than in a
   * second upload, because deleting and re-uploading was the only way to
   * correct a wrong file and it dropped every share the document had.
   * VALIDATION IS A UX HINT: the serializer requires a title and rejects a
   * duplicate within the org regardless of what this page allows.
   */
  // Seed once from the loaded document, or from a rejected submit's echoed
  // values. Read inside untrack so this captures the initial state without
  // subscribing the form fields to later `data` changes (there are none, an
  // edit page loads one document).
  const init = untrack(() => {
    const prev = result?.values ?? {};
    const doc = data.document ?? {};
    return {
      title: prev.title ?? doc.title ?? '',
      status: prev.status ?? doc.status ?? 'active',
      shared_to: (prev.shared_to ?? doc.shared_to ?? []).map(String),
      teams: (prev.teams ?? doc.teams ?? []).map(String)
    };
  });

  let title = $state(init.title);
  let status = $state(init.status);
  // SvelteSet is reactive on mutation, so the checkboxes and the reach line
  // update on `.add()`/`.delete()` without reassigning the whole set.
  let sharedTo = new SvelteSet(init.shared_to);
  let sharedTeams = new SvelteSet(init.teams);

  // Shown back to the reader so a replacement is a deliberate act, not a
  // silent one. Empty means the stored file is kept.
  let newFileName = $state('');

  function onFile(/** @type {Event} */ ev) {
    const input = /** @type {HTMLInputElement} */ (ev.currentTarget);
    newFileName = input.files?.[0]?.name ?? '';
  }

  let touched = $state(/** @type {Record<string, boolean>} */ ({}));
  let submitted = $state(false);
  let confirmingDelete = $state(false);

  let errors = $derived.by(() => {
    /** @type {Record<string, string>} */
    const e = {};
    if (!title.trim()) e.title = 'A document needs a title.';
    return e;
  });

  let valid = $derived(Object.keys(errors).length === 0);
  const show = (field) => (touched[field] || submitted) && errors[field];

  let reach = $derived(sharedTo.size + sharedTeams.size);

  // The stored shares that have a checkbox, posted back as `*_original` so the
  // save action can tell "sharing left alone" (a PATCH that keeps every share)
  // from "sharing changed" (a PUT). From the stored document, never from a
  // rejected submit's echo, so a retry still compares against the record.
  let originalShares = $derived.by(() => {
    const active = new Set((data.people ?? []).map((/** @type {any} */ p) => String(p.id)));
    return (data.document?.shared_to ?? []).map(String).filter((id) => active.has(id));
  });
  let originalTeams = $derived.by(() => {
    const known = new Set((data.teams ?? []).map((/** @type {any} */ t) => String(t.id)));
    return (data.document?.teams ?? []).map(String).filter((id) => known.has(id));
  });
  // Shares with no checkbox (people deactivated since, as the picker lists
  // active people only) are posted back as both `shared_to` and
  // `shared_to_original`, so a PUT keeps them and they never count as a change.
  let keptShares = $derived((data.document?.kept_shares ?? []).map(String));
  // Not counted when the list failed to load, when everybody would look inactive.
  let inactiveShares = $derived(data.options_failed ? 0 : keptShares.length);

  function toggle(/** @type {SvelteSet<string>} */ set, /** @type {string} */ id) {
    if (set.has(id)) set.delete(id);
    else set.add(id);
  }

  /** @type {import('./$types').SubmitFunction} */
  const check = async ({ cancel }) => {
    submitted = true;
    if (!valid) {
      cancel();
      await tick();
      /** @type {HTMLElement | null} */
      const first = document.querySelector('[aria-invalid="true"]');
      first?.focus();
    }
  };
</script>

{#if !data.can_edit}
  <PageHeader title="Manage document">
    {#snippet crumb()}<a href={resolve('/documents')}>Documents</a> ›{/snippet}
  </PageHeader>
  <div class="v2-pad" style="padding-top:40px">
    <NextAction
      label="Owner or admin only"
      text="Changing a document is limited to the person who uploaded it and admins. You can open a document shared with you, but not edit it."
    />
  </div>
{:else}
  <PageHeader title="Manage document" center>
    {#snippet crumb()}<a href={resolve('/documents')}>Documents</a> ›{/snippet}
    {#snippet sub()}{data.document.title}{/snippet}
  </PageHeader>

  <div class="v2-scroll v2-pad" style="padding-top:18px">
    <form
      class="v2-form"
      method="POST"
      action="?/save"
      enctype="multipart/form-data"
      use:enhance={check}
      novalidate
    >
      {#if result?.error}
        <div
          class="v2-next"
          style="background:color-mix(in srgb, var(--v2-rust) 9%, transparent);border-color:color-mix(in srgb, var(--v2-rust) 28%, transparent);margin-bottom:18px"
          role="alert"
        >
          <TriangleAlert size={17} style="color:var(--v2-rust);flex:none" />
          <div class="v2-next-body">
            <div style="font-weight:600">The server refused this change</div>
            <div class="v2-sub" style="margin-top:2px">{result.error}</div>
          </div>
        </div>
      {/if}

      <div class="v2-field">
        <label for="f-title">Title</label>
        <input
          id="f-title"
          name="title"
          class="v2-input"
          bind:value={title}
          onblur={() => (touched.title = true)}
          aria-invalid={show('title') ? 'true' : undefined}
          aria-describedby={show('title') ? 'e-title' : undefined}
        />
        {#if show('title')}<p class="v2-error" id="e-title">{errors.title}</p>{/if}
      </div>

      <!-- Replacing the file keeps the record, and so keeps its shares. Doing
           this by deleting and re-uploading was the only way before, and it
           silently dropped everyone the document was shared with. -->
      <div class="v2-field">
        <label for="f-file">File</label>
        <p class="v2-input v2-file-static">{data.document.document_file}</p>
        <input
          id="f-file"
          name="document_file"
          class="v2-input v2-file"
          type="file"
          onchange={onFile}
          aria-describedby="h-file"
          style="margin-top:8px"
        />
        <p class="v2-hint" id="h-file">
          {newFileName
            ? `Replacing with: ${newFileName}`
            : 'Leave this empty to keep the current file. Choosing one replaces it, and the title and shares stay as they are.'}
        </p>
      </div>

      <div class="v2-field">
        <label for="f-status">Status</label>
        <select id="f-status" name="status" class="v2-input" bind:value={status}>
          <option value="active">Active, appears in the list</option>
          <option value="inactive">Archived, kept, hidden from the default view</option>
        </select>
      </div>

      <fieldset class="v2-field share">
        <legend>Who can open it</legend>
        <p class="v2-hint" style="margin-top:0">
          {#if reach === 0}
            <span class="unshared"><Lock size={11} /> Only the owner and admins.</span>
          {:else}
            Reaches <span class="v2-num">{reach}</span>
            {reach === 1 ? 'person or team' : 'people and teams'}, plus admins.
          {/if}
        </p>

        {#if data.options_failed}
          <p class="v2-hint" role="alert">
            The people and teams list did not load, so sharing cannot be changed right now. Saving
            keeps everyone this document is shared with.
          </p>
        {/if}

        {#if data.people?.length}
          <div class="share-label">People</div>
          <div class="share-grid">
            {#each data.people as p (p.id)}
              <label class="share-opt">
                <input
                  type="checkbox"
                  name="shared_to"
                  value={p.id}
                  checked={sharedTo.has(String(p.id))}
                  onchange={() => toggle(sharedTo, String(p.id))}
                />
                <span>{p.name}</span>
              </label>
            {/each}
          </div>
        {/if}

        {#if data.teams?.length}
          <div class="share-label"><Users size={12} /> Teams</div>
          <div class="share-grid">
            {#each data.teams as t (t.id)}
              <label class="share-opt">
                <input
                  type="checkbox"
                  name="teams"
                  value={t.id}
                  checked={sharedTeams.has(String(t.id))}
                  onchange={() => toggle(sharedTeams, String(t.id))}
                />
                <span>{t.name}</span>
              </label>
            {/each}
          </div>
        {/if}

        {#if inactiveShares > 0}
          <p class="v2-hint">
            Also shared with {inactiveShares === 1 ? 'one person' : `${inactiveShares} people`} no longer
            active. Saving keeps {inactiveShares === 1 ? 'that share' : 'those shares'}.
          </p>
        {/if}

        {#each keptShares as id (id)}
          <input type="hidden" name="shared_to" value={id} />
          <input type="hidden" name="shared_to_original" value={id} />
        {/each}
        {#each originalShares as id (id)}
          <input type="hidden" name="shared_to_original" value={id} />
        {/each}
        {#each originalTeams as id (id)}
          <input type="hidden" name="teams_original" value={id} />
        {/each}
      </fieldset>

      <div style="display:flex;gap:8px;align-items:center;margin-top:22px">
        <button class="v2-btn v2-btn-primary" type="submit">Save changes</button>
        <a class="v2-btn" href={resolve('/documents')}>Cancel</a>
      </div>
    </form>

    <!-- Delete sits apart from the save form and behind an inline confirm, so a
         mis-click cannot destroy a document for everyone, no blocking browser
         dialog, just a second, deliberate button. -->
    <div
      style="margin-top:26px;padding-top:18px;border-top:1px solid var(--v2-line-soft);max-width:640px"
    >
      {#if !confirmingDelete}
        <button
          class="v2-btn"
          type="button"
          style="color:var(--v2-rust)"
          onclick={() => (confirmingDelete = true)}>Delete this document</button
        >
        <p class="v2-sub" style="font-size:12px;margin-top:8px">
          Archiving keeps the document and its history; delete removes it for everyone who could
          open it. Delete only when it was uploaded by mistake.
        </p>
      {:else}
        <form method="POST" action="?/delete" use:enhance>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <span class="v2-sub" style="font-size:13px"
              >Delete “{data.document.title}” for good?</span
            >
            <button class="v2-btn v2-btn-primary" type="submit" style="background:var(--v2-rust)"
              >Yes, delete</button
            >
            <button class="v2-btn" type="button" onclick={() => (confirmingDelete = false)}
              >Keep it</button
            >
          </div>
        </form>
      {/if}
    </div>
  </div>
{/if}

<style>
  .share {
    border: 1px solid var(--v2-line-soft);
    border-radius: 8px;
    padding: 14px 16px;
  }
  .share legend {
    font-weight: 600;
    padding: 0 6px;
  }
  .unshared {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: var(--v2-clay);
  }
  .share-label {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    font-weight: 600;
    color: var(--v2-slate);
    margin: 12px 0 6px;
  }
  .share-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px 14px;
  }
  @media (max-width: 560px) {
    .share-grid {
      grid-template-columns: 1fr;
    }
  }
  .share-opt {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13.5px;
    cursor: pointer;
  }
  .share-opt input {
    flex: none;
  }
  .v2-file-static {
    font-family: var(--v2-mono);
    font-size: 12px;
    color: var(--v2-slate);
    margin: 0;
  }
</style>
