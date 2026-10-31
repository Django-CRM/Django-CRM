<script lang="ts">
  /**
   * Settings › Apps: industry apps to install, update and remove, then the
   * modules (switched off and ordered as before), then sample data. Install,
   * Update and Remove open a confirmation built from the server's preview;
   * the apps section is hidden on an API without apps. Forms use plain
   * `use:enhance`, so the shell reloads and the menus change at once.
   */
  import { enhance, type SubmitFunction } from '$app/forms';
  import { resolve } from '$app/paths';
  import { ArrowDown, ArrowUp, Boxes, Trash2 } from '@lucide/svelte';
  import ConfirmAction from '#lib/v2/components/ConfirmAction.svelte';
  import EmptyState from '#lib/v2/components/EmptyState.svelte';
  import NextAction from '#lib/v2/components/NextAction.svelte';
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import KeyReveal from '#lib/v2/components/KeyReveal.svelte';
  import Pill from '#lib/v2/components/Pill.svelte';
  import SettingsCrumb from '#lib/v2/components/SettingsCrumb.svelte';
  import { updateLine } from '#lib/v2/apps.js';
  import { replaceSentence } from '#lib/v2/connected-apps.js';
  import { removeSentence } from '#lib/v2/private-apps.js';
  import { shortDate } from '#lib/v2/format.js';
  import { labelOf, type ModuleRow } from '#lib/v2/modules.js';
  import { asInternalPath } from '#lib/utils/paths.js';
  import type { App } from '#lib/server/v2/apps.js';
  import type { PageProps } from './$types';

  let { data, form }: PageProps = $props();

  const GROUPS = [
    { key: 'sell', label: 'Sell' },
    { key: 'serve', label: 'Serve' },
    { key: 'bill', label: 'Bill' }
  ];

  let rows: ModuleRow[] = $derived(data.modules ?? []);

  let groups = $derived(
    GROUPS.map((g) => ({ ...g, rows: rows.filter((r) => r.group === g.key) })).filter(
      (g) => g.rows.length
    )
  );

  let errors = $derived(Array.isArray(form?.errors) ? form.errors : []);
  let formsLink = $derived(form?.key === 'leads' || form?.key === 'tickets');
  let mailboxLink = $derived(form?.key === 'tickets');
  // Shown under the row it is about, so a phone user sees it where they tapped.
  let refusedKey = $derived(rows.some((r) => r.key === form?.key) ? form?.key : null);

  let apps = $derived(data.apps?.apps ?? []);
  let confirm = $derived(data.confirm ?? null);
  let doneApp = $derived(apps.find((a) => a.id === data.done?.app) ?? null);
  let busy = $state(false);
  let privateOffered = $derived(data.privateApps?.offered === true && !data.privateApps.forbidden);
  let mine = $derived(data.privateApps?.offered ? data.privateApps.apps : []);
  let uploadErrors = $derived(
    form && 'uploadErrors' in form && form.uploadErrors
      ? Object.entries(form.uploadErrors as Record<string, string[]>)
      : []
  );
  const uploadPage = (id: string) => `/settings/apps/uploads/${encodeURIComponent(id)}`;
  let confirmingClear = $state(false);

  const DONE = {
    install: 'Installed',
    upgrade: 'Updated',
    remove: 'Removed'
  } as Record<string, string>;
  const VERB = {
    install: 'Install',
    upgrade: 'Update',
    remove: 'Remove'
  } as Record<string, string>;

  function statusLine(app: App) {
    if (app.status === 'removed') return 'Removed. Installing it again brings its parts back.';
    if (app.status !== 'installed') return '';
    const when = app.installedAt ? ` ${shortDate(app.installedAt)}` : '';
    const who = app.installedBy ? ` by ${app.installedBy}` : '';
    return `Installed${when}${who}.`;
  }

  const ask = (id: string, action: string) =>
    `/settings/apps?app=${encodeURIComponent(id)}&do=${action}`;

  const submitting: SubmitFunction = () => {
    busy = true;
    return async ({ update }) => {
      await update();
      busy = false;
    };
  };

  const clearSubmit: SubmitFunction = () => {
    busy = true;
    return async ({ update, result }) => {
      await update();
      busy = false;
      if (result?.type === 'success') confirmingClear = false;
    };
  };

  function needOff(row: ModuleRow) {
    const need = row.needs ? rows.find((r) => r.key === row.needs) : null;
    return need ? !need.available : false;
  }
</script>

<PageHeader title="Apps">
  {#snippet crumb()}<SettingsCrumb />{/snippet}
  {#snippet sub()}
    Install an industry app to set BottleCRM up for one kind of business, and switch off the modules
    this organization does not use. Neither ever deletes a record.
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:18px;padding-bottom:32px">
    {#if data.forbidden}
      <NextAction
        label="Admins only"
        text="Only an organization admin can install apps or switch modules on or off."
      />
    {:else}
      {#if data.done && doneApp}
        <div class="app-done" role="status">
          {DONE[data.done.action]}
          {doneApp.name}.
          {#if data.done.action === 'remove'}
            Every record stays, and installing it again brings its parts back.
          {/if}
        </div>
      {/if}

      {#if form?.appError && confirm?.app !== form?.app}
        <p class="v2-error" role="alert" style="margin:0 0 14px">{form.appError}</p>
      {/if}

      {#if data.apps?.state !== 'absent'}
        <h2 class="app-h">Industry apps</h2>
        {#if data.apps?.state === 'failed'}
          <div class="v2-card app-note">
            Apps could not be loaded. Reload the page to try again.
          </div>
        {:else if apps.length === 0}
          <div class="v2-card app-note">No industry apps are available right now.</div>
        {:else}
          <div class="v2-card" style="overflow:hidden;margin-bottom:22px">
            {#each apps as app (app.id)}
              {@const open = confirm?.app === app.id ? confirm : null}
              <div class="mod-item">
                <div class="mod-row">
                  <div class="mod-body">
                    <div class="mod-title">
                      <b>{app.name}</b>
                      {#if app.status === 'installed'}
                        <Pill tone="moss"
                          >Installed{app.installedVersion ? ` v${app.installedVersion}` : ''}</Pill
                        >
                      {:else if app.status === 'removed'}
                        <Pill tone="slate">Removed</Pill>
                      {/if}
                    </div>
                    <span class="v2-sub" style="font-size:11.5px">{app.description}</span>
                    {#if statusLine(app)}
                      <span class="v2-sub" style="font-size:11.5px">{statusLine(app)}</span>
                    {/if}
                    {#if app.status === 'installed' && app.updateAvailable}
                      <span class="v2-sub app-update">
                        {updateLine(app)}
                      </span>
                    {/if}
                    {#if app.status === 'installed' && app.skippedModules.length}
                      <span class="v2-sub" style="font-size:11.5px">
                        Skipped while switched off: {app.skippedModules
                          .map((k: string) => labelOf(data.modules, k))
                          .join(', ')}.
                      </span>
                    {/if}
                  </div>
                  {#if !open}
                    <div class="mod-actions">
                      {#if app.status === 'installed'}
                        {#if app.updateAvailable}
                          <a
                            class="v2-btn v2-btn-sm app-tap"
                            href={resolve(asInternalPath(ask(app.id, 'upgrade')))}>Update</a
                          >
                        {/if}
                        <a
                          class="v2-btn v2-btn-sm app-tap"
                          href={resolve(asInternalPath(ask(app.id, 'remove')))}>Remove</a
                        >
                      {:else}
                        <a
                          class="v2-btn v2-btn-sm app-tap"
                          href={resolve(asInternalPath(ask(app.id, 'install')))}>Install</a
                        >
                      {/if}
                    </div>
                  {/if}
                </div>

                {#if open}
                  <div class="app-confirm">
                    {#if open.error}
                      <p class="v2-error" role="alert" style="margin:0 0 10px">{open.error}</p>
                    {:else}
                      <div style="font-weight:600;font-size:13px;margin-bottom:6px">
                        {VERB[open.action]}
                        {app.name}?
                      </div>
                      {#if open.action === 'remove'}
                        <p class="v2-sub app-lede">
                          Its fields are turned off and its lead sources and products made inactive.
                          Pipelines, tags and every record stay. Installing it again brings it all
                          back.
                        </p>
                      {:else if open.action === 'upgrade'}
                        <p class="v2-sub app-lede">
                          Only what is new is added. Nothing this organization already has changes.
                        </p>
                      {:else}
                        <p class="v2-sub app-lede">
                          This is everything it adds. Anything already here is kept as it is.
                        </p>
                      {/if}
                      {#each open.preview?.groups ?? [] as group (group.type)}
                        <div class="v2-label" style="margin:12px 0 4px">{group.label}</div>
                        <ul class="app-items">
                          {#each group.items as item, n (n)}
                            <li class:app-hand={item.effect === 'add_by_hand'}>
                              <span>{item.label}</span>
                              {#if item.target}<span class="v2-sub"> on {item.target}</span>{/if}
                              <span class="v2-sub app-effect">{item.effectLabel}</span>
                            </li>
                          {/each}
                        </ul>
                      {:else}
                        <p class="v2-sub app-lede">Nothing to {VERB[open.action].toLowerCase()}.</p>
                      {/each}
                    {/if}

                    {#if form?.appError && form?.app === app.id}
                      <p class="v2-error" role="alert" style="margin:12px 0 0">{form.appError}</p>
                    {/if}

                    <form
                      method="POST"
                      action="?/{open.action}"
                      use:enhance={submitting}
                      class="app-confirm-actions"
                    >
                      <input type="hidden" name="app" value={app.id} />
                      {#if open.action === 'install' && open.preview?.sampleData.offered}
                        <label class="app-sample">
                          <input
                            type="checkbox"
                            name="sample_data"
                            checked={open.preview.sampleData.defaultOn}
                          />
                          <span>
                            Add sample records{open.preview.sampleData.records
                              ? ` (${open.preview.sampleData.records})`
                              : ''}
                            <span class="v2-sub" style="display:block;font-size:11.5px">
                              Marked as samples, so "Clear sample data" below removes them.
                            </span>
                          </span>
                        </label>
                      {/if}
                      <div class="app-buttons">
                        {#if !open.error}
                          <button class="v2-btn v2-btn-sm v2-btn-primary" disabled={busy}>
                            {VERB[open.action]}
                          </button>
                        {/if}
                        <a class="v2-btn v2-btn-sm" href={resolve(asInternalPath('/settings/apps'))}
                          >Cancel</a
                        >
                      </div>
                    </form>
                  </div>
                {/if}
              </div>
            {/each}
          </div>
        {/if}
      {/if}

      {#if form && 'revealed' in form && form.revealed}
        <KeyReveal title="New key for {form.revealed.name}" secret={form.revealed.key} />
      {/if}
      {#if form && 'removedPrivate' in form && form.removedPrivate}
        <div class="app-done" role="status">
          Removed {form.removedPrivate}. Every record stays; install it again from the list below.
        </div>
      {/if}

      {#if privateOffered}
        <h2 class="app-h">Your own apps</h2>
        {#if form && 'privateError' in form && form.privateError}
          <p class="v2-error" role="alert" style="margin:0 0 14px">{form.privateError}</p>
        {/if}
        {#if mine.length}
          <div class="v2-card" style="overflow:hidden;margin-bottom:14px">
            {#each mine as app (app.id)}
              <div class="mod-item">
                <div class="mod-row">
                  <div class="mod-body">
                    <div class="mod-title">
                      <b class="own-name">{app.name}</b>
                      <Pill tone={app.status === 'removed' ? 'slate' : 'moss'}>
                        {app.status === 'removed' ? 'Removed' : 'Installed'} v{app.version}
                      </Pill>
                    </div>
                    {#if app.description}
                      <span class="v2-sub own-text">{app.description}</span>
                    {/if}
                    <span class="v2-sub own-text">
                      Objects: {app.objects.length
                        ? app.objects.map((o) => o.label).join(', ')
                        : 'none'}.
                      {app.connection ? 'Holds a key.' : 'Holds no key.'}
                    </span>
                    {#if app.newer_upload && app.status !== 'removed'}
                      <span class="v2-sub app-update">
                        Version {app.newer_upload.version} is waiting.
                      </span>
                    {/if}
                  </div>
                  <div class="mod-actions">
                    {#if app.status === 'removed'}
                      {#if app.upload_id}
                        <a
                          class="v2-btn v2-btn-sm app-tap"
                          href={resolve(asInternalPath(uploadPage(app.upload_id)))}>Install again</a
                        >
                      {/if}
                    {:else}
                      <a
                        class="v2-btn v2-btn-sm app-tap"
                        href={resolve(
                          asInternalPath(
                            app.newer_upload
                              ? uploadPage(app.newer_upload.id)
                              : '/settings/apps#upload-own'
                          )
                        )}>Update</a
                      >
                      {#if app.connection}
                        <ConfirmAction
                          action="?/replaceKey"
                          label="Replace key"
                          confirmLabel="Replace key"
                          explain={replaceSentence(app.name)}
                          hidden={{ id: app.id }}
                        />
                      {/if}
                      <ConfirmAction
                        action="?/removePrivate"
                        label="Remove"
                        confirmLabel="Remove"
                        explain={removeSentence(app.name, {
                          objects: app.objects.length > 0,
                          key: Boolean(app.connection)
                        })}
                        hidden={{ id: app.id }}
                      />
                    {/if}
                  </div>
                </div>
              </div>
            {/each}
          </div>
        {:else}
          <div class="v2-card app-note">
            No apps of your own yet. Upload the file for one below; you will see what it adds before
            anything is installed.
          </div>
        {/if}

        <form
          id="upload-own"
          method="POST"
          action="?/upload"
          enctype="multipart/form-data"
          use:enhance={submitting}
          class="v2-card own-upload"
        >
          <div style="font-weight:600;font-size:13px">Upload an app</div>
          <p class="v2-sub app-lede">
            A .json file, or paste its text. A new version of an app you have installed updates it.
          </p>
          <label class="v2-label" for="own-file">File</label>
          <input id="own-file" name="file" type="file" accept=".json,application/json" />
          <label class="v2-label" for="own-text">Or paste the text</label>
          <textarea id="own-text" name="manifest" rows="6" class="v2-input own-text-area"
          ></textarea>
          {#if form && 'uploadError' in form && form.uploadError}
            <p class="v2-error" role="alert" style="margin:0">{form.uploadError}</p>
          {/if}
          {#if uploadErrors.length}
            <div class="mod-errors" role="alert" style="margin:0">
              <ul>
                {#each uploadErrors as [path, messages] (path)}
                  <li><code>{path}</code>: {messages.join(' ')}</li>
                {/each}
              </ul>
            </div>
          {/if}
          <div class="app-buttons">
            <button class="v2-btn v2-btn-sm v2-btn-primary app-tap" disabled={busy}>
              Review the app
            </button>
          </div>
        </form>
        <div style="margin-bottom:22px"></div>
      {/if}

      <h2 class="app-h">Modules</h2>
      {#if data.modulesFailed}
        <EmptyState title="Modules could not be loaded." body="Reload the page to try again.">
          {#snippet icon()}<Boxes size={21} />{/snippet}
        </EmptyState>
      {/if}
      {#snippet refusal()}
        <div class="mod-errors" role="alert">
          <ul>
            {#each errors as message, n (n)}
              <li>{message}</li>
            {/each}
          </ul>
          {#if formsLink || mailboxLink}
            <p>
              {#if formsLink}<a href={resolve(asInternalPath('/settings/web-forms'))}
                  >Open web forms</a
                >{/if}
              {#if mailboxLink}<a href={resolve(asInternalPath('/settings/inbound-email'))}
                  >Open inbound email</a
                >{/if}
            </p>
          {/if}
        </div>
      {/snippet}

      {#if errors.length && !refusedKey}
        {@render refusal()}
      {/if}

      {#each groups as g (g.key)}
        <div class="v2-label" style="margin-bottom:10px">{g.label}</div>
        <div class="v2-card" style="overflow:hidden;margin-bottom:22px">
          {#each g.rows as row, i (row.key)}
            {@const blocked = needOff(row)}
            <div class="mod-item">
              <div class="mod-row">
                <div class="mod-body">
                  <div class="mod-title">
                    <b>{row.label}</b>
                    <Pill tone={row.available ? 'moss' : 'slate'}
                      >{row.available ? 'On' : 'Off'}</Pill
                    >
                  </div>
                  <span class="v2-sub" style="font-size:11.5px">{row.covers}</span>
                  {#if blocked && row.needs}
                    <span class="v2-sub" style="font-size:11.5px">
                      Needs {labelOf(data.modules, row.needs)}.{row.enabled
                        ? ` It comes back when ${labelOf(data.modules, row.needs)} is switched on.`
                        : ''}
                    </span>
                  {/if}
                </div>
                <div class="mod-actions">
                  {#if row.available}
                    <ConfirmAction
                      action="?/switch"
                      label="Switch off"
                      confirmLabel="Switch off"
                      explain={row.stops}
                      hidden={{ key: row.key, on: 'false' }}
                    />
                  {:else if !blocked}
                    <form method="POST" action="?/switch" use:enhance>
                      <input type="hidden" name="key" value={row.key} />
                      <input type="hidden" name="on" value="true" />
                      <button class="v2-btn v2-btn-sm" type="submit">Switch on</button>
                    </form>
                  {/if}
                  <div class="mod-order">
                    {#each ['up', 'down'] as direction (direction)}
                      <form method="POST" action="?/move" use:enhance>
                        <input type="hidden" name="key" value={row.key} />
                        <input type="hidden" name="direction" value={direction} />
                        <button
                          class="v2-btn v2-btn-sm mod-icon"
                          type="submit"
                          aria-label="Move {row.label} {direction}"
                          disabled={direction === 'up' ? i === 0 : i === g.rows.length - 1}
                        >
                          {#if direction === 'up'}<ArrowUp size={14} />{:else}<ArrowDown
                              size={14}
                            />{/if}
                        </button>
                      </form>
                    {/each}
                  </div>
                </div>
              </div>
              {#if errors.length && refusedKey === row.key}
                {@render refusal()}
              {/if}
            </div>
          {/each}
        </div>
      {/each}

      <h2 class="app-h">Sample data</h2>
      <div class="v2-card danger">
        {#if form?.sampleError}
          <p class="v2-error" role="alert" style="margin:0;flex-basis:100%">{form.sampleError}</p>
        {/if}
        {#if confirmingClear}
          <form
            method="POST"
            action="?/clearSampleData"
            use:enhance={clearSubmit}
            style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"
          >
            <span class="v2-sub" style="font-size:12px">
              Permanently delete every sample record an app created for this organization? This
              cannot be undone. Your real records are never touched, and any sample record you have
              since attached real work to is kept.
            </span>
            <button class="v2-btn danger-btn" type="submit" disabled={busy}>
              <Trash2 size={14} /> Clear sample data
            </button>
            <button
              class="v2-btn"
              type="button"
              disabled={busy}
              onclick={() => (confirmingClear = false)}
            >
              Cancel
            </button>
          </form>
        {:else if form?.cleared !== undefined}
          <span class="v2-sub" style="font-size:12.5px">
            {form.cleared
              ? `Deleted ${form.cleared} sample ${form.cleared === 1 ? 'record' : 'records'}.`
              : 'No sample data to clear.'}
            {#if form.retained}
              Kept {form.retained}
              {form.retained === 1 ? 'record' : 'records'} you have since attached real work to.
            {/if}
          </span>
        {:else}
          <button class="v2-btn danger-btn" type="button" onclick={() => (confirmingClear = true)}>
            <Trash2 size={14} /> Clear sample data
          </button>
          <span class="v2-sub" style="font-size:11.5px">
            Removes only the records an app created as samples. Your real records are never touched.
          </span>
        {/if}
      </div>
    {/if}
  </div>
</div>

<style>
  .app-h {
    font-size: 14px;
    font-weight: 650;
    margin: 0 0 12px;
  }
  .app-done {
    border: 1px solid color-mix(in srgb, var(--v2-moss) 40%, var(--v2-line));
    background: color-mix(in srgb, var(--v2-moss) 8%, transparent);
    border-radius: 8px;
    padding: 12px 14px;
    margin-bottom: 18px;
    font-size: 13px;
  }
  .app-note {
    padding: 14px 16px;
    margin-bottom: 22px;
    font-size: 12.5px;
    color: var(--v2-slate);
  }
  .own-name,
  .own-text {
    overflow-wrap: anywhere;
  }
  .own-text {
    font-size: 11.5px;
  }
  .own-upload {
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .own-text-area {
    width: 100%;
    font-family: var(--v2-mono, monospace);
    font-size: 12px;
  }
  .app-update {
    font-size: 11.5px;
    color: var(--v2-clay);
  }
  .app-confirm {
    border-top: 1px solid var(--v2-line-soft);
    margin: 0 16px;
    padding: 12px 0 14px;
  }
  .app-lede {
    font-size: 12.5px;
    margin: 0;
    line-height: 1.5;
  }
  .app-items {
    margin: 0;
    padding-left: 18px;
    font-size: 12.5px;
    line-height: 1.6;
  }
  .app-items li {
    overflow-wrap: anywhere;
  }
  .app-effect {
    font-size: 11px;
  }
  .app-effect::before {
    content: ' · ';
  }
  .app-hand {
    color: var(--v2-clay);
  }
  .app-confirm-actions {
    margin-top: 14px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .app-sample {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    font-size: 13px;
    min-height: 44px;
    cursor: pointer;
  }
  .app-sample input {
    width: 18px;
    height: 18px;
    flex: none;
    margin-top: 1px;
  }
  .app-buttons {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .danger {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    padding: 14px 16px;
  }
  .danger-btn {
    color: var(--v2-rust);
    border-color: color-mix(in srgb, var(--v2-rust) 32%, transparent);
  }
  .danger-btn:hover {
    background: color-mix(in srgb, var(--v2-rust) 9%, transparent);
  }
  .mod-errors {
    border: 1px solid color-mix(in srgb, var(--v2-rust) 28%, transparent);
    background: color-mix(in srgb, var(--v2-rust) 9%, transparent);
    border-radius: 8px;
    padding: 12px 14px;
    margin-bottom: 18px;
    font-size: 13px;
  }
  .mod-errors ul {
    margin: 0;
    padding-left: 18px;
  }
  .mod-errors p {
    margin: 8px 0 0;
    display: flex;
    gap: 14px;
    flex-wrap: wrap;
  }
  /* Inside a row, the refusal sits under the row it is about. */
  .mod-item .mod-errors {
    margin: 0 16px 12px;
  }
  .mod-item {
    border-top: 1px solid var(--v2-line);
  }
  .mod-item:first-child {
    border-top: 0;
  }
  /* Wraps, so an armed Switch off, whose sentence can run longer than the
     card is wide, drops under the name instead of pushing past the edge. */
  .mod-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px 14px;
    padding: 12px 16px;
  }
  .mod-body {
    flex: 1 1 260px;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .mod-title {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .mod-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 0 1 auto;
    min-width: 0;
    margin-left: auto;
  }
  .mod-order {
    display: flex;
    gap: 6px;
  }
  .mod-icon {
    min-width: 44px;
    min-height: 44px;
    justify-content: center;
  }
  @media (max-width: 768px) {
    .app-tap,
    .app-buttons .v2-btn {
      min-height: 44px;
      justify-content: center;
    }
    .mod-row {
      flex-direction: column;
      align-items: stretch;
    }
    .mod-body {
      flex-basis: auto;
    }
    .mod-actions {
      justify-content: space-between;
      flex-wrap: wrap;
      margin-left: 0;
    }
  }
</style>
