<script>
  import { resolve } from '$app/paths';
  /**
   * A person, and everything that person is involved in.
   *
   * The rail holds the identity fields. Those genuinely are label/value
   * pairs. The body holds what is happening: the deals they are named on, the
   * tasks that name them, the tickets they are on, and, now writable, the log
   * of what has been said and shared. v1 put all of it in one flat form and
   * answered none of it.
   *
   * The mock version of this page leant on two fields that do not exist: a
   * `relationship` ("Champion", "Blocker"), and `last_activity_at`. The whole
   * headline argument was built on the second, how long since anyone spoke to
   * this person, and nothing in the CRM records that. What is left is what the
   * data can support, which is less dramatic and true.
   *
   * Built to the same shape as the lead detail page: an avatar anchors the
   * header, and the activity log takes notes and files because the daily act on
   * a contact is recording that you spoke to them. Nothing is faked. Every
   * event kind is backed by a row that exists.
   */
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import NextAction from '$lib/v2/components/NextAction.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import Avatar from '$lib/v2/components/Avatar.svelte';
  import { money, moneyEach, shortDate, relativeDays, daysSince } from '$lib/v2/format.js';
  import {
    CLOSED_KINDS,
    PRIORITY_TONE,
    CASE_STATUS_TONE,
    TASK_PRIORITY_TONE
  } from '$lib/v2/enums.js';
  import { enhance } from '$app/forms';
  import {
    ChevronRight,
    Mail,
    Phone,
    PhoneOff,
    Pencil,
    Paperclip,
    MessageSquare,
    Sparkles,
    X
  } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  let { contact, deals, tickets, tasks, colleagues, owners, activity } = $derived(data);

  // Count and value come from the server, over every open deal: the `deals`
  // list stops at 10. Per currency, never added across, since there are no
  // exchange rates. Nothing priced reads as zero in the org's currency.
  let openCount = $derived(data.openDeals.count);
  let openPipeline = $derived(moneyEach(data.openDeals.by_currency) || money(0, data.org.currency));
  // Named only when it is the single open deal and it is among the rows held.
  let onlyOpenDeal = $derived(
    openCount === 1
      ? deals.find((/** @type {any} */ d) => !CLOSED_KINDS.includes(d.stage_kind))
      : null
  );
  let overdueTasks = $derived(
    tasks.filter(
      (/** @type {any} */ t) =>
        t.status !== 'Completed' && t.due_date && (daysSince(t.due_date) ?? 0) > 0
    )
  );
  // Open = not in a terminal state. The Case statuses are New / Assigned /
  // Pending / Closed / Rejected / Duplicate (see CASE_STATUS_TONE); the last
  // three are done, so anything else is still live.
  let openTickets = $derived(
    tickets.filter(
      (/** @type {any} */ t) => !['Closed', 'Rejected', 'Duplicate'].includes(t.status)
    )
  );

  // ── note composer ────────────────────────────────────────────────────────
  let note = $state('');
  let saving = $state(false);

  // The picked file's name, mirrored out of the input so the composer can show
  // and clear it. `fileInput` is the element itself. A file input's value can
  // only be cleared through the DOM, not by rebinding.
  let fileName = $state('');
  /** @type {HTMLInputElement | undefined} */
  let fileInput;

  /** @param {Event} e */
  function pickFile(e) {
    fileName = /** @type {HTMLInputElement} */ (e.currentTarget).files?.[0]?.name ?? '';
  }
  function clearFile() {
    if (fileInput) fileInput.value = '';
    fileName = '';
  }

  // A contact accepts a file on its own, the API saves the attachment in a
  // block separate from the comment, so the composer sends when there is
  // either a note or a file, and the button says which it will do.
  let canSubmit = $derived(Boolean(note.trim() || fileName));

  // ── activity feed ────────────────────────────────────────────────────────
  // Three real kinds (note / file / created). The filter only appears once there
  // is a file to filter. With nothing but notes it would sort one pile.
  let hasFiles = $derived(activity.some((/** @type {any} */ e) => e.type === 'file'));
  let filter = $state(/** @type {'all'|'notes'|'files'} */ ('all'));
  let shown = $derived(
    filter === 'files'
      ? activity.filter((/** @type {any} */ e) => e.type === 'file')
      : filter === 'notes'
        ? activity.filter((/** @type {any} */ e) => e.type !== 'file')
        : activity
  );
  let newestId = $derived(shown[0]?.id ?? null);

  /**
   * The line under an event: "Attached" for a file, the author where known, then
   * how long ago, joined so no separator dangles when a part is missing.
   * @param {{type:string,by:string|null,at:string}} e
   */
  function metaFor(e) {
    const parts = [];
    if (e.type === 'file') parts.push('Attached');
    if (e.by) parts.push(e.by);
    parts.push(relativeDays(e.at));
    return parts.join(' · ');
  }

  /** @param {string} iso */
  function dayGroup(iso) {
    const n = daysSince(iso);
    if (n === 0) return 'Today';
    if (n === 1) return 'Yesterday';
    return shortDate(iso);
  }

  // Interleave day headers so each date is announced once, in order.
  let feed = $derived.by(() => {
    /** @type {Array<{kind:'day',id:string,label:string}|{kind:'event',id:string,event:any}>} */
    const out = [];
    let last = null;
    for (const e of shown) {
      const g = dayGroup(e.at);
      if (g !== last) {
        out.push({ kind: 'day', id: `day-${g}-${e.id}`, label: g });
        last = g;
      }
      out.push({ kind: 'event', id: e.id, event: e });
    }
    return out;
  });

  /**
   * One sentence, and only where the data can carry it.
   *
   * Ordered by what stops work: somebody who has left, then somebody nobody
   * can reach, then money riding on a record with no owner. Anything softer
   * than that gets no banner at all. A page that always shouts is a page
   * people stop reading.
   */
  let headline = $derived(
    !contact.is_active
      ? `${contact.first_name} is marked inactive${contact.account ? ` at ${contact.account.name}` : ''}. Find out who replaced them before the next conversation.`
      : !contact.email && (!contact.phone || contact.do_not_call)
        ? `There is no way to reach ${contact.first_name} on this record: no email${contact.do_not_call ? ', and they asked not to be called' : ' and no phone'}.`
        : openCount && !contact.owner
          ? `${contact.first_name} is on ${onlyOpenDeal ? onlyOpenDeal.name : `${openCount} open deal${openCount === 1 ? '' : 's'}`} worth ${openPipeline}, and nobody owns this record.`
          : overdueTasks.length
            ? `${overdueTasks.length === 1 ? 'A task' : `${overdueTasks.length} tasks`} naming ${contact.first_name} ${overdueTasks.length === 1 ? 'is' : 'are'} past due.`
            : null
  );
</script>

<PageHeader title={contact.name} record>
  {#snippet leading()}
    <Avatar name={contact.name} size={42} />
  {/snippet}
  {#snippet crumb()}
    <a href={resolve('/contacts')}>Contacts</a>
    <ChevronRight size={12} />
    {#if contact.account}
      <a href={resolve(`/accounts/${contact.account.id}`)}>{contact.account.name}</a>
    {:else if contact.organization}
      <span>{contact.organization}</span>
    {:else}
      <span>No account</span>
    {/if}
  {/snippet}
  {#snippet sub()}
    {[contact.title, contact.department].filter(Boolean).join(' · ') || 'No title recorded'}
    {#if contact.updated_at}
      · updated {relativeDays(contact.updated_at)}
    {/if}
  {/snippet}
  {#snippet actions()}
    {#if contact.email}
      <a class="v2-btn" href="mailto:{contact.email}"><Mail />Email</a>
    {/if}
    {#if contact.do_not_call}
      <!-- Disabled rather than removed: the reason has to stay visible, or
           somebody just looks up the number somewhere else. -->
      <button class="v2-btn" type="button" disabled title="This person asked not to be called">
        <PhoneOff />Do not call
      </button>
    {:else if contact.phone}
      <a class="v2-btn" href="tel:{contact.phone}"><Phone />Call</a>
    {/if}
    <a class="v2-btn v2-btn-primary" href={resolve(`/contacts/${contact.id}/edit`)}
      ><Pencil />Edit</a
    >
  {/snippet}
</PageHeader>

<div style="display:flex;flex:1;min-height:0;overflow:hidden">
  <div class="v2-main">
    <div class="v2-scroll">
      <div class="v2-pad" style="padding-top:16px;padding-bottom:32px">
        {#if headline}
          <div style="margin-bottom:20px">
            <NextAction
              label={contact.is_active ? 'Needs you' : 'Out of date'}
              text={headline}
              action="Edit this contact"
              href="/contacts/{contact.id}/edit"
              tone={contact.is_active ? 'ember' : 'rust'}
            />
          </div>
        {/if}

        <div class="v2-label" style="margin-bottom:10px">
          Deals they are named on
          {#if openCount}
            <span class="v2-num" style="margin-left:6px">{openPipeline}</span> open
          {/if}
        </div>
        <div class="v2-card" style="overflow:hidden;margin-bottom:22px">
          {#each deals as d (d.id)}
            <a
              href={resolve(`/pipeline/${d.id}`)}
              style="display:flex;gap:12px;align-items:center;padding:11px 15px;border-bottom:1px solid var(--v2-line-soft);color:inherit;text-decoration:none"
            >
              <div style="flex:1;min-width:0">
                <div style="font-weight:550;font-size:13px">{d.name}</div>
                <!-- `closed_on` is an expectation while the deal is open and a
                     fact once it is not. A won deal that "closes 22 Aug" reads
                     as still running. -->
                <div class="v2-sub" style="font-size:11.5px">
                  {d.stage_label}{d.closed_on
                    ? CLOSED_KINDS.includes(d.stage_kind)
                      ? ` · closed ${shortDate(d.closed_on)}`
                      : ` · due ${shortDate(d.closed_on)}`
                    : ''}
                </div>
              </div>
              <span class="v2-num" style="font-weight:600;font-size:13px"
                >{money(d.amount, d.currency)}</span
              >
            </a>
          {:else}
            <p class="v2-sub" style="padding:14px 15px;font-size:12.5px">
              No deals name this person. Add them to the deal they are actually involved in. The
              account having deals is a different fact.
            </p>
          {/each}
        </div>

        <div class="v2-label" style="margin-bottom:10px">
          Tasks
          {#if overdueTasks.length}
            <span style="margin-left:6px;color:var(--v2-rust);font-weight:600"
              >· {overdueTasks.length} overdue</span
            >
          {/if}
        </div>
        <div class="v2-card" style="overflow:hidden;margin-bottom:22px">
          {#each tasks as t (t.id)}
            <!-- Overdue reads the same here as it does on the Tasks page. A
                 task that is six days late should not look calmer because you
                 happened to arrive from a contact. -->
            {@const late = t.due_date ? Math.max(0, daysSince(t.due_date) ?? 0) : 0}
            <!-- A link now. These rows have been real since contacts were
                 wired and had nowhere to go, which is what made tasks the
                 module to do next. -->
            <a
              href={resolve(`/tasks/${t.id}`)}
              style="display:flex;gap:12px;align-items:center;padding:11px 15px;border-bottom:1px solid var(--v2-line-soft);color:inherit;text-decoration:none"
            >
              <span style="flex:1;font-size:13px;min-width:0">{t.title}</span>
              <Pill tone={TASK_PRIORITY_TONE[t.priority]}>{t.priority}</Pill>
              <span
                class="v2-sub"
                style={late && t.status !== 'Completed'
                  ? 'font-size:11.5px;white-space:nowrap;color:var(--v2-rust);font-weight:600'
                  : 'font-size:11.5px;white-space:nowrap'}
              >
                {t.status === 'Completed'
                  ? 'done'
                  : late
                    ? `${late}d late`
                    : t.due_date
                      ? relativeDays(t.due_date)
                      : 'no due date'}
              </span>
            </a>
          {:else}
            <p class="v2-sub" style="padding:14px 15px;font-size:12.5px">
              Nothing outstanding that names this person.
            </p>
          {/each}
        </div>

        <div class="v2-label" style="margin-bottom:10px">
          Tickets
          {#if openTickets.length}
            <span class="v2-num" style="margin-left:6px">{openTickets.length}</span> open
          {/if}
        </div>
        <div class="v2-card" style="overflow:hidden;margin-bottom:22px">
          {#each tickets as t (t.id)}
            <!-- A link again: tickets is wired, so a real id sent to
                 `/tickets/<uuid>` opens the ticket. -->
            <a
              href={resolve(`/tickets/${t.id}`)}
              style="display:flex;gap:12px;align-items:center;padding:11px 15px;border-bottom:1px solid var(--v2-line-soft);color:inherit;text-decoration:none"
            >
              <span style="flex:1;font-size:13px;min-width:0">{t.name}</span>
              <Pill tone={CASE_STATUS_TONE[t.status]}>{t.status}</Pill>
              <Pill tone={PRIORITY_TONE[t.priority]}>{t.priority}</Pill>
            </a>
          {:else}
            <p class="v2-sub" style="padding:14px 15px;font-size:12.5px">No tickets.</p>
          {/each}
        </div>

        {#if contact.description}
          <div class="v2-label" style="margin:0 0 10px">About</div>
          <div class="v2-card about">{contact.description}</div>
        {/if}

        <div class="act-head">
          <div class="v2-label">Activity</div>
          {#if hasFiles}
            <!-- Only real kinds. There is no calls/emails/meetings split because
                 there are no such records to split on. -->
            <div class="seg" role="tablist" aria-label="Filter activity">
              <button class:on={filter === 'all'} onclick={() => (filter = 'all')}>All</button>
              <button class:on={filter === 'notes'} onclick={() => (filter = 'notes')}>Notes</button
              >
              <button class:on={filter === 'files'} onclick={() => (filter = 'files')}>Files</button
              >
            </div>
          {/if}
        </div>

        <!-- The composer. `reset: false` plus clearing state by hand on success
             keeps the box and the binding in step; a failed save keeps the words
             and the picked file. A contact takes a note, a file, or both. -->
        <form
          method="POST"
          action="?/note"
          enctype="multipart/form-data"
          class="note-form"
          use:enhance={() => {
            saving = true;
            return async ({ result, update }) => {
              saving = false;
              if (result.type === 'success') {
                note = '';
                clearFile();
              }
              await update({ reset: false });
            };
          }}
        >
          <textarea
            name="comment"
            rows="2"
            bind:value={note}
            class="note-input"
            placeholder="Log a call, a reply, what they said…"></textarea>
          <div class="note-actions">
            <button class="v2-btn v2-btn-primary" type="submit" disabled={saving || !canSubmit}>
              {saving
                ? 'Saving…'
                : note.trim()
                  ? 'Add note'
                  : fileName
                    ? 'Attach file'
                    : 'Add note'}
            </button>
            <label class="v2-btn" class:has-file={fileName}>
              <Paperclip size={14} />
              <span class="attach-label">{fileName || 'Attach file'}</span>
              <input
                bind:this={fileInput}
                type="file"
                name="attachment"
                onchange={pickFile}
                hidden
              />
            </label>
            {#if fileName}
              <button
                type="button"
                class="v2-btn-quiet clear-file"
                onclick={clearFile}
                title="Remove file"
              >
                <X size={13} />
              </button>
            {/if}
            {#if form?.message}
              <span class="note-err">{form.message}</span>
            {/if}
          </div>
        </form>

        <div class="tl">
          {#each feed as row (row.id)}
            {#if row.kind === 'day'}
              <div class="tl-day">{row.label}</div>
            {:else}
              {@const e = row.event}
              <div class="tl-row" class:latest={e.id === newestId}>
                <span class="tl-ico" data-kind={e.type}>
                  {#if e.type === 'file'}<Paperclip size={13} />
                  {:else if e.type === 'status'}<Sparkles size={13} />
                  {:else}<MessageSquare size={13} />{/if}
                </span>
                <div class="tl-body">
                  {#if e.type === 'file' && e.href}
                    <a
                      class="tl-file"
                      href={resolve(e.href)}
                      target="_blank"
                      rel="noreferrer noopener"
                    >
                      {e.body}
                    </a>
                  {:else}
                    <div class="tl-text" class:note={e.type === 'note'}>{e.body}</div>
                  {/if}
                  <div class="tl-meta">{metaFor(e)}</div>
                </div>
              </div>
            {/if}
          {:else}
            <p class="v2-sub" style="font-size:12.5px">
              Nothing logged yet. The first note you add shows up here.
            </p>
          {/each}
        </div>
      </div>
    </div>
  </div>

  <aside class="v2-rail">
    <div class="v2-label v2-rail-head">Contact</div>
    <dl class="v2-kv">
      <dt>Title</dt>
      <dd>{contact.title || '—'}</dd>
      <dt>Department</dt>
      <dd>{contact.department || '—'}</dd>
      <dt>Account</dt>
      <dd>
        {#if contact.account}
          <a href={resolve(`/accounts/${contact.account.id}`)} style="color:inherit"
            >{contact.account.name}</a
          >
        {:else},
        {/if}
      </dd>
      {#if contact.other_accounts.length}
        <dt>Also at</dt>
        <dd>
          {#each contact.other_accounts as other, i (other.id)}
            {i > 0 ? ', ' : ''}<a href={resolve(`/accounts/${other.id}`)} style="color:inherit"
              >{other.name}</a
            >
          {/each}
        </dd>
      {/if}
      {#if contact.organization && contact.organization !== contact.account?.name}
        <!-- Typed into the contact rather than linked, and often a different
             company from the account. Shown as what it is instead of being
             quietly presented as the account. -->
        <dt>Company typed in</dt>
        <dd>{contact.organization}</dd>
      {/if}
      <dt>Email</dt>
      <dd style="font-size:12px;word-break:break-all">
        {#if contact.email}<a href="mailto:{contact.email}" style="color:inherit">{contact.email}</a
          >{:else},
        {/if}
      </dd>
      <dt>Phone</dt>
      <dd class="v2-num" style="font-size:12px">
        {#if contact.phone}<a href="tel:{contact.phone}" style="color:inherit">{contact.phone}</a
          >{:else},
        {/if}
      </dd>
      {#if contact.linkedin_url}
        <dt>LinkedIn</dt>
        <dd style="font-size:12px;word-break:break-all">
          <a href={contact.linkedin_url} rel="external noreferrer noopener" target="_blank"
            >Profile</a
          >
        </dd>
      {/if}
      <dt>Owner</dt>
      <dd>
        {owners.length ? owners.join(', ') : 'Unassigned'}
      </dd>
      <dt>Status</dt>
      <dd>
        <span style="display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap">
          <Pill tone={contact.is_active ? 'moss' : 'slate'}>
            {contact.is_active ? 'Active' : 'Inactive'}
          </Pill>
          {#if contact.do_not_call}
            <Pill tone="rust"><PhoneOff size={11} />Do not call</Pill>
          {/if}
        </span>
      </dd>
      <dt>Added</dt>
      <dd>{shortDate(contact.created_at)}</dd>
      <dt>Updated</dt>
      <dd>{contact.updated_at ? relativeDays(contact.updated_at) : '—'}</dd>
    </dl>

    {#if colleagues.length}
      <div class="v2-label v2-rail-head">
        Also at {contact.account?.name ?? 'this account'}
      </div>
      {#each colleagues as c (c.id)}
        <a
          class="v2-rail-row"
          href={resolve(`/contacts/${c.id}`)}
          style="color:inherit;text-decoration:none;align-items:center"
        >
          <Avatar name={c.name} size={27} />
          <div style="min-width:0">
            <div style="font-size:12.5px;font-weight:550">{c.name}</div>
            <div class="v2-sub" style="font-size:11.5px">{c.title || 'No title recorded'}</div>
          </div>
        </a>
      {/each}
    {/if}
  </aside>
</div>

<style>
  .about {
    padding: 14px 16px;
    font-size: 13px;
    line-height: 1.6;
    white-space: pre-wrap;
    margin-bottom: 22px;
  }

  /* ── note composer ──────────────────────────────────────────────────────── */
  .note-form {
    margin-bottom: 22px;
  }
  .note-input {
    width: 100%;
    padding: 9px 11px;
    font: inherit;
    font-size: 13px;
    color: var(--v2-ink);
    background: var(--v2-card);
    border: 1px solid var(--v2-line);
    border-radius: 8px;
    resize: vertical;
    line-height: 1.5;
  }
  .note-input:focus {
    outline: 2px solid var(--v2-ember);
    outline-offset: -1px;
  }
  .note-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 8px;
    flex-wrap: wrap;
  }
  /* The attach control is a label wrapping a hidden input, so the whole chip is
     the click target. */
  .note-actions label {
    cursor: pointer;
  }
  .note-actions .attach-label {
    max-width: 190px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .note-actions .has-file {
    border-color: var(--v2-slate);
    color: var(--v2-ink);
  }
  .clear-file {
    padding: 5px 7px;
  }
  .note-err {
    color: var(--v2-rust);
    font-size: 12px;
  }

  /* ── activity ───────────────────────────────────────────────────────────── */
  .act-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin: 0 0 12px;
  }
  .seg {
    display: flex;
    border: 1px solid var(--v2-line);
    border-radius: 7px;
    overflow: hidden;
    flex: none;
  }
  .seg button {
    padding: 4px 11px;
    font: inherit;
    font-size: 11.8px;
    color: var(--v2-slate);
    background: var(--v2-card);
    border: 0;
    cursor: pointer;
  }
  .seg button + button {
    border-left: 1px solid var(--v2-line);
  }
  .seg button.on {
    color: var(--v2-ink);
    background: var(--v2-hover);
    font-weight: 600;
  }

  .tl-day {
    font-size: 11px;
    font-weight: 650;
    letter-spacing: 0.02em;
    color: var(--v2-slate);
    margin: 14px 0 9px;
  }
  .tl-day:first-child {
    margin-top: 0;
  }
  .tl-row {
    display: flex;
    gap: 11px;
    align-items: flex-start;
    padding-bottom: 15px;
  }
  /* A small icon chip instead of a bare dot: the kind of event is legible at a
     glance, which is the whole point of a mixed feed. */
  .tl-ico {
    flex: none;
    width: 26px;
    height: 26px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    background: var(--v2-line-soft);
    border: 1px solid var(--v2-line);
    color: var(--v2-slate);
    margin-top: 1px;
  }
  .tl-row.latest .tl-ico {
    background: var(--v2-ink);
    border-color: var(--v2-ink);
    color: var(--v2-paper);
  }
  .tl-body {
    min-width: 0;
    padding-top: 3px;
  }
  .tl-text {
    font-size: 13px;
    line-height: 1.5;
    color: var(--v2-ink);
  }
  .tl-file {
    display: inline-flex;
    font-size: 13px;
    font-weight: 550;
    color: var(--v2-ink);
    text-decoration: none;
    overflow-wrap: anywhere;
  }
  .tl-file:hover {
    text-decoration: underline;
  }
  .tl-meta {
    font-size: 11.5px;
    color: var(--v2-slate);
    margin-top: 2px;
    overflow-wrap: anywhere;
  }
</style>
