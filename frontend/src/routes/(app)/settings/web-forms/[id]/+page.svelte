<script>
  /**
   * One web form: the editor.
   *
   * FIVE SECTIONS, ONE SAVE
   * Fields, Behaviour, Spam, Embed and Activity all sit on this page, and the
   * first three are inside a single form posting to `?/save`. A per-section
   * save would mean three requests, three failure states, and an ordering
   * question nobody asked ("I changed the fields and the success message, why
   * did only one stick?").
   *
   * THE FIELD LIST TRAVELS AS JSON
   * Rows are added, removed and reordered in the browser, so index-derived
   * input names (`fields[3][label]`) would have to be renumbered across the
   * DOM on every move. Instead the array this component holds is serialised
   * into one hidden input at submit time. `withOrder` stamps `order` from list
   * position on the way out, and the server re-derives it from list position
   * anyway (`_write_fields` enumerates what it is given), so a client that
   * sent its own `order` could not reorder anything by lying about it.
   *
   * TWO WAYS TO REORDER, ONE IMPLEMENTATION
   * Above 768px each row has a drag handle. At or below it, the handle is
   * hidden and up/down buttons appear. Both paths call `moveField`, so they
   * cannot disagree about what a move means. Both controls are always in the
   * DOM and CSS alone decides which is usable: a JS breakpoint variable is a
   * second opinion about the viewport that can drift from the media query.
   *
   * PUBLISHING IS NOT A CHECKBOX HERE
   * It has its own endpoint, which validates the source state and the form's
   * shape. `is_published` is read-only on the update serializer, so a checkbox
   * bound to it would look like it worked and do nothing.
   */
  import { untrack } from 'svelte';
  import { enhance } from '$app/forms';
  import { resolve } from '$app/paths';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import StatCard from '$lib/v2/components/StatCard.svelte';
  import NextAction from '$lib/v2/components/NextAction.svelte';
  import ConfirmAction from '$lib/v2/components/ConfirmAction.svelte';
  import { count, relativeTime, shortDate } from '$lib/v2/format.js';
  import { LEAD_SOURCES, LEAD_SOURCE_LABEL } from '$lib/v2/enums.js';
  import {
    moveField,
    withOrder,
    isFieldComplete,
    hasRequiredField,
    leadFieldLabel,
    WEBFORM_LEAD_FIELDS
  } from '$lib/v2/webform-fields.js';
  import { ChevronUp, ChevronDown, GripVertical, Plus, Trash2, Copy, Check } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  let wf = $derived(data.form);
  let canManage = $derived(data.canManage);

  /**
   * The editable field list, seeded from the server ONCE and owned by the
   * browser from then on. `untrack` says that is deliberate: a `$derived`
   * would throw away every keystroke the moment anything invalidated `data`.
   *
   * Rows are keyed by a client-side `key` rather than by the row's `id`,
   * because a row added here has no id until it is saved, and `{#each}` needs
   * a stable key or Svelte re-uses the wrong DOM node on a reorder.
   */
  let fields = $state(untrack(() => seed(data.form.fields ?? [])));
  let nextKey = $state(1000);

  /** @param {any[]} rows */
  function seed(rows) {
    return rows.map((row, index) => ({
      key: index,
      source: row.source,
      lead_field: row.lead_field ?? '',
      custom_field: row.custom_field ?? null,
      label: row.label ?? '',
      placeholder: row.placeholder ?? '',
      is_required: Boolean(row.is_required)
    }));
  }

  let successMode = $state(untrack(() => data.form.success_mode));
  let captchaProvider = $state(untrack(() => data.form.captcha_provider ?? ''));
  let busy = $state(false);
  let copied = $state('');

  /**
   * Re-seed when the page starts describing a different form.
   *
   * SvelteKit re-uses this component across a `[id]` change rather than
   * remounting it, and the three `untrack`ed values above would then still
   * hold the previous form's fields. Guarded on the id so an ordinary
   * invalidation (a save, a publish) leaves the editor's own copy alone, which
   * is the whole reason it is untracked.
   */
  let loadedId = $state(untrack(() => data.form.id));
  $effect(() => {
    if (data.form.id === loadedId) return;
    loadedId = data.form.id;
    fields = seed(data.form.fields ?? []);
    successMode = data.form.success_mode;
    captchaProvider = data.form.captcha_provider ?? '';
  });

  let complete = $derived(fields.every(isFieldComplete));

  /**
   * What stops this form being published, said before the round trip.
   *
   * The server runs the same checks and is what actually decides; this only
   * saves someone a 400 that says the same thing. The wording is kept close to
   * the API's own so the two never read like different rules.
   *
   * The last check reads the SAVED success mode rather than the select's
   * current value, on purpose: publish acts on the stored form, so a redirect
   * URL typed but not yet saved would not be there when the server looked.
   */
  let publishBlocker = $derived.by(() => {
    if (!fields.length) return 'Add at least one field first.';
    if (!hasRequiredField(fields)) {
      return 'Add an email field before publishing. It is what lets a repeat submission update the existing lead instead of failing.';
    }
    if (!complete) return 'Every field needs a label and something to write into.';
    if (wf.success_mode === 'redirect' && !wf.redirect_url) {
      return 'This form redirects on success but has no redirect URL set.';
    }
    return null;
  });

  const working = () => {
    busy = true;
    return async (/** @type {any} */ { update }) => {
      await update();
      busy = false;
    };
  };

  /** Save also reseeds the editor from whatever came back, so a row the
   *  server rejected or normalised does not linger in the browser's copy. */
  const saveSubmit = () => {
    busy = true;
    return async (/** @type {any} */ { update, result }) => {
      await update({ reset: false });
      busy = false;
      if (result?.type === 'success') fields = seed(data.form.fields ?? []);
    };
  };

  function addField() {
    fields = [
      ...fields,
      {
        key: nextKey++,
        source: 'lead',
        lead_field: '',
        custom_field: null,
        label: '',
        placeholder: '',
        is_required: false
      }
    ];
  }

  /** @param {number} index */
  function removeField(index) {
    fields = fields.filter((_, i) => i !== index);
  }

  /**
   * When a row's target changes and the label is still the one the previous
   * target suggested (or empty), follow it. A label the person actually typed
   * is never overwritten: guessing is a convenience, not a correction.
   *
   * @param {number} index
   * @param {string} value
   */
  function pickLeadField(index, value) {
    const row = fields[index];
    const wasSuggested = !row.label.trim() || row.label === leadFieldLabel(row.lead_field);
    row.lead_field = value;
    row.custom_field = null;
    if (wasSuggested) row.label = leadFieldLabel(value);
  }

  /**
   * @param {number} index
   * @param {string} value
   */
  function pickCustomField(index, value) {
    const row = fields[index];
    const previous = data.customFields.find((/** @type {any} */ c) => c.id === row.custom_field);
    const wasSuggested = !row.label.trim() || row.label === previous?.label;
    row.custom_field = value || null;
    row.lead_field = '';
    const picked = data.customFields.find((/** @type {any} */ c) => c.id === value);
    if (wasSuggested && picked) row.label = picked.label;
  }

  // ---- drag reorder, pointer only -------------------------------------
  //
  // `dragging` holds the index being carried. It is set on dragstart and
  // cleared on dragend, so an interrupted drag (Escape, drop outside the list)
  // leaves no stuck state.
  let dragging = $state(/** @type {number | null} */ (null));

  /** @param {number} index */
  function onDrop(index) {
    if (dragging === null || dragging === index) return;
    fields = moveField(fields, dragging, index - dragging);
    dragging = null;
  }

  /** @param {string} text @param {string} which */
  async function copy(text, which) {
    try {
      await navigator.clipboard.writeText(text);
      copied = which;
      setTimeout(() => (copied = ''), 1600);
    } catch {
      // Clipboard blocked (no https, or no permission). The snippet is on
      // screen to select by hand; nothing else to do and nothing worth an
      // alarm.
    }
  }

  let actionError = $derived(
    form?.save?.error ??
      form?.publish?.error ??
      form?.unpublish?.error ??
      form?.delete?.error ??
      null
  );

  let submissions = $derived(data.submissions ?? []);
  let totals = $derived(data.analytics?.totals ?? null);

  /** @param {string} status */
  const statusTone = (status) =>
    status === 'accepted' || status === 'accepted_duplicate' ? 'moss' : 'slate';

  /** @param {string} status */
  const statusLabel = (status) =>
    ({
      accepted: 'Lead created',
      accepted_duplicate: 'Merged into an existing lead',
      rejected_spam: 'Rejected as spam',
      rejected_invalid: 'Rejected, invalid',
      rejected_captcha: 'Rejected, captcha'
    })[status] ?? status;
</script>

<PageHeader title={wf.name} record>
  {#snippet crumb()}
    <a href={resolve('/settings/web-forms')}>Web forms</a>
  {/snippet}
  {#snippet sub()}
    <Pill tone={wf.is_published ? 'moss' : 'slate'}>
      {wf.is_published ? 'Published' : 'Draft'}
    </Pill>
    <span style="margin-left:8px">
      {wf.is_published
        ? 'Accepting submissions from anyone with the embed.'
        : 'Collecting nothing until it is published.'}
    </span>
  {/snippet}
  {#snippet actions()}
    {#if canManage}
      {#if wf.is_published}
        <ConfirmAction
          action="?/unpublish"
          label="Unpublish"
          confirmLabel="Unpublish it"
          explain="The embed stays on the site and starts refusing people."
        />
      {:else}
        <form method="POST" action="?/publish" use:enhance={working}>
          <button class="v2-btn v2-btn-primary" disabled={busy || Boolean(publishBlocker)}>
            Publish
          </button>
        </form>
      {/if}
    {/if}
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  <div class="v2-pad wf-body">
    {#if actionError}
      <div style="margin-bottom:18px">
        <NextAction label="That did not work" text={actionError} tone="rust" />
      </div>
    {:else if form?.saved}
      <p class="v2-sub wf-ok">Saved.</p>
    {/if}

    {#if !wf.is_published && publishBlocker && canManage}
      <div style="margin-bottom:18px">
        <NextAction label="Before you can publish" text={publishBlocker} />
      </div>
    {/if}

    <form method="POST" action="?/save" use:enhance={saveSubmit}>
      <!-- The whole ordered list, in one value. `withOrder` stamps `order`
           from list position; the server does the same from the array's own
           order, so this is a convenience and not the authority. -->
      <input type="hidden" name="fields" value={JSON.stringify(withOrder(fields))} />

      <!-- ============ Fields ============ -->
      <section class="wf-section">
        <div class="wf-section-head">
          <h2 class="v2-section">Fields</h2>
          <p class="v2-sub wf-section-sub">
            What a visitor is asked, in the order they are asked it. An email field is required
            before the form can be published.
          </p>
        </div>

        {#if !fields.length}
          <p class="v2-sub wf-empty">No fields yet. A form with no fields collects nothing.</p>
        {/if}

        <ul class="wf-fields">
          {#each fields as field, i (field.key)}
            <li
              class="wf-row"
              class:is-dragging={dragging === i}
              draggable={canManage}
              ondragstart={() => (dragging = i)}
              ondragend={() => (dragging = null)}
              ondragover={(e) => e.preventDefault()}
              ondrop={(e) => {
                e.preventDefault();
                onDrop(i);
              }}
            >
              <!-- Pointer reorder. Hidden below 768px, where a drag handle
                   competes with the scroll gesture and loses. -->
              <span class="wf-drag" aria-hidden="true"><GripVertical size={15} /></span>

              <div class="wf-row-body">
                <div class="wf-row-line">
                  <label class="wf-sr" for="src-{field.key}">Field type</label>
                  <select
                    id="src-{field.key}"
                    class="v2-input wf-narrow"
                    disabled={!canManage}
                    value={field.source}
                    onchange={(e) => {
                      field.source = e.currentTarget.value;
                      field.lead_field = '';
                      field.custom_field = null;
                    }}
                  >
                    <option value="lead">Lead field</option>
                    <option value="custom" disabled={!data.customFields.length}>
                      Custom field{data.customFields.length ? '' : ' (none defined)'}
                    </option>
                  </select>

                  {#if field.source === 'custom'}
                    <label class="wf-sr" for="tgt-{field.key}">Custom field</label>
                    <select
                      id="tgt-{field.key}"
                      class="v2-input wf-narrow"
                      disabled={!canManage}
                      value={field.custom_field ?? ''}
                      onchange={(e) => pickCustomField(i, e.currentTarget.value)}
                    >
                      <option value="">Choose one…</option>
                      {#each data.customFields as c (c.id)}
                        <option value={c.id}>{c.label}</option>
                      {/each}
                    </select>
                  {:else}
                    <label class="wf-sr" for="tgt-{field.key}">Lead field</label>
                    <select
                      id="tgt-{field.key}"
                      class="v2-input wf-narrow"
                      disabled={!canManage}
                      value={field.lead_field}
                      onchange={(e) => pickLeadField(i, e.currentTarget.value)}
                    >
                      <option value="">Choose one…</option>
                      {#each WEBFORM_LEAD_FIELDS as f (f.value)}
                        <option value={f.value}>{f.label}</option>
                      {/each}
                    </select>
                  {/if}
                </div>

                <div class="wf-row-line">
                  <label class="wf-sr" for="lbl-{field.key}">Label</label>
                  <input
                    id="lbl-{field.key}"
                    class="v2-input"
                    disabled={!canManage}
                    maxlength="255"
                    placeholder="Label the visitor sees"
                    bind:value={field.label}
                  />
                  <label class="wf-sr" for="ph-{field.key}">Placeholder</label>
                  <input
                    id="ph-{field.key}"
                    class="v2-input"
                    disabled={!canManage}
                    maxlength="255"
                    placeholder="Placeholder (optional)"
                    bind:value={field.placeholder}
                  />
                </div>

                <label class="wf-check">
                  <input type="checkbox" disabled={!canManage} bind:checked={field.is_required} />
                  Required
                </label>
              </div>

              {#if canManage}
                <div class="wf-row-actions">
                  <!-- Touch reorder. Shown below 768px, where the drag handle
                       is hidden. Both call `moveField`, one implementation. -->
                  <div class="wf-move">
                    <button
                      type="button"
                      class="wf-move-btn"
                      disabled={i === 0}
                      aria-label="Move {field.label || 'this field'} up"
                      onclick={() => (fields = moveField(fields, i, -1))}
                    >
                      <ChevronUp size={16} />
                    </button>
                    <button
                      type="button"
                      class="wf-move-btn"
                      disabled={i === fields.length - 1}
                      aria-label="Move {field.label || 'this field'} down"
                      onclick={() => (fields = moveField(fields, i, 1))}
                    >
                      <ChevronDown size={16} />
                    </button>
                  </div>
                  <button
                    type="button"
                    class="wf-move-btn"
                    aria-label="Remove {field.label || 'this field'}"
                    onclick={() => removeField(i)}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              {/if}
            </li>
          {/each}
        </ul>

        {#if canManage}
          <button type="button" class="v2-btn v2-btn-sm wf-add" onclick={addField}>
            <Plus size={13} />Add a field
          </button>
        {/if}
      </section>

      <!-- ============ Behaviour ============ -->
      <section class="wf-section">
        <div class="wf-section-head">
          <h2 class="v2-section">Behaviour</h2>
          <p class="v2-sub wf-section-sub">
            What the visitor sees after they submit, and where the lead lands.
          </p>
        </div>

        <div class="wf-grid">
          <div class="v2-field">
            <label for="name">Name</label>
            <input
              id="name"
              name="name"
              class="v2-input"
              required
              maxlength="255"
              disabled={!canManage}
              value={wf.name}
            />
            <p class="v2-hint">Internal only. The visitor never sees it.</p>
          </div>

          <div class="v2-field">
            <label for="submit_button_label">Submit button</label>
            <input
              id="submit_button_label"
              name="submit_button_label"
              class="v2-input"
              maxlength="64"
              disabled={!canManage}
              value={wf.submit_button_label}
            />
          </div>

          <div class="v2-field">
            <label for="success_mode">After a successful submission</label>
            <select
              id="success_mode"
              name="success_mode"
              class="v2-input"
              disabled={!canManage}
              bind:value={successMode}
            >
              <option value="message">Show a message</option>
              <option value="redirect">Redirect to a URL</option>
            </select>
          </div>

          {#if successMode === 'redirect'}
            <div class="v2-field">
              <label for="redirect_url">Redirect URL</label>
              <input
                id="redirect_url"
                name="redirect_url"
                class="v2-input"
                type="url"
                maxlength="500"
                disabled={!canManage}
                value={wf.redirect_url}
                placeholder="https://example.com/thanks"
              />
              <p class="v2-hint">
                http or https only. The embed navigates the visitor's browser here, so any other
                scheme would be a script running on your own site.
              </p>
            </div>
          {:else}
            <div class="v2-field wf-wide">
              <label for="success_message">Success message</label>
              <textarea
                id="success_message"
                name="success_message"
                class="v2-input"
                rows="2"
                disabled={!canManage}>{wf.success_message}</textarea
              >
            </div>
          {/if}

          <div class="v2-field">
            <label for="assign_to">Assign new leads to</label>
            <select
              id="assign_to"
              name="assign_to"
              class="v2-input"
              disabled={!canManage}
              value={wf.assign_to ?? ''}
            >
              <option value="">Nobody</option>
              {#each data.profiles as p (p.id)}
                <option value={p.id}>{p.name}</option>
              {/each}
            </select>
          </div>

          <div class="v2-field">
            <label for="lead_source">Record the source as</label>
            <select
              id="lead_source"
              name="lead_source"
              class="v2-input"
              disabled={!canManage}
              value={wf.lead_source}
            >
              {#each LEAD_SOURCES as s (s)}
                <option value={s}>{LEAD_SOURCE_LABEL[s] ?? s}</option>
              {/each}
            </select>
            <p class="v2-hint">
              Which form a lead came from is recorded separately, so this can stay broad.
            </p>
          </div>

          <div class="v2-field">
            <label for="notify_profiles">Email these people on each lead</label>
            <select
              id="notify_profiles"
              name="notify_profiles"
              class="v2-input wf-multi"
              multiple
              size="4"
              disabled={!canManage}
            >
              {#each data.profiles as p (p.id)}
                <option value={p.id} selected={wf.notify_profiles?.includes(p.id)}>{p.name}</option>
              {/each}
            </select>
            <p class="v2-hint">Nobody selected means no notification is sent.</p>
          </div>

          <div class="v2-field">
            <label for="tags">Tag every lead with</label>
            <select
              id="tags"
              name="tags"
              class="v2-input wf-multi"
              multiple
              size="4"
              disabled={!canManage}
            >
              {#each data.tags as t (t.id)}
                <option value={t.id} selected={wf.tags?.includes(t.id)}>{t.name}</option>
              {/each}
            </select>
          </div>
        </div>
      </section>

      <!-- ============ Spam ============ -->
      <section class="wf-section">
        <div class="wf-section-head">
          <h2 class="v2-section">Spam</h2>
          <p class="v2-sub wf-section-sub">
            A hidden honeypot field, a per-address rate limit and a per-form one are always on and
            are not configurable. These are the parts you choose.
          </p>
        </div>

        <div class="wf-grid">
          <div class="v2-field wf-wide">
            <label for="allowed_origins">Allowed origins</label>
            <textarea
              id="allowed_origins"
              name="allowed_origins"
              class="v2-input"
              rows="3"
              disabled={!canManage}
              placeholder="https://example.com">{(wf.allowed_origins ?? []).join('\n')}</textarea
            >
            <p class="v2-hint">
              One per line, scheme and host only, no path. Leave empty and the iframe embed works
              anywhere. <strong>The script embed needs the site's origin listed here</strong>: the
              browser refuses a cross-origin POST that we have not permitted, and a form with no
              listed origins permits none.
            </p>
          </div>

          <div class="v2-field wf-wide">
            <label class="wf-check">
              <input
                type="checkbox"
                name="reject_disposable_email"
                disabled={!canManage}
                checked={wf.reject_disposable_email}
              />
              Reject throwaway email addresses
            </label>
          </div>

          <div class="v2-field">
            <label for="captcha_provider">Challenge</label>
            <select
              id="captcha_provider"
              name="captcha_provider"
              class="v2-input"
              disabled={!canManage}
              bind:value={captchaProvider}
            >
              <option value="">None</option>
              <option value="turnstile">Cloudflare Turnstile</option>
            </select>
          </div>

          {#if captchaProvider === 'turnstile'}
            <div class="v2-field">
              <label for="captcha_site_key">Turnstile site key</label>
              <input
                id="captcha_site_key"
                name="captcha_site_key"
                class="v2-input"
                maxlength="255"
                disabled={!canManage}
                value={wf.captcha_site_key}
              />
            </div>

            <div class="v2-field wf-wide">
              <label for="captcha_secret">Turnstile secret</label>
              <input
                id="captcha_secret"
                name="captcha_secret"
                class="v2-input"
                type="password"
                autocomplete="off"
                maxlength="255"
                disabled={!canManage}
                placeholder={wf.has_captcha_secret
                  ? 'Stored. Leave blank to keep it.'
                  : 'Paste the secret from Cloudflare'}
              />
              <p class="v2-hint">
                Never shown again once saved; we only send it to Cloudflare. Leaving this blank
                keeps whatever is stored rather than clearing it.
                {#if !wf.has_captcha_secret}
                  <strong>
                    No secret is stored yet. Verification fails closed, so publishing with Turnstile
                    on and no secret would refuse every submission.
                  </strong>
                {/if}
              </p>
            </div>
          {/if}
        </div>
      </section>

      {#if canManage}
        <div class="wf-save">
          <button class="v2-btn v2-btn-primary" disabled={busy}>Save changes</button>
        </div>
      {/if}
    </form>

    <!-- ============ Embed ============ -->
    <section class="wf-section">
      <div class="wf-section-head">
        <h2 class="v2-section">Embed</h2>
        <p class="v2-sub wf-section-sub">
          Paste one of these into your own site. Both are built by the server, because they need
          this API's address and a browser only knows your site's.
        </p>
      </div>

      <div class="wf-snippet">
        <div class="wf-snippet-head">
          <b>iframe</b>
          <span class="v2-sub">Works anywhere, no origin list needed.</span>
          <button
            type="button"
            class="v2-btn v2-btn-sm"
            onclick={() => copy(wf.embed_html, 'html')}
          >
            {#if copied === 'html'}<Check size={13} />Copied{:else}<Copy size={13} />Copy{/if}
          </button>
        </div>
        <pre>{wf.embed_html}</pre>
      </div>

      <div class="wf-snippet">
        <div class="wf-snippet-head">
          <b>script</b>
          <span class="v2-sub">Inherits your site's styling.</span>
          <button type="button" class="v2-btn v2-btn-sm" onclick={() => copy(wf.embed_js, 'js')}>
            {#if copied === 'js'}<Check size={13} />Copied{:else}<Copy size={13} />Copy{/if}
          </button>
        </div>
        <pre>{wf.embed_js}</pre>
        {#if !(wf.allowed_origins ?? []).length}
          <p class="v2-hint wf-warn">
            This one will not work yet. Add the site's origin under Spam first: the browser blocks a
            cross-origin POST unless we permit that origin, and this form permits none.
          </p>
        {/if}
      </div>
    </section>

    <!-- ============ Activity ============ -->
    <section class="wf-section">
      <div class="wf-section-head">
        <h2 class="v2-section">Activity</h2>
        <p class="v2-sub wf-section-sub">
          The last 30 days. A view is counted when the embed loads, whether or not anyone fills it
          in.
        </p>
      </div>

      {#if totals}
        <div class="v2-stats" style="margin-bottom:16px">
          <StatCard label="Views" value={count(totals.views)} tone="slate" />
          <StatCard label="Leads" value={count(totals.submissions)} tone="ink" />
          <StatCard
            label="Conversion"
            value={totals.views ? `${Math.round(totals.conversion_rate * 100)}%` : '-'}
            tone="slate"
            detail={totals.views ? null : 'No views yet'}
          />
          <StatCard
            label="Spam blocked"
            value={count(totals.spam)}
            tone="slate"
            detail={totals.spam ? 'Never reached a lead' : 'None'}
          />
        </div>
      {/if}

      {#if !submissions.length}
        <p class="v2-sub wf-empty">
          Nothing submitted yet. Rejected attempts would be listed here too, so an empty list means
          nobody has reached the form at all.
        </p>
      {:else}
        <div class="v2-table-wrap">
          <table class="v2-table">
            <thead>
              <tr>
                <th>Submitted</th>
                <th>Outcome</th>
                <th data-m="hide">Lead</th>
                <th data-m="hide">From</th>
              </tr>
            </thead>
            <tbody>
              {#each submissions as s (s.id)}
                <tr>
                  <td>
                    <div class="v2-table-primary">{relativeTime(s.created_at)}</div>
                    <div class="v2-table-secondary">{shortDate(s.created_at)}</div>
                  </td>
                  <td data-m="tag"
                    ><Pill tone={statusTone(s.status)}>{statusLabel(s.status)}</Pill></td
                  >
                  <td data-m="meta">
                    {#if s.lead}
                      <a href={resolve(`/leads/${s.lead}`)}>{s.lead_name}</a>
                    {:else}
                      <span class="v2-muted">No lead</span>
                    {/if}
                  </td>
                  <td data-m="hide" class="v2-muted">{s.referer || s.submitted_ip || '—'}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        {#if data.count > submissions.length}
          <p class="v2-sub" style="margin-top:10px;font-size:12px">
            Showing the {submissions.length} most recent of
            <span class="v2-num">{count(data.count)}</span>.
          </p>
        {/if}
      {/if}
    </section>

    {#if canManage}
      <section class="wf-section wf-danger">
        <div>
          <b>Delete this form</b>
          <p class="v2-sub" style="font-size:12px;margin:4px 0 0;max-width:60ch">
            Removes the form and its submission history. Leads it already created stay where they
            are. Any embed still on your site will stop working.
          </p>
        </div>
        <ConfirmAction
          action="?/delete"
          label="Delete"
          confirmLabel="Delete permanently"
          explain="This cannot be undone."
        />
      </section>
    {/if}
  </div>
</div>

<style>
  .wf-body {
    padding-top: 16px;
    padding-bottom: 40px;
    max-width: 900px;
  }

  .wf-section {
    margin-bottom: 30px;
  }

  .wf-section-head {
    margin-bottom: 12px;
  }

  .wf-section-sub {
    font-size: 12px;
    margin: 4px 0 0;
    max-width: 70ch;
  }

  .wf-ok {
    color: var(--v2-moss);
    font-weight: 550;
    font-size: 12.5px;
    margin: 0 0 16px;
  }

  .wf-empty {
    font-size: 12.5px;
    padding: 14px 15px;
    border: 1px dashed var(--v2-line);
    border-radius: var(--v2-radius);
    margin: 0;
  }

  /* ---- field editor ---- */

  .wf-fields {
    list-style: none;
    margin: 0;
    padding: 0;
  }

  /* Stacked, because that is what fits a phone. At 390px the four controls in
     a row share about 190px once the action column is subtracted, which turned
     "Company name" into "Comp" and every placeholder into "Placeholc". The
     side-by-side arrangement is added back at 768px, where there is room for
     it. Mobile-first: the wide layout is the enhancement. */
  .wf-row {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 12px 13px;
    border: 1px solid var(--v2-line);
    border-radius: var(--v2-radius);
    background: var(--v2-card);
    margin-bottom: 8px;
  }

  .wf-row.is-dragging {
    opacity: 0.5;
  }

  .wf-row-body {
    min-width: 0;
  }

  .wf-row-line {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-bottom: 8px;
  }

  .wf-row-line > .v2-input {
    min-width: 0;
  }

  .wf-multi {
    padding: 4px;
  }

  .wf-check {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    font-size: 12.5px;
    font-weight: 500;
  }

  /* Its own line on a phone, pushed right so the reorder and remove controls
     sit under the thumb rather than beside a truncated select. */
  .wf-row-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 4px;
  }

  .wf-move {
    display: flex;
  }

  /* An explicit minimum rather than padding: these are icon-only, and an empty
     button has nothing to pad around. */
  .wf-move-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 44px;
    min-height: 44px;
    border: 1px solid var(--v2-line);
    border-radius: 8px;
    background: var(--v2-card);
    color: var(--v2-slate);
    cursor: pointer;
  }

  .wf-move-btn:hover:not(:disabled) {
    color: var(--v2-ink);
  }

  .wf-move-btn:disabled {
    opacity: 0.35;
    cursor: not-allowed;
  }

  /* Hidden by default, shown from 768px up. Mobile-first: the phone gets the
     buttons, and the pointer-only affordance is what is added on the way up,
     rather than the phone getting a desktop control taken away. */
  .wf-drag {
    display: none;
    color: var(--v2-slate);
  }

  .wf-add {
    margin-top: 4px;
  }

  /* Labels for the selects and inputs in a field row. The row's own layout
     carries the meaning visually; screen readers still need the words. */
  .wf-sr {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }

  /* ---- settings grids ---- */

  .wf-grid {
    display: grid;
    grid-template-columns: 1fr;
    gap: 14px;
  }

  .wf-save {
    margin-bottom: 30px;
  }

  /* ---- embed snippets ---- */

  .wf-snippet {
    border: 1px solid var(--v2-line);
    border-radius: var(--v2-radius);
    margin-bottom: 10px;
    overflow: hidden;
  }

  .wf-snippet-head {
    display: flex;
    align-items: center;
    gap: 9px;
    flex-wrap: wrap;
    padding: 9px 12px;
    border-bottom: 1px solid var(--v2-line);
    font-size: 12.5px;
  }

  .wf-snippet-head .v2-sub {
    font-size: 11.5px;
  }

  .wf-snippet-head button {
    margin-left: auto;
  }

  /* Scrolls inside its own box. Without this a long absolute URL widens the
     page and every section beside it inherits a sideways swipe. */
  .wf-snippet pre {
    margin: 0;
    padding: 11px 12px;
    overflow-x: auto;
    font-size: 12px;
    background: var(--v2-bg-sunk);
  }

  .wf-warn {
    padding: 0 12px 11px;
    color: var(--v2-clay);
  }

  .wf-danger {
    display: flex;
    gap: 14px;
    align-items: flex-start;
    justify-content: space-between;
    flex-wrap: wrap;
    padding: 14px 15px;
    border: 1px solid color-mix(in srgb, var(--v2-rust) 28%, var(--v2-line));
    border-radius: var(--v2-radius);
  }

  @media (min-width: 768px) {
    /* The pointer affordance, added at the width where a pointer is likely.
       Below this the up/down buttons are the only reorder, because a drag
       handle on a touch screen competes with the scroll gesture and loses. */
    .wf-drag {
      display: inline-flex;
      align-items: center;
      min-height: 44px;
      cursor: grab;
    }

    .wf-move {
      display: none;
    }

    /* Side by side, now that there is room for the words to fit inside the
       controls. */
    .wf-row {
      flex-direction: row;
      gap: 10px;
      align-items: flex-start;
    }

    .wf-row-body {
      flex: 1;
    }

    .wf-row-line {
      flex-direction: row;
    }

    .wf-row-line > .v2-input {
      flex: 1;
    }

    .wf-narrow {
      flex: 0 1 auto;
    }

    .wf-row-actions {
      flex: none;
    }

    .wf-grid {
      grid-template-columns: 1fr 1fr;
    }

    .wf-wide {
      grid-column: 1 / -1;
    }
  }
</style>
