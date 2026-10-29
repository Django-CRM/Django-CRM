<script lang="ts">
  /**
   * Settings › Connected apps. An admin connects an app, approves what it may
   * read and write, copies its key (shown once, in the reply to the request
   * that made it) and can replace the key or disconnect the app. Both
   * destructive actions ask in the page, never in a browser dialog.
   */
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import SettingsCrumb from '#lib/v2/components/SettingsCrumb.svelte';
  import NextAction from '#lib/v2/components/NextAction.svelte';
  import EmptyState from '#lib/v2/components/EmptyState.svelte';
  import { relativeDays, shortDate } from '#lib/v2/format.js';
  import {
    disconnectSentence,
    KEY_NOTICE,
    kindLabel,
    lastUsedLabel,
    MAX_APP_NAME,
    replaceSentence,
    scopeLabel
  } from '#lib/v2/connected-apps.js';
  import { enhance, type SubmitFunction } from '$app/forms';
  import { Plus, Copy, Check } from '@lucide/svelte';
  import type { PageProps } from './$types';

  let { data, form }: PageProps = $props();

  let connecting = $state(false);
  let busy = $state(false);
  let copied = $state(false);
  let kind = $state('');
  let ticked = $state<string[] | null>(null);
  let confirming = $state<{ id: string; action: 'replace' | 'disconnect' } | null>(null);

  const chosen = $derived(
    data.forbidden
      ? undefined
      : data.catalogue.find((c) => c.kind === (kind || data.catalogue[0]?.kind))
  );
  const needsName = $derived(chosen ? chosen.name === null : false);
  const selected = $derived(ticked ?? (data.forbidden ? [] : data.scopes));

  let keyCard = $state<HTMLElement | null>(null);

  // A long list at phone width would leave the new key off-screen.
  $effect(() => {
    if (form?.revealed && keyCard) {
      keyCard.scrollIntoView({ block: 'center' });
      keyCard.focus({ preventScroll: true });
    }
  });

  const toggle = (scope: string, on: boolean) => {
    const base = ticked ?? (data.forbidden ? [] : data.scopes);
    ticked = on ? [...base, scope] : base.filter((s) => s !== scope);
  };

  const run: SubmitFunction = () => {
    busy = true;
    return async ({ update, result }) => {
      await update();
      busy = false;
      confirming = null;
      if (result.type === 'success' && result.data?.revealed) {
        connecting = false;
        ticked = null;
        copied = false;
      }
    };
  };

  async function copyKey(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      copied = true;
      setTimeout(() => (copied = false), 1600);
    } catch {
      // Clipboard blocked: the key is on screen to select by hand.
    }
  }
</script>

{#if data.forbidden}
  <PageHeader title="Connected apps">
    {#snippet crumb()}<SettingsCrumb />{/snippet}
  </PageHeader>
  <div class="v2-pad" style="padding-top:40px">
    <NextAction
      label="Admins only"
      text="Connecting apps is limited to admins, because an app can read and write the organization. Ask an admin to connect one."
    />
  </div>
{:else}
  <PageHeader title="Connected apps">
    {#snippet crumb()}<SettingsCrumb />{/snippet}
    {#snippet sub()}Apps that read and write this organization, each with its own key.{/snippet}
    {#snippet actions()}
      <button class="v2-btn v2-btn-primary" onclick={() => (connecting = !connecting)}>
        <Plus />Connect an app
      </button>
    {/snippet}
  </PageHeader>

  <div class="v2-scroll">
    <div class="v2-pad" style="padding-top:16px;padding-bottom:32px">
      {#if form?.revealed}
        <div
          bind:this={keyCard}
          tabindex="-1"
          class="v2-card"
          style="outline:none;padding:15px 16px;margin-bottom:18px;border-color:color-mix(in srgb, var(--v2-moss) 40%, var(--v2-line))"
        >
          <div style="font-weight:650;font-size:13px">
            {form.revealed.how === 'replaced' ? 'New key for' : 'Connected'}
            {form.revealed.name}
          </div>
          <p class="v2-sub" style="font-size:12px;margin:4px 0 10px">
            {KEY_NOTICE}
          </p>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <code
              class="v2-num"
              style="flex:1;min-width:0;background:var(--v2-bg-sunk);border:1px solid var(--v2-line);border-radius:var(--v2-radius);padding:9px 11px;font-size:12.5px;word-break:break-all"
            >
              {form.revealed.key}
            </code>
            <button class="v2-btn v2-btn-sm" onclick={() => copyKey(form.revealed.key)}>
              {#if copied}<Check size={13} />Copied{:else}<Copy size={13} />Copy{/if}
            </button>
          </div>
        </div>
      {/if}

      {#if form?.error}
        <div style="margin-bottom:16px">
          <NextAction label="That did not work" text={form.error} tone="rust" />
        </div>
      {/if}

      {#if connecting}
        <form
          method="POST"
          action="?/connect"
          use:enhance={run}
          class="v2-card"
          style="padding:14px 15px;margin-bottom:18px;display:flex;flex-direction:column;gap:14px"
        >
          <fieldset style="border:0;padding:0;margin:0">
            <legend class="v2-label" style="margin-bottom:6px">App</legend>
            <div style="display:flex;flex-wrap:wrap;gap:8px 16px">
              {#each data.catalogue as entry, i (entry.kind)}
                <label style="display:inline-flex;gap:6px;align-items:center;min-height:32px">
                  <input
                    type="radio"
                    name="kind"
                    value={entry.kind}
                    checked={(kind || data.catalogue[0]?.kind) === entry.kind}
                    onchange={() => (kind = entry.kind)}
                    required={i === 0}
                  />
                  {kindLabel(entry)}
                </label>
              {/each}
            </div>
          </fieldset>

          {#if needsName}
            <div>
              <label class="v2-label" for="app-name" style="display:block;margin-bottom:4px">
                Name
              </label>
              <input
                id="app-name"
                name="name"
                required
                maxlength={MAX_APP_NAME}
                class="v2-input"
                style="width:100%;max-width:360px"
                placeholder="What should this app be called?"
              />
            </div>
          {/if}

          <fieldset style="border:0;padding:0;margin:0">
            <legend class="v2-label" style="margin-bottom:6px">Access</legend>
            <div
              style="display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:4px 16px"
            >
              {#each data.scopes as scope (scope)}
                <label style="display:inline-flex;gap:6px;align-items:center;min-height:32px">
                  <input
                    type="checkbox"
                    name="scopes"
                    value={scope}
                    checked={selected.includes(scope)}
                    onchange={(e) => toggle(scope, e.currentTarget.checked)}
                  />
                  {scopeLabel(scope)}
                </label>
              {/each}
            </div>
          </fieldset>

          <div style="display:flex;gap:10px;flex-wrap:wrap">
            <button class="v2-btn v2-btn-primary" disabled={busy}>Connect</button>
            <button
              type="button"
              class="v2-btn"
              disabled={busy}
              onclick={() => (connecting = false)}
            >
              Cancel
            </button>
          </div>
        </form>
      {/if}

      {#if !data.apps.length}
        {#if !connecting}
          <EmptyState
            title="No apps connected"
            body="Connect an app to let it read and write this organization with a key of its own."
          >
            {#snippet actions()}
              <button class="v2-btn v2-btn-primary" onclick={() => (connecting = true)}>
                <Plus />Connect an app
              </button>
            {/snippet}
          </EmptyState>
        {/if}
      {:else}
        <div style="display:flex;flex-direction:column;gap:12px">
          {#each data.apps as app (app.id)}
            <section class="v2-card" style="padding:14px 15px">
              <div style="display:flex;gap:10px;justify-content:space-between;flex-wrap:wrap">
                <div style="min-width:0">
                  <div style="font-weight:650;font-size:14px;word-break:break-word">{app.name}</div>
                  <div class="v2-sub" style="font-size:12px">
                    Connected {shortDate(app.connected_at)}{app.connected_by
                      ? ` by ${app.connected_by.name}`
                      : ''}
                  </div>
                </div>
                {#if confirming?.id !== app.id}
                  <div style="display:flex;gap:8px;flex-wrap:wrap">
                    <button
                      class="v2-btn v2-btn-sm"
                      disabled={busy}
                      onclick={() => (confirming = { id: app.id, action: 'replace' })}
                    >
                      Replace key
                    </button>
                    <button
                      class="v2-btn v2-btn-sm"
                      disabled={busy}
                      onclick={() => (confirming = { id: app.id, action: 'disconnect' })}
                    >
                      Disconnect
                    </button>
                  </div>
                {/if}
              </div>

              <dl
                style="display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;font-size:12.5px;margin:10px 0 0"
              >
                <dt class="v2-muted">Last used</dt>
                <dd style="margin:0">
                  {lastUsedLabel(app.last_used_at, relativeDays)}
                </dd>
                <dt class="v2-muted">Subscriptions</dt>
                <dd style="margin:0">{app.subscriptions}</dd>
                <dt class="v2-muted">Last failed delivery</dt>
                <dd style="margin:0;word-break:break-word">
                  {#if app.last_failed_delivery}
                    {shortDate(app.last_failed_delivery.at)}, {app.last_failed_delivery.event}:
                    {app.last_failed_delivery.error}
                  {:else}
                    None
                  {/if}
                </dd>
                <dt class="v2-muted">Access</dt>
                <dd style="margin:0">
                  {#if app.scopes.length}
                    <ul
                      style="margin:0;padding:0;list-style:none;display:flex;flex-wrap:wrap;gap:2px 12px"
                    >
                      {#each app.scopes as scope (scope)}
                        <li>{scopeLabel(scope)}</li>
                      {/each}
                    </ul>
                  {:else}
                    None
                  {/if}
                </dd>
              </dl>

              {#if confirming?.id === app.id}
                <form
                  method="POST"
                  action={confirming.action === 'replace' ? '?/replace' : '?/disconnect'}
                  use:enhance={run}
                  style="margin-top:12px;padding-top:12px;border-top:1px solid var(--v2-line)"
                >
                  <input type="hidden" name="id" value={app.id} />
                  <p style="font-size:12.5px;margin:0 0 10px">
                    {confirming.action === 'replace'
                      ? replaceSentence(app.name)
                      : disconnectSentence(app.name)}
                  </p>
                  <div style="display:flex;gap:10px;flex-wrap:wrap">
                    <button class="v2-btn v2-btn-primary" disabled={busy}>
                      {confirming.action === 'replace' ? 'Replace key' : 'Disconnect'}
                    </button>
                    <button
                      type="button"
                      class="v2-btn"
                      disabled={busy}
                      onclick={() => (confirming = null)}
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              {/if}
            </section>
          {/each}
        </div>
      {/if}
    </div>
  </div>
{/if}
