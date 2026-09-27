<script>
  /**
   * Canned replies.
   *
   * The body is the whole point of a macro, so it is on the page. V1 hides it
   * behind an edit dialog and lists titles, which means you cannot tell two
   * similarly-named macros apart without opening both.
   *
   * Placeholders are marked inline. `%customer_name%` is substituted by the
   * server; anything outside its seven supported tokens is left literal and
   * goes to the customer exactly as typed. That is the failure this page
   * exists to make visible: `%custmer_name%` has shipped twelve times.
   *
   * The body is org-authored text and is rendered as TEXT, split into
   * segments and placed in elements, never through {@html}. A canned reply is
   * a string a colleague wrote; putting it in the DOM as markup would make the
   * macro editor a stored-XSS form.
   *
   * PERMISSIONS, WHICH ARE NOT ROLE-GATED THE WAY THE REST OF SETTINGS IS
   * Every signed-in member may create, edit and delete their own `personal`
   * macros; only an admin may do any of that to an `org`-scope one.
   * `data.can_create_org` and `data.my_profile_id` are display hints decoded
   * server-side from the JWT (see `macros.js`'s `getMacros`), never the
   * authorization: the backend re-derives both and is what actually decides
   * whether a write succeeds. `canWrite` below mirrors that split so a row
   * only offers Edit/Delete when the click would not just come back as an
   * error.
   */
  import { enhance } from '$app/forms';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import SettingsCrumb from '$lib/v2/components/SettingsCrumb.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import StatCard from '$lib/v2/components/StatCard.svelte';
  import SettingsFormPanel from '$lib/v2/components/SettingsFormPanel.svelte';
  import ConfirmAction from '$lib/v2/components/ConfirmAction.svelte';
  import { count, relativeDays } from '$lib/v2/format.js';
  import { MACRO_SCOPE_LABEL, CASE_PRIORITIES } from '$lib/v2/enums.js';
  import { Plus, TriangleAlert } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  // `null` when the panel is closed, `'new'` when adding, or the macro
  // object when editing that row. One panel, two modes, so two rows can
  // never be open for edit at once.
  let editing = $state(/** @type {any} */ (null));

  // The scope currently selected in the form. Bound separately from `editing`
  // so the select works the same way in both modes: seeded from the row on
  // edit, defaulted to 'personal' on create, since every member can make a
  // personal macro but not every member can make an org one.
  let scope = $state('personal');

  function openCreate() {
    editing = 'new';
    scope = 'personal';
  }

  function openEdit(m) {
    editing = m;
    scope = m.scope;
  }

  /**
   * May the signed-in viewer write this row. An org macro is writable only
   * by an admin (`data.can_create_org`); a personal macro is writable only
   * by its owner, compared by Profile id (`m.owner.id`, from `getMacros`'s
   * reshape, against `data.my_profile_id`). This is a display decision, not
   * an authorization one: the backend enforces the same split independently
   * (403 on someone else's org macro, 404 on someone else's personal one)
   * and would refuse the write even if this returned true by mistake.
   */
  function canWrite(m) {
    return m.scope === 'org' ? data.can_create_org : m.owner?.id === data.my_profile_id;
  }

  let totals = $derived(data.totals);
  let byUse = $derived([...data.macros].sort((a, b) => b.usage_count - a.usage_count));
  let orgMacros = $derived(byUse.filter((m) => m.scope === 'org'));
  let personalMacros = $derived(byUse.filter((m) => m.scope === 'personal'));

  /**
   * Split a body into text and placeholder segments for rendering.
   *
   * Whether a token is real is decided by the server's `unknown_placeholders`,
   * not by matching against a list kept here. The supported set lives in
   * `macros/render.py` precisely so clients cannot drift from it. A copy in
   * this file would eventually mark a working macro as broken.
   */
  function segments(macro) {
    const re = /%[a-zA-Z_][a-zA-Z0-9_]*%/g;
    const out = [];
    let last = 0;
    let m;
    while ((m = re.exec(macro.body)) !== null) {
      if (m.index > last) out.push({ text: macro.body.slice(last, m.index), token: false });
      out.push({
        text: m[0],
        token: true,
        known: !macro.unknown_placeholders.includes(m[0])
      });
      last = m.index + m[0].length;
    }
    if (last < macro.body.length) out.push({ text: macro.body.slice(last), token: false });
    return out;
  }
</script>

<PageHeader title="Macros">
  {#snippet crumb()}<SettingsCrumb />{/snippet}
  {#snippet sub()}
    <span class="v2-num">{count(totals.org)}</span> shared ·
    <span class="v2-num">{count(totals.personal)}</span> yours
  {/snippet}
  {#snippet actions()}
    {#if !editing}
      <button class="v2-btn v2-btn-primary" onclick={openCreate}><Plus />New macro</button>
    {/if}
  {/snippet}
</PageHeader>

<div class="v2-pad" style="padding-top:16px;flex:none">
  <div class="v2-stats">
    <StatCard label="Shared with everyone" value={count(totals.org)} tone="ink" />
    <StatCard label="Only yours" value={count(totals.personal)} tone="slate" />
    <StatCard
      label="Broken placeholders"
      value={count(totals.with_unknown_placeholders)}
      tone={totals.with_unknown_placeholders > 0 ? 'rust' : 'slate'}
      detail="Sent to customers as typed"
    />
    <StatCard label="Turned off" value={count(totals.inactive)} tone="slate" />
  </div>
</div>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-bottom:32px">
    {#if editing}
      <SettingsFormPanel
        title={editing === 'new' ? 'New macro' : `Edit ${editing.title}`}
        action={editing === 'new' ? '?/create' : '?/update'}
        error={editing === 'new' ? form?.create?.error : form?.update?.error}
        submitLabel={editing === 'new' ? 'Add macro' : 'Save macro'}
        oncancel={() => (editing = null)}
        ondone={() => (editing = null)}
      >
        {#snippet fields()}
          {#if editing !== 'new'}
            <input type="hidden" name="id" value={editing.id} />
          {/if}

          <div class="v2-field">
            <label for="m-title">Title</label>
            <input
              id="m-title"
              class="v2-input"
              name="title"
              maxlength="255"
              required
              value={editing === 'new' ? '' : editing.title}
            />
          </div>

          <div class="v2-field">
            <label for="m-scope">Who sees it</label>
            <select id="m-scope" class="v2-input" name="scope" bind:value={scope}>
              <option value="personal">Just me</option>
              {#if data.can_create_org}
                <option value="org">Everyone in the org</option>
              {/if}
            </select>
            {#if !data.can_create_org}
              <p class="v2-hint">Only an admin can share a macro with everyone.</p>
            {/if}
          </div>

          <div class="v2-field v2-sfp-wide">
            <label for="m-body">Body</label>
            <textarea id="m-body" class="v2-input" name="body" rows="5"
              >{editing === 'new' ? '' : editing.body}</textarea
            >
            <p class="v2-hint">
              Placeholders like %customer_name% are substituted when the macro is sent. The seven
              supported tokens are listed to the right; anything else goes to the customer exactly
              as typed. Leave it empty for a macro that only changes the ticket.
            </p>
          </div>

          <!-- The actions, applied to the ticket right after the reply is
               sent. Blank or nothing selected means "leave it alone". -->
          <div class="v2-field">
            <label for="m-status">Set status</label>
            <select id="m-status" class="v2-input" name="set_status">
              <option value="">No change</option>
              {#each data.statuses as s (s)}
                <option value={s} selected={editing !== 'new' && editing.set_status === s}
                  >{s}</option
                >
              {/each}
            </select>
          </div>

          <div class="v2-field">
            <label for="m-priority">Set priority</label>
            <select id="m-priority" class="v2-input" name="set_priority">
              <option value="">No change</option>
              {#each CASE_PRIORITIES as p (p)}
                <option value={p} selected={editing !== 'new' && editing.set_priority === p}
                  >{p}</option
                >
              {/each}
            </select>
          </div>

          <div class="v2-field">
            <label for="m-assignees">Assign to</label>
            <select
              id="m-assignees"
              class="v2-input m-multi"
              name="set_assignees"
              multiple
              size="4"
            >
              {#each editing === 'new' ? data.people : editing.assignee_options as p (p.id)}
                <option
                  value={p.id}
                  selected={editing !== 'new' && editing.set_assignees.includes(p.id)}
                  >{p.name}</option
                >
              {/each}
            </select>
            <p class="v2-hint">Replaces whoever the ticket is assigned to.</p>
          </div>

          <div class="v2-field">
            <label for="m-tags">Add tags</label>
            <select id="m-tags" class="v2-input m-multi" name="add_tags" multiple size="4">
              {#each editing === 'new' ? data.tags : editing.tag_options as t (t.id)}
                <option value={t.id} selected={editing !== 'new' && editing.add_tags.includes(t.id)}
                  >{t.name}</option
                >
              {/each}
            </select>
            <p class="v2-hint">Added to the ticket's own tags.</p>
          </div>
        {/snippet}
      </SettingsFormPanel>
    {/if}

    {#if form?.delete?.error}
      <p class="v2-error" style="margin-bottom:12px">{form.delete.error}</p>
    {/if}
    {#if form?.activate?.error}
      <p class="v2-error" style="margin-bottom:12px">{form.activate.error}</p>
    {/if}

    <div class="v2-split-wide">
      <div>
        <div class="v2-label" style="margin-bottom:10px">Shared with everyone</div>
        <div style="display:flex;flex-direction:column;gap:9px;margin-bottom:24px">
          {#each orgMacros as m (m.id)}
            {@render macro(m)}
          {/each}
        </div>

        <div class="v2-label" style="margin-bottom:10px">Only yours</div>
        <div style="display:flex;flex-direction:column;gap:9px">
          {#each personalMacros as m (m.id)}
            {@render macro(m)}
          {/each}
        </div>
        <p class="v2-sub" style="font-size:11.5px;margin-top:11px">
          Personal macros are visible only to you. Nobody else in the organisation, admins included,
          sees this list.
        </p>
      </div>

      <div>
        <div class="v2-label" style="margin-bottom:10px">Placeholders that work</div>
        <div class="v2-card" style="overflow:hidden">
          {#each data.placeholders as p (p.token)}
            <div class="v2-setting" style="padding:10px 15px">
              <div class="v2-setting-body">
                <code class="v2-token">{p.token}</code>
              </div>
              <span class="v2-sub" style="font-size:11.5px;text-align:right">{p.resolves}</span>
            </div>
          {/each}
        </div>
        <p class="v2-sub" style="font-size:11.5px;margin-top:11px;line-height:1.5">
          These seven are the whole set. Anything else between percent signs is left exactly as
          written and goes out that way. The server does not guess, on purpose, so a typo is visible
          in the composer rather than a blank in the customer's inbox.
        </p>
      </div>
    </div>
  </div>
</div>

{#snippet macro(m)}
  <div class="v2-card" style="padding:14px 16px;opacity:{m.is_active ? 1 : 0.62}">
    <div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">
      <b style="font-size:13.5px">{m.title}</b>
      {#if !m.is_active}<Pill tone="slate">Off</Pill>{/if}
      {#if m.unknown_placeholders.length}<Pill tone="rust">Broken placeholder</Pill>{/if}
      <span class="v2-sub" style="font-size:11.5px;margin-left:auto">
        used <span class="v2-num">{count(m.usage_count)}</span> times · {relativeDays(m.updated_at)}
      </span>
    </div>

    <!--
      Text nodes and spans, never {@html}.

      Written on one line and held there by prettier-ignore. Svelte collapses
      the newline and indentation between two inline children into a single
      space, so a formatted version of this block renders "%customer_name% ,".
      A space the macro does not contain, in a preview whose whole job is to
      show exactly what the customer receives.
    -->
    <!-- prettier-ignore -->
    <p class="v2-macro-body">{#each segments(m) as seg, i (i)}{#if seg.token}<span class="v2-token" class:v2-token-bad={!seg.known}>{seg.text}</span>{:else}{seg.text}{/if}{/each}</p>

    {#if m.unknown_placeholders.length}
      <div class="v2-macro-flag">
        <TriangleAlert size={14} style="color:var(--v2-rust);flex:none" />
        <span>
          {m.unknown_placeholders.join(' and ')}
          {m.unknown_placeholders.length === 1 ? 'is not a placeholder' : 'are not placeholders'},
          {m.unknown_placeholders.length === 1 ? 'it goes' : 'they go'} to the customer exactly as written.
          {#if m.usage_count > 0}
            This macro has been sent
            <span class="v2-num">{count(m.usage_count)}</span> times.
          {/if}
        </span>
      </div>
    {/if}

    {#if m.chips.length}
      <div class="v2-macro-acts">
        <span class="v2-sub">On send:</span>
        {#each m.chips as chip (chip.key)}
          <Pill tone="slate">{chip.label}</Pill>
        {/each}
      </div>
    {/if}

    {#if m.owner}
      <div class="v2-sub" style="font-size:11px;margin-top:8px">
        {MACRO_SCOPE_LABEL[m.scope]} · {m.owner.name}
      </div>
    {/if}

    {#if canWrite(m)}
      <div style="display:flex;gap:6px;align-items:center;justify-content:flex-end;margin-top:10px">
        <button class="v2-btn v2-btn-sm" type="button" onclick={() => openEdit(m)}>Edit</button>
        {#if !m.is_active}
          <!-- Turning a macro back on restores nothing that was destroyed, so
               unlike "Turn off"/"Delete" this doesn't need the two-click
               confirm. A plain enhanced form posting just the id keeps the
               request to `{ is_active: true }`; see `activateMacro`'s
               comment for why that has to bypass `updateMacro` rather than
               reuse it. Offered for both scopes on purpose: an org row gets
               here by "Turn off" (soft), a personal row can only be inactive
               from data written before the edit form stopped carrying
               `is_active`, and either way `_get_writable` still enforces
               admin-only for org / owner-only for personal server-side, so
               this can never write a row `canWrite` above disagrees with. -->
          <form method="POST" action="?/activate" use:enhance>
            <input type="hidden" name="id" value={m.id} />
            <button class="v2-btn v2-btn-sm" type="submit">Turn on</button>
          </form>
        {:else if m.scope === 'org'}
          <!-- `MacroDetailView.delete` soft-deletes an org macro: it flips
               `is_active` and leaves the row (and its usage count) in place.
               "Turn off", not "Delete", says what actually happens. -->
          <ConfirmAction
            action="?/delete"
            label="Turn off"
            confirmLabel="Turn off"
            explain="Turns it off for everyone. It stops appearing in the picker."
            hidden={{ id: m.id }}
          />
        {:else}
          <!-- A personal macro is hard-deleted, not soft-deactivated: this
               button removes the row outright, so once it's gone there is
               nothing left to turn back on. -->
          <ConfirmAction
            action="?/delete"
            label="Delete"
            confirmLabel="Delete"
            explain="Deletes it permanently."
            hidden={{ id: m.id }}
          />
        {/if}
      </div>
    {/if}
  </div>
{/snippet}

<style>
  .v2-macro-body {
    font-size: 12.5px;
    color: var(--v2-slate);
    line-height: 1.55;
    white-space: pre-wrap;
    margin: 9px 0 0;
  }
  .v2-token {
    font-family: var(--v2-mono);
    font-size: 11.5px;
    background: var(--v2-hover);
    border-radius: 3px;
    padding: 1px 4px;
    color: var(--v2-ink);
  }
  /* No side padding inside a body preview. Four pixels either side renders
     "%customer_name% ,". A space the macro does not contain, on a screen
     whose only job is to show exactly what the customer will receive. The
     background alone is enough to mark it. */
  .v2-macro-body .v2-token {
    padding: 1px 0;
  }
  /* Rust, not ember: a broken placeholder is a fact about the macro, not a
     button. Ember stays reserved for things you act on. */
  .v2-token-bad {
    color: var(--v2-rust);
    text-decoration: underline wavy;
    text-underline-offset: 2px;
  }
  .v2-macro-acts {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    margin-top: 10px;
    font-size: 11.5px;
  }
  /* A pill never wraps; an assignee list has to, or it pushes the card wider
     than a phone. */
  .v2-macro-acts :global(.v2-pill) {
    white-space: normal;
    max-width: 100%;
    overflow-wrap: anywhere;
  }
  /* A select that shows several rows at once; the default single-row height
     would hide all but one person. */
  .m-multi {
    min-height: 96px;
  }
  .v2-macro-flag {
    display: flex;
    gap: 7px;
    align-items: flex-start;
    margin-top: 10px;
    font-size: 12px;
    color: var(--v2-slate);
    line-height: 1.45;
  }
</style>
