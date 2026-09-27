<script>
  /**
   * Your task calendar feed (G14).
   *
   * A private URL that Google Calendar, Outlook or Apple Calendar polls for
   * your open tasks with a due date, each shown as an all-day event carrying
   * the title, the priority and a link back here. Never notes, and never a
   * contact, account or other record name.
   *
   * THE URL IS SHOWN ONCE
   * The server stores only a hash of it, so there is nothing to show again on
   * a later visit. The reveal below is the action's response, and it is gone
   * on the next load. Lost it? Regenerate, which also retires the old one.
   */
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import NextAction from '$lib/v2/components/NextAction.svelte';
  import ConfirmAction from '$lib/v2/components/ConfirmAction.svelte';
  import { relativeDays, shortDate } from '$lib/v2/format.js';
  import { resolve } from '$app/paths';
  import { enhance } from '$app/forms';
  import { CalendarDays, Copy, Check, ShieldAlert } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  let feed = $derived(data.feed);
  let busy = $state(false);
  let copied = $state(false);

  /** @param {string} value */
  async function copyUrl(value) {
    try {
      await navigator.clipboard.writeText(value);
      copied = true;
      setTimeout(() => (copied = false), 1600);
    } catch {
      // Clipboard blocked (no https or no permission). The URL is on screen to
      // select by hand.
    }
  }
</script>

<PageHeader title="Calendar feed">
  {#snippet crumb()}<a href={resolve('/profile')}>Profile</a> ›{/snippet}
  {#snippet sub()}
    {#if feed.enabled}
      On, URL created {shortDate(feed.created_at)}
    {:else}
      Off
    {/if}
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  <div class="v2-pad feed" style="padding-top:18px;padding-bottom:32px;max-width:720px">
    {#if form?.url}
      <!-- The one and only time this URL is shown. -->
      <div
        class="v2-card"
        style="padding:15px 16px;margin-bottom:18px;border-color:color-mix(in srgb, var(--v2-moss) 40%, var(--v2-line))"
      >
        <div style="font-weight:650;font-size:13px">Your feed URL, copy it now</div>
        <p class="v2-sub" style="font-size:12px;margin:4px 0 10px">
          This is the only time it is shown. Paste it into your calendar app below. If you lose it,
          regenerate it.
        </p>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <code
            class="v2-num"
            style="flex:1;min-width:0;background:var(--v2-bg-sunk);border:1px solid var(--v2-line);border-radius:var(--v2-radius);padding:9px 11px;font-size:12.5px;word-break:break-all"
          >
            {form.url}
          </code>
          <button class="v2-btn" type="button" onclick={() => copyUrl(form.url)}>
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

    <div class="v2-card" style="overflow:hidden;margin-bottom:20px">
      <div class="v2-setting">
        <CalendarDays size={16} style="color:var(--v2-slate);flex:none" />
        <div class="v2-setting-body">
          <b>Your open tasks, in your calendar</b>
          <span class="v2-sub" style="font-size:11.5px">
            Tasks that are New or In progress and have a due date, from 90 days ago to a year ahead,
            as all-day events. Each shows the title, the priority and a link back here.
          </span>
        </div>
        {#if feed.enabled}
          <Pill tone="moss" dot>On</Pill>
        {:else}
          <Pill>Off</Pill>
        {/if}
      </div>
      {#if feed.enabled}
        <div class="v2-setting">
          <div class="v2-setting-body">
            <b>Last fetched</b>
            <span class="v2-sub" style="font-size:11.5px">
              {#if feed.last_used_at}
                A calendar app read the feed {relativeDays(feed.last_used_at)}.
              {:else}
                No calendar app has read it yet.
              {/if}
            </span>
          </div>
        </div>
        <div class="v2-setting feed-actions">
          <div class="v2-setting-body">
            <b>Regenerate or turn off</b>
            <span class="v2-sub" style="font-size:11.5px">
              Either one stops the current URL at once, and calendars subscribed to it stop
              updating.
            </span>
          </div>
          <ConfirmAction
            action="?/issue"
            label="Regenerate URL"
            confirmLabel="Regenerate"
            explain="The old URL stops working. Subscribe again with the new one."
          />
          <ConfirmAction
            action="?/disable"
            label="Turn off"
            confirmLabel="Turn off"
            explain="The URL stops working and subscribed calendars stop updating."
          />
        </div>
      {:else}
        <form
          method="POST"
          action="?/issue"
          class="v2-setting feed-actions"
          use:enhance={() => {
            busy = true;
            return async (/** @type {any} */ { update }) => {
              await update();
              busy = false;
            };
          }}
        >
          <div class="v2-setting-body">
            <b>Turn on</b>
            <span class="v2-sub" style="font-size:11.5px">
              Creates your private feed URL and shows it once.
            </span>
          </div>
          <button class="v2-btn v2-btn-primary" type="submit" disabled={busy}>
            Turn on calendar feed
          </button>
        </form>
      {/if}
    </div>

    <div class="v2-label" style="margin-bottom:10px">Subscribe to it</div>
    <div class="v2-card" style="overflow:hidden;margin-bottom:20px">
      <div class="v2-setting">
        <div class="v2-setting-body">
          <b>Google Calendar</b>
          <span class="v2-sub" style="font-size:11.5px">
            On a computer, open Google Calendar. Beside Other calendars choose +, then From URL,
            paste the feed URL and choose Add calendar. Google refreshes it every few hours, so
            changes take a while to appear.
          </span>
        </div>
      </div>
      <div class="v2-setting">
        <div class="v2-setting-body">
          <b>Outlook</b>
          <span class="v2-sub" style="font-size:11.5px">
            In Outlook on the web choose Add calendar, then Subscribe from web, paste the feed URL
            and choose Import.
          </span>
        </div>
      </div>
      <div class="v2-setting">
        <div class="v2-setting-body">
          <b>Apple Calendar</b>
          <span class="v2-sub" style="font-size:11.5px">
            Choose File, then New Calendar Subscription, and paste the feed URL.
          </span>
        </div>
      </div>
    </div>

    <div
      style="display:flex;gap:10px;align-items:flex-start;padding:14px 16px;border:1px solid var(--v2-line);border-radius:var(--v2-radius)"
    >
      <ShieldAlert size={16} style="color:var(--v2-clay);flex:none;margin-top:1px" />
      <div>
        <div style="font-weight:600;font-size:13px">The URL is the key</div>
        <p class="v2-sub" style="font-size:12px;margin:4px 0 0">
          Anyone who has it can see the titles and priorities of your open tasks, without signing
          in. Share it with nobody but your calendar app, and regenerate it if it leaks. It stops
          working on its own if you leave the organisation or your account is deactivated.
        </p>
      </div>
    </div>
  </div>
</div>

<style>
  .feed-actions {
    flex-wrap: wrap;
  }
  @media (max-width: 768px) {
    /* A thumb's tap target. The two-step buttons are small by default. */
    .feed :global(.v2-btn) {
      min-height: 44px;
    }
    /* The explanation takes the row and the buttons wrap under it, rather
       than squeezing the text into a column a few words wide. */
    .feed-actions .v2-setting-body {
      flex-basis: 100%;
    }
  }
</style>
