<script>
  /**
   * Lead pipelines: the stages a lead moves through on the board.
   *
   * One pipeline's stages are on screen at a time, picked by `?pipeline=`, the
   * same control the board uses. Stages are reordered with up and down
   * buttons rather than by dragging: they work with a thumb at 390px and from
   * a keyboard, and each press is one small reorder the server checks.
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
  import EmptyState from '$lib/v2/components/EmptyState.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import { count } from '$lib/v2/format.js';
  import { LEAD_STATUS_LABEL } from '$lib/v2/enums.js';
  import { ArrowDown, ArrowUp, Columns3, Plus } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  /** `LeadStage.STAGE_TYPE_CHOICES`. */
  const STAGE_TYPES = /** @type {const} */ ([
    ['open', 'Open'],
    ['won', 'Won'],
    ['lost', 'Lost']
  ]);
  const TYPE_LABEL = Object.fromEntries(STAGE_TYPES);
  const TYPE_TONE = /** @type {Record<string, 'slate'|'moss'|'rust'>} */ ({
    open: 'slate',
    won: 'moss',
    lost: 'rust'
  });

  /**
   * The statuses a stage may map to. "converted" is not offered: the board
   * refuses every move into a stage mapped to it, and the API refuses a new
   * mapping to it. A stage that already carries it keeps an option for it
   * below, because a select with no matching option would quietly submit
   * "None" on an unrelated edit.
   */
  const STAGE_STATUSES = ['assigned', 'in process', 'recycled', 'closed'];

  const statusLabel = (/** @type {string} */ s) =>
    /** @type {Record<string, string>} */ (LEAD_STATUS_LABEL)[s] ?? s;

  let pipeline = $derived(data.pipeline);

  // `null` closed, `'new'` adding a stage, or the stage being edited. One
  // panel, so two stages can never be open for edit at once.
  let editing = $state(/** @type {any} */ (null));
  let addingPipeline = $state(false);
  let renaming = $state(false);

  // Landing on another pipeline (picked, just created, or the next one after
  // a delete) closes any open form, which belonged to the one before.
  let pipelineId = $derived(pipeline?.id);
  $effect(() => {
    void pipelineId;
    editing = null;
    renaming = false;
    addingPipeline = false;
  });

  let stageOptions = $derived(
    editing &&
      editing !== 'new' &&
      editing.mapsToStatus &&
      !STAGE_STATUSES.includes(editing.mapsToStatus)
      ? [...STAGE_STATUSES, editing.mapsToStatus]
      : STAGE_STATUSES
  );
</script>

<PageHeader title="Lead pipelines">
  {#snippet crumb()}<SettingsCrumb />{/snippet}
  {#snippet sub()}
    <span class="v2-num">{count(data.pipelines.length)}</span>
    {data.pipelines.length === 1 ? 'pipeline' : 'pipelines'} on the lead board
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
            <input id="p-name" class="v2-input" name="name" maxlength="255" required autofocus />
            <p class="v2-hint">
              Starts with six stages (New, Contacted, Qualified, Proposal, Won, Lost) that you can
              rename, reorder or delete.
            </p>
          </div>
        {/snippet}
      </SettingsFormPanel>
    {/if}

    {#if data.pipelines.length === 0}
      <EmptyState
        title="No lead pipelines yet"
        body={data.can_edit
          ? 'A pipeline gives the lead board its columns. Create one to start moving leads through stages.'
          : 'A pipeline gives the lead board its columns. An admin can create one here.'}
      >
        {#snippet icon()}<Columns3 size={21} />{/snippet}
      </EmptyState>
    {:else}
      {#if data.pipelines.length > 1}
        <div class="v2-label" style="margin-bottom:10px">Pipelines</div>
        <nav class="lp-pipelines" aria-label="Pipelines">
          {#each data.pipelines as p (p.id)}
            <a
              class="v2-btn lp-pipeline"
              href={resolve(`/settings/lead-pipelines?pipeline=${p.id}`)}
              aria-current={p.id === pipeline?.id ? 'page' : undefined}
            >
              {p.name}
              <span class="v2-sub v2-num">{count(p.stageCount)}</span>
            </a>
          {/each}
        </nav>
      {/if}

      {#if pipeline}
        <div class="v2-card lp-head">
          {#if renaming}
            <form
              class="lp-rename"
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
                maxlength="255"
                required
                value={pipeline.name}
              />
              <button class="v2-btn v2-btn-primary" type="submit">Save</button>
              <button class="v2-btn" type="button" onclick={() => (renaming = false)}>Cancel</button
              >
            </form>
          {:else}
            <div style="flex:1;min-width:0">
              <b style="font-size:14px">{pipeline.name}</b>
              <div class="v2-sub" style="font-size:11.5px;margin-top:3px">
                <span class="v2-num">{count(pipeline.stages.length)}</span> stages ·
                <span class="v2-num">{count(pipeline.leadCount)}</span>
                {pipeline.leadCount === 1 ? 'lead' : 'leads'} in them ·
                <a href={resolve(`/leads/board?pipeline=${pipeline.id}`)} style="color:inherit"
                  >Open the board</a
                >
              </div>
            </div>
            {#if data.can_edit}
              <div class="lp-actions">
                <button class="v2-btn v2-btn-sm" type="button" onclick={() => (renaming = true)}>
                  Rename
                </button>
                <ConfirmAction
                  action="?/deletePipeline"
                  label="Delete"
                  confirmLabel="Delete pipeline"
                  explain="It leaves the board and this list. Refused while a lead on the board is still in one of its stages."
                  hidden={{ id: pipeline.id }}
                />
              </div>
            {/if}
          {/if}
        </div>
        {#if form?.renamePipeline?.error}
          <p class="v2-error" style="margin-bottom:12px">{form.renamePipeline.error}</p>
        {/if}
        {#if form?.deletePipeline?.error}
          <p class="v2-error" style="margin-bottom:12px">{form.deletePipeline.error}</p>
        {/if}

        {#if editing}
          <SettingsFormPanel
            title={editing === 'new' ? 'New stage' : `Edit ${editing.name}`}
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
                <label for="s-name">Name</label>
                <input
                  id="s-name"
                  class="v2-input"
                  name="name"
                  maxlength="100"
                  required
                  value={editing === 'new' ? '' : editing.name}
                />
              </div>
              <div class="v2-field">
                <label for="s-type">Type</label>
                <select id="s-type" class="v2-input" name="stage_type">
                  {#each STAGE_TYPES as [value, label] (value)}
                    <option
                      {value}
                      selected={editing === 'new' ? value === 'open' : editing.stageType === value}
                      >{label}</option
                    >
                  {/each}
                </select>
              </div>
              <div class="v2-field">
                <label for="s-status">Sets lead status to</label>
                <select id="s-status" class="v2-input" name="maps_to_status">
                  <option value="" selected={editing === 'new' || !editing.mapsToStatus}
                    >Leave it as it is</option
                  >
                  {#each stageOptions as s (s)}
                    <option value={s} selected={editing !== 'new' && editing.mapsToStatus === s}
                      >{statusLabel(s)}</option
                    >
                  {/each}
                </select>
                <p class="v2-hint">Applied when a lead is moved into this stage.</p>
              </div>
              <div class="v2-field">
                <label for="s-prob">Win probability (%)</label>
                <input
                  id="s-prob"
                  class="v2-input"
                  type="number"
                  name="win_probability"
                  min="0"
                  max="100"
                  step="1"
                  inputmode="numeric"
                  value={editing === 'new' ? 0 : editing.winProbability}
                />
                <p class="v2-hint">Given to a lead moved here that has no probability yet.</p>
              </div>
            {/snippet}
          </SettingsFormPanel>
        {/if}

        {#if form?.stageList?.error}
          <p class="v2-error" style="margin-bottom:12px">{form.stageList.error}</p>
        {/if}

        <div class="lp-stages-head">
          <div class="v2-label">Stages, in board order</div>
          {#if data.can_edit && !editing}
            <button class="v2-btn v2-btn-sm" type="button" onclick={() => (editing = 'new')}
              ><Plus size={13} />Add stage</button
            >
          {/if}
        </div>
        <div class="v2-table-wrap">
          <table class="v2-table">
            <thead>
              <tr>
                <th>Stage</th>
                <th>Type</th>
                <th>Sets status</th>
                <th style="text-align:right">Win %</th>
                <th style="text-align:right">Leads</th>
                {#if data.can_edit}<th></th>{/if}
              </tr>
            </thead>
            <tbody>
              {#each pipeline.stages as s, i (s.id)}
                <tr>
                  <td class="v2-table-primary" data-m="title">{s.name}</td>
                  <td data-m="tag"
                    ><Pill tone={TYPE_TONE[s.stageType] ?? 'slate'}
                      >{TYPE_LABEL[s.stageType] ?? s.stageType}</Pill
                    ></td
                  >
                  <td data-m="meta">{s.mapsToStatus ? statusLabel(s.mapsToStatus) : '—'}</td>
                  <td class="v2-num" style="text-align:right" data-m="meta">{s.winProbability}%</td>
                  <td class="v2-num" style="text-align:right" data-m="meta"
                    >{count(s.leadCount)} {s.leadCount === 1 ? 'lead' : 'leads'}</td
                  >
                  {#if data.can_edit}
                    <td class="lp-actions-cell">
                      <div class="lp-actions">
                        {#each ['up', 'down'] as direction (direction)}
                          <form method="POST" action="?/moveStage" use:enhance>
                            <input type="hidden" name="pipeline_id" value={pipeline.id} />
                            <input type="hidden" name="id" value={s.id} />
                            <input type="hidden" name="direction" value={direction} />
                            <button
                              class="v2-btn v2-btn-sm lp-icon"
                              type="submit"
                              aria-label="Move {s.name} {direction}"
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
                        <button class="v2-btn v2-btn-sm" type="button" onclick={() => (editing = s)}
                          >Edit</button
                        >
                        <ConfirmAction
                          action="?/deleteStage"
                          label="Delete"
                          confirmLabel="Delete stage"
                          explain={s.leadCount > 0
                            ? 'Refused while leads are in it. Move them to another stage first.'
                            : 'The column leaves the board.'}
                          hidden={{ id: s.id }}
                        />
                      </div>
                    </td>
                  {/if}
                </tr>
              {:else}
                <tr>
                  <td colspan={data.can_edit ? 6 : 5}>
                    <p class="v2-sub" style="margin:6px 0">
                      No stages yet, so the board has nowhere to put a lead.
                    </p>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <p class="v2-sub" style="font-size:11.5px;margin-top:14px;max-width:64ch">
          Lead counts are the leads you can see. A converted lead never blocks deleting a stage; it
          just loses the stage.
        </p>
      {/if}
    {/if}
  </div>
</div>

<style>
  .lp-pipelines {
    display: flex;
    flex-wrap: wrap;
    gap: 7px;
    margin-bottom: 18px;
  }
  .lp-pipeline[aria-current='page'] {
    border-color: var(--v2-ink);
    font-weight: 600;
  }
  .lp-head {
    display: flex;
    gap: 12px;
    align-items: center;
    flex-wrap: wrap;
    padding: 14px 16px;
    margin-bottom: 16px;
  }
  .lp-rename {
    display: flex;
    gap: 7px;
    align-items: center;
    flex-wrap: wrap;
    width: 100%;
  }
  .lp-rename .v2-input {
    flex: 1 1 200px;
  }
  .lp-actions {
    display: flex;
    gap: 6px;
    align-items: center;
    justify-content: flex-end;
    flex-wrap: wrap;
  }
  .lp-stages-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 10px;
  }
  @media (max-width: 768px) {
    /* Thumb-sized: the default small button is too short to hit reliably. */
    .lp-actions :global(.v2-btn),
    .lp-stages-head .v2-btn,
    .lp-pipeline,
    .lp-rename .v2-btn {
      min-height: 44px;
    }
    .lp-icon {
      min-width: 44px;
      justify-content: center;
    }
    .lp-actions {
      justify-content: flex-start;
    }
    /* Its own line under the meta run, not squeezed in beside it. */
    .lp-actions-cell {
      display: block;
      margin: 8px 0 0;
    }
  }
</style>
