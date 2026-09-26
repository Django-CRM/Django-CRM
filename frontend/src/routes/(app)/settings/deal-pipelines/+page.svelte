<script>
  /**
   * Deal pipelines: the stages a deal moves through, and how long it may sit
   * in each before it counts as rotting.
   *
   * One pipeline's stages are on screen at a time, picked by `?pipeline=`, the
   * same param the deal board uses. Stages are reordered with up and down
   * buttons rather than by dragging: they work with a thumb at 390px and from
   * a keyboard, and each press is one small reorder the server checks.
   *
   * What a stage MEANS is its kind. Reports, goals and the invoice gate count
   * a won-kind stage as won whatever it is called, so renaming "Closed Won"
   * changes words, not numbers. The server keeps at least one open, one won
   * and one lost stage in every pipeline and refuses a kind change or a delete
   * while deals sit in the stage; this page explains, the server decides.
   *
   * A member sees the same page with no write controls (`can_edit`). The
   * backend refuses every write from them regardless.
   */
  import { enhance } from '$app/forms';
  import { resolve } from '$app/paths';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import SettingsCrumb from '$lib/v2/components/SettingsCrumb.svelte';
  import SettingsFormPanel from '$lib/v2/components/SettingsFormPanel.svelte';
  import ConfirmAction from '$lib/v2/components/ConfirmAction.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import { count } from '$lib/v2/format.js';
  import { ArrowDown, ArrowUp, Plus } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  /** `workflow.STAGE_KINDS`. */
  const KINDS = /** @type {const} */ ([
    ['open', 'Open'],
    ['won', 'Won'],
    ['lost', 'Lost']
  ]);
  const KIND_LABEL = Object.fromEntries(KINDS);
  const KIND_TONE = /** @type {Record<string, 'slate'|'moss'|'rust'>} */ ({
    open: 'slate',
    won: 'moss',
    lost: 'rust'
  });

  let pipeline = $derived(data.pipeline);

  // `null` closed, `'new'` adding a stage, or the stage being edited. One
  // panel, so two stages can never be open for edit at once.
  let editing = $state(/** @type {any} */ (null));
  let addingPipeline = $state(false);
  let renaming = $state(false);
  // The kind picked in the open stage form: rotting days only apply to open.
  let editingKind = $state('open');

  // Landing on another pipeline (picked, just created, or the default after a
  // delete) closes any open form, which belonged to the one before.
  let pipelineId = $derived(pipeline?.id);
  $effect(() => {
    void pipelineId;
    editing = null;
    renaming = false;
    addingPipeline = false;
  });

  function edit(/** @type {any} */ stage) {
    editing = stage;
    editingKind = stage === 'new' ? 'open' : stage.kind;
  }

  /** @param {any} s */
  function rotting(s) {
    if (s.kind !== 'open') return 'Closed, does not age';
    if (!s.expected_days) return 'Never';
    const warn = s.warning_days ? `, warns at ${s.warning_days}d` : '';
    return `After ${s.expected_days}d${warn}`;
  }
</script>

<PageHeader title="Deal pipelines">
  {#snippet crumb()}<SettingsCrumb />{/snippet}
  {#snippet sub()}
    <span class="v2-num">{count(data.pipelines.length)}</span>
    {data.pipelines.length === 1 ? 'pipeline' : 'pipelines'} of deal stages
  {/snippet}
  {#snippet actions()}
    {#if data.can_edit && !addingPipeline}
      <button class="v2-btn v2-btn-primary" onclick={() => (addingPipeline = true)}
        ><Plus />New pipeline</button
      >
    {/if}
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:18px;padding-bottom:32px">
    {#if addingPipeline}
      <SettingsFormPanel
        title="New pipeline"
        action="?/createPipeline"
        error={form?.createPipeline?.error}
        submitLabel="Create pipeline"
        oncancel={() => (addingPipeline = false)}
        ondone={() => (addingPipeline = false)}
      >
        {#snippet fields()}
          <div class="v2-field v2-sfp-wide">
            <label for="p-name">Name</label>
            <!-- svelte-ignore a11y_autofocus -->
            <input id="p-name" class="v2-input" name="name" maxlength="100" required autofocus />
            <p class="v2-hint">
              Starts with six stages (four open, then Closed Won and Closed Lost) that you can
              rename, reorder or delete.
            </p>
          </div>
        {/snippet}
      </SettingsFormPanel>
    {/if}

    {#if data.pipelines.length > 1}
      <div class="v2-label" style="margin-bottom:10px">Pipelines</div>
      <nav class="dp-pipelines" aria-label="Pipelines">
        {#each data.pipelines as p (p.id)}
          <a
            class="v2-btn dp-pipeline"
            href={resolve(`/settings/deal-pipelines?pipeline=${p.id}`)}
            aria-current={p.id === pipeline?.id ? 'page' : undefined}
          >
            {p.name}
            {#if p.is_default}<span class="v2-sub">default</span>{/if}
          </a>
        {/each}
      </nav>
    {/if}

    {#if pipeline}
      <div class="v2-card dp-head">
        {#if renaming}
          <form
            class="dp-rename"
            method="POST"
            action="?/renamePipeline"
            use:enhance={() =>
              async (/** @type {any} */ { result, update }) => {
                await update();
                if (result.type === 'success') renaming = false;
              }}
          >
            <input type="hidden" name="id" value={pipeline.id} />
            <label class="v2-sr-only" for="p-rename">Pipeline name</label>
            <input
              id="p-rename"
              class="v2-input"
              name="name"
              maxlength="100"
              required
              value={pipeline.name}
            />
            <button class="v2-btn v2-btn-primary" type="submit">Save</button>
            <button class="v2-btn" type="button" onclick={() => (renaming = false)}>Cancel</button>
          </form>
        {:else}
          <div style="flex:1;min-width:0">
            <b style="font-size:14px">{pipeline.name}</b>
            {#if pipeline.is_default}<Pill tone="slate">Default</Pill>{/if}
            <div class="v2-sub" style="font-size:11.5px;margin-top:3px">
              <span class="v2-num">{count(pipeline.stages.length)}</span> stages ·
              <a
                href={resolve(`/pipeline?view=board&pipeline=${pipeline.id}`)}
                style="color:inherit">Open the board</a
              >
            </div>
          </div>
          {#if data.can_edit}
            <div class="dp-actions">
              <button class="v2-btn v2-btn-sm" type="button" onclick={() => (renaming = true)}>
                Rename
              </button>
              {#if !pipeline.is_default}
                <ConfirmAction
                  action="?/deletePipeline"
                  label="Delete"
                  confirmLabel="Delete pipeline"
                  explain="It leaves the board and the deal forms. Refused while any deal is still in it."
                  hidden={{ id: pipeline.id }}
                />
              {/if}
            </div>
          {/if}
        {/if}
      </div>
      {#if pipeline.is_default}
        <p class="v2-sub" style="font-size:11.5px;margin:-6px 0 14px;max-width:64ch">
          The default pipeline is where a deal lands when nothing names another one, so it cannot be
          deleted.
        </p>
      {/if}
      {#if form?.renamePipeline?.error}
        <p class="v2-error" style="margin-bottom:12px">{form.renamePipeline.error}</p>
      {/if}
      {#if form?.deletePipeline?.error}
        <p class="v2-error" style="margin-bottom:12px">{form.deletePipeline.error}</p>
      {/if}

      {#if editing}
        <SettingsFormPanel
          title={editing === 'new' ? 'New stage' : `Edit ${editing.label}`}
          action={editing === 'new' ? '?/createStage' : '?/updateStage'}
          error={form?.stage?.error}
          submitLabel={editing === 'new' ? 'Add stage' : 'Save stage'}
          oncancel={() => (editing = null)}
          ondone={() => (editing = null)}
        >
          {#snippet fields()}
            {#if editing === 'new'}
              <input type="hidden" name="pipeline_id" value={pipeline.id} />
            {:else}
              <input type="hidden" name="id" value={editing.id} />
            {/if}
            <div class="v2-field">
              <label for="s-label">Name</label>
              <input
                id="s-label"
                class="v2-input"
                name="label"
                maxlength="100"
                required
                value={editing === 'new' ? '' : editing.label}
              />
            </div>
            <div class="v2-field">
              <label for="s-kind">Kind</label>
              <select id="s-kind" class="v2-input" name="kind" bind:value={editingKind}>
                {#each KINDS as [value, label] (value)}
                  <option {value}>{label}</option>
                {/each}
              </select>
              <p class="v2-hint">
                Won and lost close a deal. Reports and goals count by kind, not by name.
              </p>
            </div>
            {#if editingKind === 'open'}
              <div class="v2-field">
                <label for="s-expected">Rotting after (days)</label>
                <input
                  id="s-expected"
                  class="v2-input"
                  type="number"
                  name="expected_days"
                  min="1"
                  max="3650"
                  step="1"
                  inputmode="numeric"
                  value={editing === 'new' ? '' : (editing.expected_days ?? '')}
                />
                <p class="v2-hint">
                  A deal past this many days in the stage reads Past expected, and Stalled at one
                  and a half times it. Blank: it never rots here.
                </p>
              </div>
              <div class="v2-field">
                <label for="s-warning">Warn after (days)</label>
                <input
                  id="s-warning"
                  class="v2-input"
                  type="number"
                  name="warning_days"
                  min="1"
                  max="3650"
                  step="1"
                  inputmode="numeric"
                  value={editing === 'new' ? '' : (editing.warning_days ?? '')}
                />
                <p class="v2-hint">Optional. An earlier Past expected, before the rotting day.</p>
              </div>
            {/if}
          {/snippet}
        </SettingsFormPanel>
      {/if}

      {#if form?.stageList?.error}
        <p class="v2-error" style="margin-bottom:12px">{form.stageList.error}</p>
      {/if}

      <div class="dp-stages-head">
        <div class="v2-label">Stages, in board order</div>
        {#if data.can_edit && !editing}
          <button class="v2-btn v2-btn-sm" type="button" onclick={() => edit('new')}
            ><Plus size={13} />Add stage</button
          >
        {/if}
      </div>
      <div class="v2-table-wrap">
        <table class="v2-table">
          <thead>
            <tr>
              <th>Stage</th>
              <th>Kind</th>
              <th>Rots</th>
              {#if data.can_edit}<th></th>{/if}
            </tr>
          </thead>
          <tbody>
            {#each pipeline.stages as s, i (s.id)}
              <tr>
                <td class="v2-table-primary" data-m="title">{s.label}</td>
                <td data-m="tag"
                  ><Pill tone={KIND_TONE[s.kind] ?? 'slate'}>{KIND_LABEL[s.kind]}</Pill></td
                >
                <td data-m="meta">{rotting(s)}</td>
                {#if data.can_edit}
                  <td class="dp-actions-cell">
                    <div class="dp-actions">
                      {#each ['up', 'down'] as direction (direction)}
                        <form method="POST" action="?/moveStage" use:enhance>
                          <input type="hidden" name="pipeline_id" value={pipeline.id} />
                          <input type="hidden" name="id" value={s.id} />
                          <input type="hidden" name="direction" value={direction} />
                          <button
                            class="v2-btn v2-btn-sm dp-icon"
                            type="submit"
                            aria-label="Move {s.label} {direction}"
                            disabled={direction === 'up'
                              ? i === 0
                              : i === pipeline.stages.length - 1}
                          >
                            {#if direction === 'up'}<ArrowUp size={14} />{:else}<ArrowDown
                                size={14}
                              />{/if}
                          </button>
                        </form>
                      {/each}
                      <button class="v2-btn v2-btn-sm" type="button" onclick={() => edit(s)}
                        >Edit</button
                      >
                      <ConfirmAction
                        action="?/deleteStage"
                        label="Delete"
                        confirmLabel="Delete stage"
                        explain="The column leaves the board. Refused while deals are in it, and for the pipeline's last open, won or lost stage."
                        hidden={{ id: s.id }}
                      />
                    </div>
                  </td>
                {/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </div>
</div>

<style>
  .dp-pipelines {
    display: flex;
    flex-wrap: wrap;
    gap: 7px;
    margin-bottom: 18px;
  }
  .dp-pipeline[aria-current='page'] {
    border-color: var(--v2-ink);
    font-weight: 600;
  }
  .dp-head {
    display: flex;
    gap: 12px;
    align-items: center;
    flex-wrap: wrap;
    padding: 14px 16px;
    margin-bottom: 16px;
  }
  .dp-rename {
    display: flex;
    gap: 7px;
    align-items: center;
    flex-wrap: wrap;
    width: 100%;
  }
  .dp-rename .v2-input {
    flex: 1 1 200px;
  }
  .dp-actions {
    display: flex;
    gap: 6px;
    align-items: center;
    justify-content: flex-end;
    flex-wrap: wrap;
  }
  .dp-stages-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 10px;
  }
  @media (max-width: 768px) {
    /* Thumb-sized: the default small button is too short to hit reliably. */
    .dp-actions :global(.v2-btn),
    .dp-stages-head .v2-btn,
    .dp-pipeline,
    .dp-rename .v2-btn {
      min-height: 44px;
    }
    .dp-icon {
      min-width: 44px;
      justify-content: center;
    }
    .dp-actions {
      justify-content: flex-start;
    }
    /* Its own line under the meta run, not squeezed in beside it. */
    .dp-actions-cell {
      display: block;
      margin: 8px 0 0;
    }
  }
</style>
