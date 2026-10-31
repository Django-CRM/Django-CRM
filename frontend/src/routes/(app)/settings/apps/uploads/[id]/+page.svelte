<script lang="ts">
  /**
   * The consent screen for one uploaded app: what it is, a fixed warning, what
   * it adds, how many custom objects it needs, what it may access, then
   * Install or Update. Every label comes from the uploaded file and is shown
   * as text.
   */
  import { enhance, type SubmitFunction } from '$app/forms';
  import { resolve } from '$app/paths';
  import NextAction from '#lib/v2/components/NextAction.svelte';
  import KeyReveal from '#lib/v2/components/KeyReveal.svelte';
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import SettingsCrumb from '#lib/v2/components/SettingsCrumb.svelte';
  import { asInternalPath } from '#lib/utils/paths.js';
  import { scopeLabel } from '#lib/v2/connected-apps.js';
  import {
    accessSentences,
    appliedTitle,
    keepsKey,
    modeLine,
    noNewKeyLine,
    NOT_REVIEWED,
    objectsLine,
    previousScopes,
    TRUST_LABEL
  } from '#lib/v2/private-apps.js';
  import type { PageProps } from './$types';

  let { data, form }: PageProps = $props();

  let busy = $state(false);
  let trusted = $state(false);

  const submitting: SubmitFunction = () => {
    busy = true;
    return async ({ update }) => {
      await update();
      busy = false;
    };
  };

  const back = '/settings/apps';
  let applied = $derived(form && 'applied' in form ? form.applied : null);
  let preview = $derived('preview' in data ? data.preview : null);
  let asksAccess = $derived(preview ? preview.access.scopes.length > 0 : false);
  let blocked = $derived(preview ? preview.blocked.length > 0 : false);
  let hadAccess = $derived(preview ? previousScopes(preview.access).length > 0 : false);
  let verb = $derived(preview?.mode === 'upgrade' ? 'Update' : 'Install');
  let noKeyLine = $derived(
    applied ? noNewKeyLine(applied.mode, applied.asksAccess, applied.hadAccess) : ''
  );
  let placesLine = $derived(preview ? objectsLine(preview.objects) : null);
</script>

<PageHeader title={applied ? appliedTitle(applied.mode) : 'Review an app'}>
  {#snippet crumb()}<SettingsCrumb />{/snippet}
</PageHeader>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:18px;padding-bottom:32px;max-width:720px">
    {#if applied}
      <div class="v2-card own-box" role="status">
        <b class="own-wrap">{applied.name}</b> is at version {applied.version}.
      </div>
      {#if applied.key}
        <KeyReveal title="Key for {applied.name}" secret={applied.key} />
      {:else if noKeyLine}
        <p class="v2-sub">{noKeyLine}</p>
      {/if}
      <a class="v2-btn own-tap" href={resolve(asInternalPath(back))}>Back to Apps</a>
    {:else if 'forbidden' in data && data.forbidden}
      <NextAction label="Admins only" text="Only an organization admin can install apps." />
    {:else if 'stale' in data}
      <div class="v2-card own-box" role="status">{data.stale}</div>
      <a class="v2-btn own-tap" href={resolve(asInternalPath(back))}>Back to Apps</a>
    {:else if 'upload' in data && preview}
      <section class="v2-card own-box">
        <h2 class="own-h own-wrap">{data.upload.name}</h2>
        {#if data.upload.description}
          <p class="v2-sub own-line own-wrap">{data.upload.description}</p>
        {/if}
        <p class="v2-sub own-line">
          {modeLine(preview.mode, data.upload.version, data.installedVersion)}
        </p>
      </section>

      <p class="own-warn" role="note">{NOT_REVIEWED}</p>

      <h3 class="own-h">What it adds</h3>
      {#each data.groups as group (group.type)}
        <div class="v2-label" style="margin:12px 0 4px">{group.label}</div>
        <ul class="own-items">
          {#each group.items as item, n (n)}
            <li>
              <span class="own-wrap">{item.label}</span>
              {#if item.target}<span class="v2-sub"> on {item.target}</span>{/if}
              <span class="v2-sub own-effect">{item.effectLabel}</span>
            </li>
          {/each}
        </ul>
      {:else}
        <p class="v2-sub own-line">Nothing new to add.</p>
      {/each}

      {#if placesLine}
        <h3 class="own-h">Custom objects</h3>
        <p class="own-line">{placesLine}</p>
      {/if}

      <h3 class="own-h">What it may access</h3>
      {#if asksAccess}
        <ul class="own-items">
          {#each accessSentences(preview.access.scopes, keepsKey(preview.mode, preview.access)) as line (line)}
            <li>{line}</li>
          {/each}
        </ul>
        <ul class="own-items own-scopes">
          {#each preview.access.scopes as scope (scope)}
            <li>
              {scopeLabel(scope)}
              {#if preview.access.added.includes(scope)}<span class="v2-sub"> (new)</span>{/if}
            </li>
          {/each}
          {#each preview.access.removed as scope (scope)}
            <li class="v2-sub">{scopeLabel(scope)} (removed)</li>
          {/each}
        </ul>
      {:else}
        <p class="own-line">
          It asks for no access. {preview.access.removed.length
            ? 'Its key stops working.'
            : 'No key is made.'}
        </p>
      {/if}

      {#if blocked}
        <div class="mod-errors own-blocked" role="alert">
          <b>This cannot be installed yet.</b>
          <ul>
            {#each preview.blocked as reason, n (n)}
              <li class="own-wrap">{reason}</li>
            {/each}
          </ul>
        </div>
      {/if}

      {#if form && 'error' in form && form.error}
        <p class="v2-error" role="alert">{form.error}</p>
      {/if}

      <form method="POST" action="?/apply" use:enhance={submitting} class="own-form">
        <input type="hidden" name="mode" value={preview.mode} />
        {#if hadAccess}<input type="hidden" name="had_access" value="1" />{/if}
        {#if asksAccess}
          <input type="hidden" name="asks_access" value="1" />
          <label class="own-trust">
            <input type="checkbox" name="trust" bind:checked={trusted} />
            <span>{TRUST_LABEL}</span>
          </label>
        {/if}
        <div class="own-buttons">
          <button
            class="v2-btn v2-btn-primary own-tap"
            disabled={busy || blocked || (asksAccess && !trusted)}
          >
            {verb}
          </button>
          <a class="v2-btn own-tap" href={resolve(asInternalPath(back))}>Cancel</a>
        </div>
      </form>
    {/if}
  </div>
</div>

<style>
  .own-box {
    padding: 14px 16px;
    margin-bottom: 14px;
    font-size: 13px;
  }
  .own-h {
    font-size: 14px;
    font-weight: 650;
    margin: 20px 0 6px;
  }
  .own-box .own-h {
    margin: 0;
  }
  .own-line {
    font-size: 12.5px;
    margin: 4px 0 0;
    line-height: 1.5;
  }
  .own-wrap {
    overflow-wrap: anywhere;
  }
  .own-warn {
    border: 1px solid color-mix(in srgb, var(--v2-clay) 40%, var(--v2-line));
    background: color-mix(in srgb, var(--v2-clay) 8%, transparent);
    border-radius: 8px;
    padding: 12px 14px;
    font-size: 13px;
    font-weight: 600;
    margin: 0 0 6px;
  }
  .own-items {
    margin: 0;
    padding-left: 18px;
    font-size: 12.5px;
    line-height: 1.6;
  }
  .own-items li {
    overflow-wrap: anywhere;
  }
  .own-effect {
    font-size: 11px;
  }
  .own-effect::before {
    content: ' · ';
  }
  .own-scopes {
    margin-top: 8px;
  }
  .own-blocked {
    margin-top: 18px;
  }
  .own-form {
    margin-top: 20px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .own-trust {
    display: flex;
    gap: 10px;
    align-items: center;
    min-height: 44px;
    font-size: 13px;
  }
  .own-buttons {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
  }
  @media (max-width: 768px) {
    .own-tap {
      min-height: 44px;
      justify-content: center;
    }
    .own-buttons .v2-btn {
      flex: 1 1 140px;
    }
  }
</style>
