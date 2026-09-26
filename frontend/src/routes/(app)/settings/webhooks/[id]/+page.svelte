<script>
  /**
   * One webhook: test it, switch it, rotate its secret, edit it, and read what
   * happened to each delivery.
   *
   * Sending is asynchronous. "Send test" queues a `ping` and the row appears
   * here as Pending; a worker makes the attempt within seconds, and Refresh
   * shows the answer. A failed attempt is retried on a schedule
   * (1m, 5m, 30m, 2h, 6h) before it is marked Failed, and Redeliver on a
   * settled row sends the same event again as a new row.
   */
  import { resolve } from '$app/paths';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import SettingsCrumb from '$lib/v2/components/SettingsCrumb.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import NextAction from '$lib/v2/components/NextAction.svelte';
  import ConfirmAction from '$lib/v2/components/ConfirmAction.svelte';
  import { count, relativeTime } from '$lib/v2/format.js';
  import { enhance } from '$app/forms';
  import WebhookFields from '../WebhookFields.svelte';
  import SecretOnce from '../SecretOnce.svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  let busy = $state(false);

  const working = () => {
    busy = true;
    return async (/** @type {any} */ { update }) => {
      await update({ reset: false });
      busy = false;
    };
  };

  const DELIVERY_TONE = /** @type {const} */ ({
    succeeded: 'moss',
    pending: 'slate',
    failed: 'rust'
  });

  let actionError = $derived(
    form?.update?.error ??
      form?.toggle?.error ??
      form?.test?.error ??
      form?.rotate?.error ??
      form?.redeliver?.error ??
      form?.remove?.error ??
      null
  );
</script>

{#if data.forbidden}
  <PageHeader title="Webhook">
    {#snippet crumb()}<SettingsCrumb />{/snippet}
  </PageHeader>
  <div class="v2-pad" style="padding-top:40px">
    <NextAction
      label="Admins only"
      text="Webhooks send copies of this organization's records to other services, so only admins can see or change them."
    />
  </div>
{:else}
  {@const e = data.endpoint}
  <PageHeader title={e.description || 'Webhook'}>
    {#snippet crumb()}<SettingsCrumb />{/snippet}
    {#snippet sub()}
      <span style="word-break:break-all">{e.url}</span>
    {/snippet}
  </PageHeader>

  <div class="v2-scroll">
    <div class="v2-pad" style="padding-top:16px;padding-bottom:32px">
      {#if form?.rotated?.secret}
        <SecretOnce title="Secret rotated, copy the new one now" secret={form.rotated.secret} />
      {/if}
      {#if actionError}
        <div style="margin-bottom:16px">
          <NextAction label="That did not work" text={actionError} tone="rust" />
        </div>
      {/if}
      {#if form?.tested}
        <p class="v2-sub wh-note">Test queued. Refresh the log below in a few seconds.</p>
      {:else if form?.updated}
        <p class="v2-sub wh-note">Saved.</p>
      {:else if form?.redelivered}
        <p class="v2-sub wh-note">Queued to send again.</p>
      {/if}

      <div class="v2-card" style="padding:14px 15px;margin-bottom:18px">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <Pill tone={e.is_active ? 'moss' : e.disabled_reason ? 'clay' : 'slate'}>
            {e.is_active ? 'Sending' : 'Off'}
          </Pill>
          <span class="v2-sub" style="font-size:12px">
            {e.format === 'slack' ? 'Slack message' : 'Signed JSON'} · secret
            <span class="v2-num">{e.secret_hint}</span>
          </span>
        </div>
        {#if e.disabled_reason}
          <p class="v2-sub" style="font-size:12px;color:var(--v2-clay);margin:8px 0 0">
            {e.disabled_reason}
          </p>
        {/if}
        <div class="wh-actions">
          {#if e.is_active}
            <form method="POST" action="?/test" use:enhance={working}>
              <button class="v2-btn v2-btn-primary wh-tap" disabled={busy}>Send test</button>
            </form>
          {/if}
          <form method="POST" action="?/toggle" use:enhance={working}>
            <input type="hidden" name="is_active" value={e.is_active ? 'false' : 'true'} />
            <button class="v2-btn wh-tap" disabled={busy}>
              {e.is_active ? 'Turn off' : 'Turn on'}
            </button>
          </form>
          <ConfirmAction
            action="?/rotate"
            label="Rotate secret"
            confirmLabel="Rotate now"
            explain="The current secret stops verifying at once. Update your receiver straight after."
          />
          <ConfirmAction
            action="?/remove"
            label="Delete"
            confirmLabel="Delete permanently"
            explain="Removes the webhook and its delivery log."
          />
        </div>
      </div>

      <form
        method="POST"
        action="?/update"
        use:enhance={working}
        class="v2-card"
        style="padding:14px 15px;margin-bottom:22px"
      >
        <div class="v2-label" style="margin-bottom:10px">Settings</div>
        <WebhookFields catalogue={data.catalogue} values={e} idPrefix="edit" />
        <button class="v2-btn v2-btn-primary wh-tap" style="margin-top:14px" disabled={busy}>
          Save
        </button>
      </form>

      <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
        <div class="v2-label">Deliveries</div>
        <span class="v2-sub v2-num" style="font-size:12px">{count(data.deliveryCount)}</span>
        <a
          class="v2-btn v2-btn-sm wh-tap"
          style="margin-left:auto"
          href={resolve(`/settings/webhooks/${e.id}?offset=${data.offset}`)}
        >
          Refresh
        </a>
      </div>

      {#if !data.deliveries.length}
        <p class="v2-sub" style="font-size:12.5px">
          Nothing sent yet. Send a test, or change a record this webhook listens for.
        </p>
      {:else}
        <div class="v2-table-wrap">
          <table class="v2-table">
            <thead>
              <tr>
                <th>Event</th>
                <th>Status</th>
                <th class="v2-r" data-m="hide">Tries</th>
                <th data-m="hide">When</th>
                <th class="v2-r">Actions</th>
              </tr>
            </thead>
            <tbody>
              {#each data.deliveries as d (d.id)}
                <tr>
                  <td data-m="title">
                    <div class="v2-table-primary v2-num">{d.event}</div>
                    <div class="v2-table-secondary">
                      {#if d.response_status}HTTP {d.response_status} ·
                      {/if}{d.error || relativeTime(d.created_at)}
                    </div>
                  </td>
                  <td data-m="tag">
                    <Pill
                      tone={DELIVERY_TONE[
                        /** @type {'succeeded'|'pending'|'failed'} */ (d.status)
                      ] ?? 'slate'}
                    >
                      {d.status === 'succeeded'
                        ? 'Delivered'
                        : d.status === 'failed'
                          ? 'Failed'
                          : 'Pending'}
                    </Pill>
                  </td>
                  <td class="v2-r v2-num" data-m="hide">{d.attempts}</td>
                  <td data-m="hide" class="v2-muted">{relativeTime(d.created_at)}</td>
                  <td class="v2-r">
                    {#if d.status !== 'pending' && e.is_active}
                      <form method="POST" action="?/redeliver" use:enhance={working}>
                        <input type="hidden" name="delivery_id" value={d.id} />
                        <button class="v2-btn v2-btn-sm wh-tap" disabled={busy}>Redeliver</button>
                      </form>
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <div style="display:flex;gap:8px;margin-top:12px">
          {#if data.offset > 0}
            <a
              class="v2-btn v2-btn-sm wh-tap"
              href={resolve(
                `/settings/webhooks/${e.id}?offset=${Math.max(0, data.offset - data.pageSize)}`
              )}
            >
              Newer
            </a>
          {/if}
          {#if data.offset + data.pageSize < data.deliveryCount}
            <a
              class="v2-btn v2-btn-sm wh-tap"
              href={resolve(`/settings/webhooks/${e.id}?offset=${data.offset + data.pageSize}`)}
              >Older</a
            >
          {/if}
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .wh-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
    margin-top: 12px;
  }
  .wh-tap {
    min-height: 44px;
    display: inline-flex;
    align-items: center;
  }
  .wh-note {
    color: var(--v2-moss);
    font-size: 12.5px;
    margin: 0 0 14px;
    font-weight: 550;
  }
</style>
