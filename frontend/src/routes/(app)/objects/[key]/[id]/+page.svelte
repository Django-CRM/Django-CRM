<script lang="ts">
  import { untrack } from 'svelte';
  import { enhance } from '$app/forms';
  import { resolve } from '$app/paths';
  import { asInternalPath } from '#lib/utils/paths.js';
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import ConfirmAction from '#lib/v2/components/ConfirmAction.svelte';
  import Avatar from '#lib/v2/components/Avatar.svelte';
  import CustomFieldsCard from '#lib/v2/components/CustomFieldsCard.svelte';
  import Pill from '#lib/v2/components/Pill.svelte';
  import Timeline from '#lib/v2/components/Timeline.svelte';
  import CommentComposer from '#lib/v2/components/CommentComposer.svelte';
  import { shortDate } from '#lib/v2/format.js';
  import RelatedRecords from '#lib/v2/components/RelatedRecords.svelte';
  import {
    canWriteObject,
    recordHref,
    relatedView,
    uploadTooLarge
  } from '#lib/v2/custom-objects.js';
  import { Pencil } from '@lucide/svelte';
  import type { MatchRow } from '#lib/server/v2/real-estate.js';
  import type { PageProps } from './$types';

  let { data, form }: PageProps = $props();

  let def = $derived(data.definition);
  let record = $derived(data.record);
  let canWrite = $derived(canWriteObject(def, data.customObjects?.has_custom_objects));
  let assignees = $derived(record.assigned_to ?? []);
  // A missing `tags` (an older API) shows nothing.
  let tags = $derived(record.tags ?? []);
  let uploadNote = $state('');
  let uploading = $state(false);

  let seenId = untrack(() => record.id);
  $effect.pre(() => {
    if (record.id === seenId) return;
    seenId = record.id;
    uploadNote = '';
    uploading = false;
  });

  function checkPicked(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    uploadNote = (file && uploadTooLarge(file)) || '';
    if (uploadNote) input.value = '';
  }
</script>

{#snippet matchRow(objectKey: string, row: MatchRow, action: string, label: string)}
  <div class="cf-row match">
    <div class="cf-value">
      <a class="match-name" href={resolve(asInternalPath(`/objects/${objectKey}/${row.id}`))}
        >{row.name}</a
      >
      {#if row.facts.length > 0}
        <span class="v2-sub match-facts">
          {#each row.facts as fact, i (i)}
            <span>{fact.label}: {fact.value}</span>
          {/each}
        </span>
      {/if}
    </div>
    <form method="POST" {action} use:enhance>
      <input type="hidden" name="other" value={row.id} />
      <button class="v2-btn v2-btn-quiet match-btn" type="submit" aria-label="{label} {row.name}"
        >{label}</button
      >
    </form>
  </div>
{/snippet}

<PageHeader title={record.name} record>
  {#snippet leading()}
    <Avatar name={record.name} size={42} />
  {/snippet}
  {#snippet crumb()}
    <a href={resolve(asInternalPath(`/objects/${def.key}`))}>{def.label_plural}</a>
    ›
    <span>{def.label}</span>
  {/snippet}
  {#snippet actions()}
    {#if record.can_edit}
      <a class="v2-btn" href={resolve(asInternalPath(`/objects/${def.key}/${record.id}/edit`))}
        ><Pencil />Edit</a
      >
    {/if}
    {#if record.can_delete}
      <ConfirmAction
        action="?/delete"
        label="Delete"
        confirmLabel="Delete for good"
        explain="Deletes {record.name} and its files permanently. This cannot be undone."
      />
    {/if}
  {/snippet}
</PageHeader>

<div class="v2-main">
  <div class="v2-scroll">
    <div class="v2-pad" style="padding-top:16px;padding-bottom:32px">
      {#if form?.deleteError}
        <p class="v2-error" role="alert" style="margin:0 0 14px">{form.deleteError}</p>
      {/if}

      <CustomFieldsCard
        flush
        fields={[
          { key: '__name', label: def.name_label, value: record.name, filled: true },
          ...data.details
        ]}
      />

      {#if tags.length > 0}
        <div class="v2-label" style="margin:22px 0 10px">Tags</div>
        <div class="tags">
          {#each tags as tag (tag.id)}<Pill>{tag.name}</Pill>{/each}
        </div>
      {/if}

      {#if data.linkFields.length > 0}
        <div class="v2-label" style="margin:22px 0 10px">Links</div>
        <div class="v2-card cf">
          {#each data.linkFields as f (f.key)}
            {@const entry = record.links?.[f.key] ?? { records: [], hidden: 0 }}
            <div class="cf-row">
              <span class="cf-label">{f.label}</span>
              <span class="cf-value links">
                {#each entry.records as r (r.id)}
                  {@const href = recordHref(r.object, r.id)}
                  {#if href}
                    <a class="link-row" href={resolve(asInternalPath(href))}>{r.name}</a>
                  {:else}
                    <span class="link-row">{r.name}</span>
                  {/if}
                {/each}
                {#if entry.hidden > 0}
                  <span class="v2-muted">
                    {entry.records.length > 0 ? 'and ' : ''}{entry.hidden} you can't open
                  </span>
                {:else if entry.records.length === 0}
                  <span class="v2-muted">None</span>
                {/if}
              </span>
            </div>
          {/each}
        </div>
      {/if}

      {#if data.matches}
        {@const m = data.matches}
        <div class="v2-label" id="matches" style="margin:22px 0 10px">
          {m.state === 'ok' ? m.heading : 'Matches'}
        </div>
        {#if form?.matchError}
          <p class="v2-error" role="alert" style="margin:0 0 10px">{form.matchError}</p>
        {/if}
        <div class="v2-card cf">
          {#if m.state === 'failed'}
            <p class="v2-sub" style="margin:9px 0">Could not load matches.</p>
          {:else}
            {#each m.rows as row (row.id)}
              {@render matchRow(m.objectKey, row, '?/dismiss', 'Dismiss')}
            {:else}
              <p class="v2-sub" style="margin:9px 0">No matches yet.</p>
            {/each}
            {#if m.dismissedCount > 0}
              <p class="v2-sub" style="margin:9px 0">
                {m.dismissedCount} dismissed
                {#if m.dismissed === null}
                  ·
                  <a href="?dismissed=1#matches" data-sveltekit-noscroll>Show</a>
                {:else}
                  ·
                  <a href="?#matches" data-sveltekit-noscroll>Hide</a>
                {/if}
              </p>
              {#each m.dismissed ?? [] as row (row.id)}
                {@render matchRow(m.objectKey, row, '?/undismiss', 'Undo')}
              {/each}
            {/if}
          {/if}
        </div>
      {/if}

      <RelatedRecords related={relatedView(data.related)} error={form?.related?.error} />

      <div class="v2-label" style="margin:22px 0 10px">Photos</div>
      {#if form?.fileError}
        <p class="v2-error" role="alert" style="margin:0 0 10px">{form.fileError}</p>
      {/if}
      {#if data.photos.length > 0}
        <div class="photos">
          {#each data.photos as p (p.id)}
            <div class="photo v2-card">
              <a href={resolve(asInternalPath(p.href ?? ''))} aria-label="Download {p.file_name}">
                <img src={p.href} alt={p.file_name} loading="lazy" />
              </a>
              <div class="photo-foot">
                <span class="photo-name">{p.file_name}</span>
                {#if p.can_delete}
                  <ConfirmAction
                    action="?/deleteFile"
                    hidden={{ attachment: p.id }}
                    label="Delete"
                    confirmLabel="Delete"
                    explain="Deletes this photo permanently."
                  />
                {/if}
              </div>
            </div>
          {/each}
        </div>
      {:else}
        <div class="v2-card cf"><p class="v2-sub" style="margin:9px 0">No photos yet.</p></div>
      {/if}

      <div class="v2-label" style="margin:22px 0 10px">Files</div>
      <div class="v2-card cf">
        {#each data.files as f (f.id)}
          <div class="cf-row file-row">
            <span class="cf-value">
              <a class="file-link" href={resolve(asInternalPath(f.href ?? ''))}>{f.file_name}</a>
              <span class="v2-sub" style="display:block;font-size:12px">
                {f.created_by?.name ?? 'Unknown'} · {shortDate(f.created_at)}
              </span>
            </span>
            {#if f.can_delete}
              <ConfirmAction
                action="?/deleteFile"
                hidden={{ attachment: f.id }}
                label="Delete"
                confirmLabel="Delete"
                explain="Deletes this file permanently."
              />
            {/if}
          </div>
        {:else}
          <p class="v2-sub" style="margin:9px 0">No files yet.</p>
        {/each}
      </div>

      {#if record.can_edit}
        {#key record.id}
          <form
            class="upload"
            method="POST"
            action="?/upload"
            enctype="multipart/form-data"
            use:enhance={({ cancel }) => {
              if (uploadNote) return cancel();
              uploading = true;
              return async ({ update }) => {
                await update();
                uploading = false;
              };
            }}
          >
            <input
              class="v2-input"
              type="file"
              name="file"
              required
              aria-label="Add a photo or file"
              onchange={checkPicked}
            />
            <button class="v2-btn" type="submit" disabled={uploading}>Upload</button>
          </form>
        {/key}
        {#if uploadNote || form?.uploadError}
          <p class="v2-error" role="alert" style="margin:8px 0 0">
            {uploadNote || form?.uploadError}
          </p>
        {/if}
      {/if}

      <div class="v2-label" style="margin:22px 0 10px">Timeline</div>
      {#if canWrite}
        <CommentComposer
          files={false}
          placeholder="Add a note…"
          maxlength={10000}
          error={form?.noteError}
        />
      {/if}
      <Timeline
        events={data.timeline}
        noteFailure={form?.noteFailure ?? null}
        canWriteNotes={canWrite}
      />

      <div class="v2-label" style="margin:22px 0 10px">Assignees</div>
      <div class="v2-card cf">
        {#if assignees.length > 0}
          {#each assignees as person (person.id)}
            <div class="cf-row person">
              <Avatar name={person.name} size={24} />
              <span class="cf-value">{person.name}</span>
            </div>
          {/each}
        {:else}
          <p class="v2-sub" style="margin:9px 0">No one is assigned.</p>
        {/if}
      </div>

      <div class="v2-label" style="margin:22px 0 10px">Created by</div>
      <div class="v2-card cf">
        <div class="cf-row person">
          {#if record.created_by}<Avatar name={record.created_by.name} size={24} />{/if}
          <span class="cf-value">
            {record.created_by?.name ?? 'Unknown'}
            <span class="v2-sub"> · {shortDate(record.created_at)}</span>
          </span>
        </div>
      </div>
    </div>
  </div>
</div>

<style>
  .cf {
    padding: 4px 16px;
    font-size: 13px;
  }
  .cf-row {
    display: flex;
    gap: 16px;
    align-items: baseline;
    padding: 9px 0;
    border-bottom: 1px solid var(--v2-line);
  }
  .cf-row:last-child {
    border-bottom: 0;
  }
  .cf-label {
    flex: 0 0 40%;
    color: var(--v2-ink-soft);
  }
  .cf-value {
    flex: 1 1 auto;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .person,
  .file-row {
    align-items: center;
  }
  .photos {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 12px;
  }
  .photo {
    overflow: hidden;
    padding: 0;
  }
  .photo img {
    display: block;
    width: 100%;
    aspect-ratio: 4 / 3;
    object-fit: cover;
  }
  .photo-foot {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 8px 10px;
    font-size: 12px;
  }
  .photo-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .match {
    align-items: center;
  }
  .match-name {
    display: inline-flex;
    align-items: center;
    min-height: 44px;
  }
  .match-facts {
    display: flex;
    flex-wrap: wrap;
    column-gap: 14px;
    font-size: 12px;
  }
  .match-btn {
    min-height: 44px;
  }
  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .links {
    display: flex;
    flex-wrap: wrap;
    column-gap: 14px;
  }
  .link-row {
    display: inline-flex;
    align-items: center;
    min-height: 44px;
  }
  .upload {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
    margin-top: 12px;
  }
  .upload :global(.v2-input) {
    flex: 1 1 220px;
    min-width: 0;
  }
  @media (max-width: 767px) {
    .cf-row:not(.person):not(.file-row) {
      display: block;
    }
    .cf-label {
      display: block;
      margin-bottom: 2px;
    }
    .file-row {
      flex-wrap: wrap;
    }
    .file-link {
      display: flex;
      align-items: center;
      min-height: 44px;
    }
    .upload :global(.v2-btn) {
      min-height: 44px;
    }
  }
</style>
