<script lang="ts">
  /**
   * One custom object's settings: its names, who sees its records, and its
   * fields. Without the plan only archive, restore and delete for good are
   * offered; everything else reads as plain text. The server refuses what
   * the page hides.
   */
  import { enhance } from '$app/forms';
  import { resolve } from '$app/paths';
  import { asInternalPath } from '#lib/utils/paths.js';
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import SettingsFormPanel from '#lib/v2/components/SettingsFormPanel.svelte';
  import ConfirmAction from '#lib/v2/components/ConfirmAction.svelte';
  import Pill from '#lib/v2/components/Pill.svelte';
  import EmptyState from '#lib/v2/components/EmptyState.svelte';
  import { FIELD_TYPE_LABEL } from '#lib/v2/enums.js';
  import { billingHref } from '#lib/v2/billing.js';
  import { boardFieldOptions } from '#lib/v2/object-board.js';
  import { ChevronLeft, Plus } from '@lucide/svelte';
  import type { PageProps } from './$types';
  import type { ObjectField, ObjectRef } from '#lib/server/v2/custom-objects.js';

  const TYPE_LABEL = { ...FIELD_TYPE_LABEL, link: 'Link' } as Record<string, string>;

  let { data, form }: PageProps = $props();

  let def = $derived(data.definition);
  let plan = $derived(data.customObjects?.has_custom_objects === true);
  let canWrite = $derived(data.can_edit && plan);
  let plansHref = $derived(billingHref(data));
  let linkTargets: ObjectRef[] = $derived(data.customObjects?.link_targets ?? []);

  let boardOptions = $derived(boardFieldOptions(data.fields));
  let boardField = $derived(def.board_field);

  let editingObject = $state(false);
  // `null` when closed, `'new'` when adding, or the field being edited.
  let editing = $state<'new' | ObjectField | null>(null);
  let fieldType = $state('text');
  let optionRows = $state<{ value: string; label: string }[]>([]);

  function openNewField() {
    editing = 'new';
    fieldType = 'text';
    optionRows = [];
  }

  function openEditField(f: ObjectField) {
    editing = f;
    fieldType = f.field_type;
    optionRows = (f.options ?? []).map((o) => ({
      value: o.value,
      label: o.label
    }));
  }

  const targetLabel = (target: string | null) =>
    linkTargets.find((t) => t.key === target)?.label_plural ?? target;

  // `fieldOff` and `fieldOn` are `true` after a success and `{ error }` after a refusal.
  let rowErrors = $derived(
    [form?.fieldOff, form?.fieldOn]
      .map((outcome) => (typeof outcome === 'object' ? outcome.error : undefined))
      .concat([form?.restore?.error, form?.archive?.error, form?.destroy?.error])
      .filter(Boolean)
  );
</script>

<PageHeader title={def.label_plural}>
  {#snippet crumb()}
    <a class="o-back" href={resolve(asInternalPath('/settings/objects'))}
      ><ChevronLeft size={13} />Custom objects</a
    >
  {/snippet}
  {#snippet sub()}
    <code class="o-key">{def.key}</code>
    {#if !def.is_active}<Pill tone="slate">Archived</Pill>{/if}
  {/snippet}
  {#snippet actions()}
    {#if data.can_edit}
      {#if def.is_active}
        <ConfirmAction
          action="?/archive"
          label="Archive"
          confirmLabel="Archive"
          explain="Hides it from the sidebar and its records. Nothing is deleted."
        />
      {:else}
        <form method="POST" action="?/restore" use:enhance>
          <button class="v2-btn" type="submit">Restore</button>
        </form>
        <ConfirmAction
          action="?/destroy"
          label="Delete for good"
          confirmLabel="Delete for good"
          explain="Removes the object and its fields for good."
        />
      {/if}
    {/if}
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:16px;padding-bottom:32px">
    {#each rowErrors as message (message)}
      <p class="v2-error" style="margin:0 0 12px">{message}</p>
    {/each}

    {#if data.can_edit && data.customObjects?.has_custom_objects === false}
      <p class="v2-sub" style="font-size:12.5px;margin:0 0 16px;max-width:66ch">
        Custom objects are not included in this organization's plan. Existing objects stay readable,
        and can be archived, restored or deleted.
        {#if plansHref}
          <a href={resolve(asInternalPath(plansHref))}>See plans</a>
        {/if}
      </p>
    {/if}

    {#if editingObject && canWrite}
      <SettingsFormPanel
        title={`Edit ${def.label}`}
        action="?/update"
        error={form?.update?.error}
        submitLabel="Save object"
        oncancel={() => (editingObject = false)}
        ondone={() => (editingObject = false)}
      >
        {#snippet fields()}
          <div class="v2-field">
            <label for="o-label">Name (singular)</label>
            <input
              id="o-label"
              class="v2-input"
              name="label"
              maxlength="64"
              required
              value={def.label}
            />
          </div>
          <div class="v2-field">
            <label for="o-plural">Name (plural)</label>
            <input
              id="o-plural"
              class="v2-input"
              name="label_plural"
              maxlength="64"
              required
              value={def.label_plural}
            />
          </div>
          <div class="v2-field">
            <label for="o-name-label">What each record's name is called</label>
            <input
              id="o-name-label"
              class="v2-input"
              name="name_label"
              maxlength="64"
              value={def.name_label ?? ''}
            />
          </div>
          <div class="v2-field v2-sfp-wide">
            <label for="o-desc">Description</label>
            <textarea id="o-desc" class="v2-input" name="description" rows="2"
              >{def.description ?? ''}</textarea
            >
          </div>
          <fieldset class="v2-field v2-sfp-wide" style="border:0;padding:0;margin:0">
            <legend class="v2-label" style="padding:0">Who can see its records</legend>
            <label class="o-choice">
              <input
                type="radio"
                name="record_access"
                value="own"
                checked={def.record_access !== 'everyone'}
              />
              Only the people involved
            </label>
            <label class="o-choice">
              <input
                type="radio"
                name="record_access"
                value="everyone"
                checked={def.record_access === 'everyone'}
              />
              Everyone in this organization
            </label>
          </fieldset>
        {/snippet}
      </SettingsFormPanel>
    {:else}
      <div class="v2-card o-summary">
        <dl>
          <div>
            <dt class="v2-label">Name</dt>
            <dd>{def.label} / {def.label_plural}</dd>
          </div>
          <div>
            <dt class="v2-label">Each record's name is called</dt>
            <dd>{def.name_label || 'Name'}</dd>
          </div>
          <div>
            <dt class="v2-label">Who can see its records</dt>
            <dd>
              {def.record_access === 'everyone'
                ? 'Everyone in this organization'
                : 'Only the people involved'}
            </dd>
          </div>
          {#if def.description}
            <div>
              <dt class="v2-label">Description</dt>
              <dd>{def.description}</dd>
            </div>
          {/if}
        </dl>
        {#if canWrite}
          <button class="v2-btn" type="button" onclick={() => (editingObject = true)}>
            Edit object
          </button>
        {/if}
      </div>
    {/if}

    {#if data.can_edit && def.is_active}
      <form
        class="v2-card o-board"
        method="POST"
        action="?/board"
        use:enhance={() =>
          async ({ update }) =>
            update({ reset: false })}
      >
        <div class="v2-field" style="margin:0">
          <label for="o-board">Board</label>
          <select
            id="o-board"
            class="v2-input"
            name="board_field"
            disabled={!plan}
            value={boardOptions.some((o) => o.value === boardField) ? boardField : ''}
          >
            <option value="">None</option>
            {#each boardOptions as o (o.value)}
              <option value={o.value}>{o.label}</option>
            {/each}
          </select>
          <p class="v2-hint">
            {boardOptions.length > 0
              ? 'Shows records as cards in columns, one per choice of this dropdown field.'
              : 'Add a dropdown field to use it as a board.'}
          </p>
          {#if form?.board?.error}<p class="v2-error" role="alert">{form.board.error}</p>{/if}
        </div>
        <button class="v2-btn" type="submit" disabled={!plan}>Save board</button>
      </form>
    {/if}

    <div class="o-fields-head">
      <h2 class="v2-label" style="margin:0">Fields</h2>
      {#if canWrite && !editing}
        <button class="v2-btn v2-btn-sm o-add" type="button" onclick={openNewField}>
          <Plus />New field
        </button>
      {/if}
    </div>

    {#if editing && canWrite}
      {@const current = editing}
      <SettingsFormPanel
        title={current === 'new' ? 'New field' : `Edit ${current.label}`}
        action={current === 'new' ? '?/createField' : '?/updateField'}
        error={current === 'new' ? form?.createField?.error : form?.updateField?.error}
        submitLabel={current === 'new' ? 'Add field' : 'Save field'}
        oncancel={() => (editing = null)}
        ondone={() => (editing = null)}
      >
        {#snippet fields()}
          {#if current !== 'new'}
            <input type="hidden" name="field_key" value={current.key} />
            <input type="hidden" name="field_type" value={current.field_type} />
          {/if}

          <div class="v2-field">
            <label for="f-label">Label</label>
            <input
              id="f-label"
              class="v2-input"
              name="label"
              maxlength="128"
              required
              value={current === 'new' ? '' : current.label}
            />
          </div>

          <div class="v2-field">
            <label for="f-key">Key</label>
            {#if current === 'new'}
              <input
                id="f-key"
                class="v2-input"
                name="key"
                maxlength="64"
                pattern="[a-z][a-z0-9_]*"
                required
                placeholder="price"
              />
              <p class="v2-hint">
                Lowercase letters, numbers and underscores, starting with a letter. Cannot be
                changed later.
              </p>
            {:else}
              <code class="o-key">{current.key}</code>
              <p class="v2-hint">Fixed after creation.</p>
            {/if}
          </div>

          <div class="v2-field">
            <label for="f-type">Type</label>
            {#if current === 'new'}
              <select id="f-type" class="v2-input" name="field_type" bind:value={fieldType}>
                {#each Object.entries(TYPE_LABEL) as [value, label] (value)}
                  <option {value}>{label}</option>
                {/each}
              </select>
            {:else}
              <div style="font-size:13px">{TYPE_LABEL[current.field_type]}</div>
              <p class="v2-hint">Fixed after creation.</p>
            {/if}
          </div>

          {#if fieldType === 'link'}
            {#if current === 'new'}
              <div class="v2-field">
                <label for="f-target">Links to</label>
                <select id="f-target" class="v2-input" name="target" required>
                  {#each linkTargets as t (t.key)}
                    <option value={t.key}>{t.label_plural}</option>
                  {/each}
                </select>
              </div>
              <div class="v2-field">
                <label for="f-many">How many</label>
                <select id="f-many" class="v2-input" name="many">
                  <option value="false">One record</option>
                  <option value="true">Several records</option>
                </select>
              </div>
            {:else}
              <div class="v2-field">
                <label for="f-linked">Links to</label>
                <div id="f-linked" style="font-size:13px">
                  {targetLabel(current.target)}, {current.many ? 'several records' : 'one record'}
                </div>
                <p class="v2-hint">Fixed after creation.</p>
              </div>
            {/if}
          {/if}

          {#if fieldType === 'dropdown'}
            <div class="v2-field v2-sfp-wide">
              <label for="f-choices">Choices</label>
              {#each optionRows as row, i (i)}
                <div class="o-option">
                  <input type="hidden" name="option_value" value={row.value} />
                  <input
                    id={i === 0 ? 'f-choices' : undefined}
                    class="v2-input"
                    name="option_label"
                    bind:value={row.label}
                    required
                  />
                  <button
                    class="v2-btn v2-btn-sm"
                    type="button"
                    onclick={() => (optionRows = optionRows.filter((_, j) => j !== i))}
                  >
                    Remove
                  </button>
                </div>
              {/each}
              <button
                class="v2-btn v2-btn-sm o-add"
                type="button"
                style="align-self:flex-start"
                onclick={() => (optionRows = [...optionRows, { value: '', label: '' }])}
              >
                Add a choice
              </button>
            </div>
          {/if}

          <div class="v2-field">
            <label for="f-order">Order</label>
            <input
              id="f-order"
              class="v2-input"
              type="number"
              name="display_order"
              min="0"
              value={current === 'new' ? '' : current.display_order}
            />
          </div>

          {#if fieldType !== 'link'}
            <div class="v2-field">
              <label for="f-required">Required</label>
              <label class="o-choice">
                <input
                  id="f-required"
                  type="checkbox"
                  name="is_required"
                  value="true"
                  checked={current !== 'new' && current.is_required}
                />
                A record cannot be saved without a value.
              </label>
            </div>
          {/if}
        {/snippet}
      </SettingsFormPanel>
    {/if}

    {#if !data.fields.length}
      <EmptyState
        title="No fields yet"
        body="Fields are what each record holds, such as a price, a date or a link to a contact."
      />
    {:else}
      <div class="v2-card" style="overflow:hidden">
        {#each data.fields as f (f.key)}
          <div class="v2-setting o-field" style="opacity:{f.is_active ? 1 : 0.6}">
            <div class="v2-setting-body">
              <div style="display:flex;gap:7px;align-items:baseline;flex-wrap:wrap">
                <b style="display:inline">{f.label}</b>
                <code class="o-key">{f.key}</code>
              </div>
              <span class="v2-sub" style="font-size:11.5px">
                {TYPE_LABEL[f.field_type] ?? f.field_type}
                {#if f.field_type === 'link'}
                  · {targetLabel(f.target)}, {f.many ? 'several' : 'one'}
                {/if}
                {#if f.options}
                  · {f.options.map((o) => o.label).join(', ')}
                {/if}
              </span>
            </div>
            {#if !f.is_active}
              <Pill tone="slate">Off</Pill>
            {:else if f.is_required}
              <Pill tone="clay">Required</Pill>
            {/if}
            {#if canWrite}
              <div class="o-actions">
                <button class="v2-btn v2-btn-sm" type="button" onclick={() => openEditField(f)}>
                  Edit
                </button>
                {#if f.is_active}
                  <ConfirmAction
                    action="?/fieldOff"
                    label="Turn off"
                    confirmLabel="Turn off"
                    explain="Stops being collected. Stored values stay."
                    hidden={{ field_key: f.key }}
                  />
                {:else}
                  <form method="POST" action="?/fieldOn" use:enhance>
                    <input type="hidden" name="field_key" value={f.key} />
                    <button class="v2-btn v2-btn-sm" type="submit">Turn on</button>
                  </form>
                {/if}
              </div>
            {/if}
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>

<style>
  .o-board {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: flex-end;
    padding: 12px 16px;
    margin-top: 16px;
  }
  .o-board .v2-field {
    flex: 1 1 220px;
    min-width: 0;
  }
  @media (max-width: 767px) {
    .o-board .v2-btn {
      min-height: 44px;
    }
  }
  .o-back {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    color: inherit;
    text-decoration: none;
  }
  .o-back:hover {
    color: var(--v2-ink);
    text-decoration: underline;
  }
  .o-key {
    font-family: var(--v2-mono);
    font-size: 11px;
    color: var(--v2-slate);
    background: var(--v2-hover);
    border-radius: 3px;
    padding: 1px 4px;
  }
  .o-choice {
    display: flex;
    gap: 8px;
    align-items: center;
    font-weight: 400;
    min-height: 32px;
  }
  .o-summary {
    padding: 16px 17px;
    margin-bottom: 18px;
    display: flex;
    gap: 16px;
    align-items: flex-start;
    justify-content: space-between;
  }
  .o-summary dl {
    margin: 0;
    display: grid;
    gap: 10px;
    min-width: 0;
  }
  .o-summary dd {
    margin: 2px 0 0;
    font-size: 13px;
  }
  .o-fields-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin: 4px 0 10px;
  }
  .o-option {
    display: flex;
    gap: 7px;
    align-items: center;
    margin-bottom: 6px;
  }
  .o-actions {
    display: flex;
    gap: 6px;
    align-items: center;
    flex: none;
  }

  @media (max-width: 768px) {
    .o-summary {
      flex-direction: column;
    }
    .o-field {
      flex-wrap: wrap;
    }
    .o-actions {
      width: 100%;
      justify-content: flex-end;
      flex-wrap: wrap;
    }
    .o-actions :global(.v2-btn),
    .o-back,
    .o-choice,
    .o-add,
    .o-option :global(.v2-btn) {
      min-height: 44px;
    }
  }
</style>
