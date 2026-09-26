<script>
  /**
   * The public help center: one switch and one address.
   *
   * Turning it on publishes this org's approved, published articles to anyone
   * with the link, and to search engines. That is the whole consequence, so the
   * page says it next to the switch rather than leaving it to be discovered.
   *
   * Any member can see whether it is on and open the public page; only an
   * admin gets the form. The API enforces that regardless of what is shown.
   */
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import SettingsCrumb from '$lib/v2/components/SettingsCrumb.svelte';
  import SettingsFormPanel from '$lib/v2/components/SettingsFormPanel.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import { ExternalLink, Globe } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  let editing = $state(false);

  let s = $derived(data.settings);
  let live = $derived(s.help_center_enabled && !!s.public_url);

  // A refused save re-renders the form with what was typed, not what is stored.
  let draft = $derived(form?.update?.values ?? null);
</script>

<PageHeader title="Help center">
  {#snippet crumb()}<SettingsCrumb />{/snippet}
  {#snippet sub()}
    {live ? 'Public, and open to search engines' : 'Off. Nothing is published'}
  {/snippet}
  {#snippet actions()}
    {#if s.can_edit && !editing}
      <button class="v2-btn v2-btn-primary" onclick={() => (editing = true)}>Edit</button>
    {/if}
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:16px;padding-bottom:32px">
    {#if editing}
      <SettingsFormPanel
        title="Help center"
        action="?/update"
        error={form?.update?.error}
        submitLabel="Save"
        oncancel={() => (editing = false)}
        ondone={() => (editing = false)}
      >
        {#snippet fields()}
          <div class="v2-field v2-sfp-wide">
            <label for="hc-enabled">Public help center</label>
            <label class="hc-check">
              <input
                id="hc-enabled"
                type="checkbox"
                name="help_center_enabled"
                value="true"
                checked={draft ? draft.enabled : s.help_center_enabled}
              />
              Publish approved, published articles for anyone to read.
            </label>
          </div>

          <div class="v2-field v2-sfp-wide">
            <label for="hc-slug">Address</label>
            <div class="hc-slug">
              <span class="v2-sub">/help-center/</span>
              <input
                id="hc-slug"
                class="v2-input"
                name="help_center_slug"
                value={draft ? draft.slug : (s.help_center_slug ?? '')}
                maxlength="50"
                autocomplete="off"
                autocapitalize="none"
                spellcheck="false"
                placeholder="your-company"
              />
            </div>
            <p class="v2-hint">
              3 to 50 lowercase letters, numbers and single hyphens. Required before the help center
              can be switched on.
            </p>
          </div>
        {/snippet}
      </SettingsFormPanel>
    {/if}

    <div class="v2-label" style="margin-bottom:10px">Current setting</div>
    <div class="v2-card" style="overflow:hidden;margin-bottom:22px">
      <div class="v2-setting">
        <div class="v2-setting-body">
          <b>Public help center</b>
          <span class="v2-sub" style="font-size:11.5px">
            Off means every public page answers "not found".
          </span>
        </div>
        <Pill tone={s.help_center_enabled ? 'moss' : 'slate'}>
          {s.help_center_enabled ? 'On' : 'Off'}
        </Pill>
      </div>
      <div class="v2-setting">
        <div class="v2-setting-body">
          <b>Address</b>
          <span class="v2-sub hc-wrap" style="font-size:11.5px">
            {s.public_url ?? 'Not chosen yet'}
          </span>
        </div>
      </div>
      {#if live}
        <!-- eslint-disable svelte/no-navigation-without-resolve -- an absolute URL
             the API builds from FRONTEND_URL, opened in a new tab -->
        <a class="v2-setting hc-open" href={s.public_url} target="_blank" rel="noopener noreferrer">
          <div class="v2-setting-body">
            <b>Open the public page</b>
            <span class="v2-sub" style="font-size:11.5px">See it as a customer does.</span>
          </div>
          <ExternalLink size={15} style="color:var(--v2-slate);flex:none" />
        </a>
        <!-- eslint-enable svelte/no-navigation-without-resolve -->
      {/if}
    </div>

    <div class="v2-label" style="margin-bottom:10px">What this publishes</div>
    <div class="v2-card" style="padding:15px 16px">
      <div style="display:flex;gap:10px;align-items:flex-start">
        <Globe size={16} style="color:var(--v2-slate);flex:none;margin-top:2px" />
        <p class="v2-sub" style="font-size:12.5px;margin:0;line-height:1.5">
          Only articles that are both approved and published, the same ones customers see in the
          portal. Drafts, reviewed articles, tags, linked tickets and who wrote an article are never
          shown. Search engines can list these pages, and a sitemap is served at the address
          followed by <span class="v2-num">/sitemap.xml</span>.
        </p>
      </div>
    </div>
  </div>
</div>

<style>
  .hc-check {
    display: flex;
    gap: 8px;
    align-items: center;
    font-weight: 400;
    min-height: 44px;
  }
  .hc-slug {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
  }
  .hc-slug input {
    flex: 1;
    min-width: 0;
    min-height: 44px;
    /* 16px stops iOS Safari zooming the viewport on focus. */
    font-size: 16px;
  }
  .hc-wrap {
    overflow-wrap: anywhere;
  }
  .hc-open {
    text-decoration: none;
    color: inherit;
  }
</style>
