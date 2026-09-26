<script>
  /**
   * Outbound webhooks: where the org's events are sent.
   *
   * Admin-only to read as well as to change. A hook URL is often a credential
   * of its own (Zapier and Slack put theirs in the path), and every endpoint
   * receives copies of records, so the API answers a member 403 and this page
   * says so instead of drawing an empty table.
   *
   * The signing secret appears once, straight after a create, and never on
   * this list: the API only returns `secret_hint` here.
   */
  import { resolve } from '$app/paths';
  import { asInternalPath } from '$lib/utils/paths.js';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import SettingsCrumb from '$lib/v2/components/SettingsCrumb.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import NextAction from '$lib/v2/components/NextAction.svelte';
  import EmptyState from '$lib/v2/components/EmptyState.svelte';
  import { count, shortDate } from '$lib/v2/format.js';
  import { enhance } from '$app/forms';
  import { Plus, ShieldAlert } from '@lucide/svelte';
  import WebhookFields from './WebhookFields.svelte';
  import SecretOnce from './SecretOnce.svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  let creating = $state(false);
  let busy = $state(false);

  let atLimit = $derived(!data.forbidden && data.endpoints.length >= data.limit);

  const createSubmit = () => {
    busy = true;
    return async (/** @type {any} */ { update, result }) => {
      await update({ reset: false });
      busy = false;
      if (result?.type === 'success' && result?.data?.created) creating = false;
    };
  };

  /** @param {any} e */
  function endpointState(e) {
    if (e.is_active) return { tone: /** @type {const} */ ('moss'), label: 'Sending' };
    if (e.disabled_reason) return { tone: /** @type {const} */ ('clay'), label: 'Turned off' };
    return { tone: /** @type {const} */ ('slate'), label: 'Off' };
  }
</script>

<PageHeader title="Webhooks">
  {#snippet crumb()}<SettingsCrumb />{/snippet}
  {#snippet sub()}
    {#if !data.forbidden}
      <span class="v2-num"
        >{count(data.endpoints.filter((/** @type {any} */ e) => e.is_active).length)}</span
      >
      sending of <span class="v2-num">{count(data.endpoints.length)}</span>
    {/if}
  {/snippet}
  {#snippet actions()}
    {#if !data.forbidden && !atLimit}
      <button class="v2-btn v2-btn-primary" onclick={() => (creating = !creating)}>
        <Plus />New webhook
      </button>
    {/if}
  {/snippet}
</PageHeader>

{#if data.forbidden}
  <div class="v2-pad" style="padding-top:40px">
    <NextAction
      label="Admins only"
      text="Webhooks send copies of this organization's records to other services, and a hook URL is often a secret of its own, so only admins can see or change them."
    />
  </div>
{:else}
  <div class="v2-scroll">
    <div class="v2-pad" style="padding-top:16px;padding-bottom:32px">
      {#if form?.created?.secret}
        <SecretOnce
          title="Webhook added, copy its signing secret now"
          secret={form.created.secret}
        />
      {/if}

      {#if form?.create?.error && !creating}
        <div style="margin-bottom:16px">
          <NextAction label="That did not work" text={form.create.error} tone="rust" />
        </div>
      {/if}

      {#if creating}
        <form
          method="POST"
          action="?/create"
          use:enhance={createSubmit}
          class="v2-card"
          style="padding:14px 15px;margin-bottom:18px"
        >
          <WebhookFields
            catalogue={data.catalogue}
            values={form?.create?.values ?? {}}
            idPrefix="new"
          />
          {#if form?.create?.error}
            <p class="v2-sub" style="color:var(--v2-rust);font-size:12px;margin:10px 0 0">
              {form.create.error}
            </p>
          {/if}
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">
            <button class="v2-btn v2-btn-primary wh-tap" disabled={busy}>Add webhook</button>
            <button
              type="button"
              class="v2-btn wh-tap"
              disabled={busy}
              onclick={() => (creating = false)}
            >
              Cancel
            </button>
          </div>
        </form>
      {/if}

      {#if atLimit}
        <p class="v2-sub" style="font-size:12px;margin:0 0 14px">
          An organization can have at most {data.limit} webhooks. Remove one to add another.
        </p>
      {/if}

      {#if !data.endpoints.length}
        <EmptyState
          title="No webhooks yet"
          body="A webhook posts to a URL whenever a lead, deal, ticket or other record changes. Point one at Zapier, n8n, Slack or your own code."
        >
          {#snippet actions()}
            <button class="v2-btn v2-btn-primary" onclick={() => (creating = true)}>
              <Plus />New webhook
            </button>
          {/snippet}
        </EmptyState>
      {:else}
        <div class="v2-table-wrap">
          <table class="v2-table">
            <thead>
              <tr>
                <th>Endpoint</th>
                <th>State</th>
                <th class="v2-r">Events</th>
                <th data-m="hide">Added</th>
              </tr>
            </thead>
            <tbody>
              {#each data.endpoints as e (e.id)}
                {@const s = endpointState(e)}
                <tr>
                  <td data-m="title">
                    <a
                      class="v2-row-link"
                      href={resolve(asInternalPath(`/settings/webhooks/${e.id}`))}
                    >
                      <div class="v2-table-primary" style="word-break:break-all">{e.url}</div>
                      <div class="v2-table-secondary">
                        {e.description || (e.format === 'slack' ? 'Slack' : 'Signed JSON')}
                      </div>
                    </a>
                  </td>
                  <td data-m="tag"><Pill tone={s.tone}>{s.label}</Pill></td>
                  <td class="v2-r v2-num" data-m="meta" data-l="events">{count(e.events.length)}</td
                  >
                  <td data-m="hide" class="v2-muted">{shortDate(e.created_at)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}

      <div
        style="display:flex;gap:10px;align-items:flex-start;margin-top:20px;padding:14px 16px;border:1px solid var(--v2-line);border-radius:var(--v2-radius)"
      >
        <ShieldAlert size={16} style="color:var(--v2-clay);flex:none;margin-top:1px" />
        <div>
          <div style="font-weight:600;font-size:13px">
            Check the signature before trusting a delivery
          </div>
          <p class="v2-sub" style="font-size:12px;margin:4px 0 0">
            Every JSON delivery carries an <code>X-BottleCRM-Signature</code> header: an HMAC-SHA256 of
            the timestamp and the raw body, keyed with the webhook's secret. A failed delivery is retried
            five times over about eight and a half hours, and an endpoint that answers 410 Gone is turned
            off. The Webhooks page in the documentation has verification code and Zapier and n8n recipes.
          </p>
        </div>
      </div>
    </div>
  </div>
{/if}

<style>
  .wh-tap {
    min-height: 44px;
  }
</style>
