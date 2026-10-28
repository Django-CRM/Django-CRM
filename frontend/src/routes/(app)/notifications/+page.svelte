<script lang="ts">
  import { resolve } from '$app/paths';
  import { asInternalPath } from '#lib/utils/paths.js';
  /**
   * Notifications, as a page rather than a 360px dropdown.
   *
   * v1 has a bell and a panel and no page, which is fine for two items and
   * useless for twenty: you cannot scan a backlog through a letterbox, and
   * the panel's only bulk control is "mark all read", which is the control
   * you press when you have given up. A page can be worked through.
   *
   * Two things the real feed does that shaped this:
   *
   *   1. THE LINKS USED TO BE BROKEN. `cases/notifications.py` wrote
   *      `link=f"/cases/{case.id}"` and no client has ever served `/cases`.
   *      Tickets are at `/tickets/<id>`, so v1's panel, which assigns
   *      `n.link` straight to `window.location.href`, sent every notification
   *      to a 404. Fixed at the producer (`case_link()`), which is the only
   *      place it could be fixed once: the web panel, this page and the
   *      Flutter client all read the same rows. `api.js` still rewrites the
   *      dead prefix defensively, because rows written before the fix are
   *      still in the database.
   *
   *   2. ONLY TWO VERBS ARE EVER PRODUCED, `case.mentioned` and
   *      `case.commented`. v1's panel carries labels for five more that
   *      nothing dispatches, which reads as a rich notification system and is
   *      one comment hook. Anything unrecognised is rendered as a plain,
   *      readable sentence rather than falling through to a raw dotted
   *      identifier, because a new producer will ship before its copy does.
   *
   * `data.comment_excerpt` is somebody's comment body, truncated server-side.
   * It is interpolated as text and never as markup.
   */
  import { untrack } from 'svelte';
  import { deserialize } from '$app/forms';
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import EmptyState from '#lib/v2/components/EmptyState.svelte';
  import Avatar from '#lib/v2/components/Avatar.svelte';
  import { relativeTime } from '#lib/v2/format.js';
  import { isOn, notificationsEmptyText } from '#lib/v2/modules.js';
  import { notificationText, actorName, AUTOMATION_FALLBACK } from '#lib/v2/automation.js';
  import {
    BellOff,
    AtSign,
    MessageSquare,
    Bell,
    Check,
    LifeBuoy,
    Zap,
    TriangleAlert,
    CirclePause,
    Gauge,
    FileCheck,
    FileText,
    FileX,
    UserPlus,
    Clock
  } from '@lucide/svelte';
  import type { PageData, PageProps } from './$types';

  let { data }: PageProps = $props();

  type Row = PageData['results'][number];

  let emptyText = $derived(notificationsEmptyText(data.modules));

  /* Local copy: marking read is a real state change in this page's terms even
     though nothing is persisted yet. It is not derived from `data`, or every
     revalidation would resurrect what you just cleared. */
  let rows: Row[] = $state(untrack(() => data.results.map((n) => ({ ...n }))));
  let filter = $state('unread' as 'unread' | 'all');

  let unread = $derived(rows.filter((n) => n.read_at === null));
  let visible = $derived(filter === 'unread' ? unread : rows);

  const ICON: Record<string, typeof Bell> = {
    'case.mentioned': AtSign,
    'case.commented': MessageSquare,
    'support.replied': LifeBuoy,
    'support.status_changed': LifeBuoy,
    'automation.message': Zap,
    'automation.failed': TriangleAlert,
    'automation.limit': TriangleAlert,
    'automation.paused': CirclePause,
    'project.budget': Gauge,
    'project.retainer_invoice': FileText,
    'estimate.accepted': FileCheck,
    'estimate.declined': FileX,
    'lead.assigned': UserPlus,
    'lead.created': UserPlus,
    'lead.enquired': MessageSquare,
    'lead.waiting': Clock
  };

  /**
   * "mentioned you" / "commented" for the verbs that exist; a readable
   * fallback for the ones that do not. `case.sla_breached` becomes
   * "sla breached" and not `case.sla_breached`.
   */
  function verbPhrase(n: Row) {
    if (n.verb === 'case.mentioned') return 'mentioned you on';
    if (n.verb === 'case.commented') return 'commented on';
    if (n.verb === 'support.replied') return 'replied to';
    if (n.verb === 'support.status_changed') return 'updated';
    return `${n.verb.replace(/^[^.]+\./, '').replace(/_/g, ' ')}, `;
  }

  /* The wording of the push, so the bell and the phone say the same thing. */
  const LEAD_COPY: Record<string, [string, string]> = {
    'lead.assigned': ['New lead assigned: ', ''],
    'lead.created': ['New lead: ', ''],
    'lead.enquired': ['', ' enquired again'],
    'lead.waiting': ['', ' is waiting for a reply']
  };

  function leadCopy(n: Row) {
    return LEAD_COPY[n.verb] ?? null;
  }

  function leadSource(n: Row) {
    const source = n.data?.source;
    return typeof source === 'string' && source ? ` (${source})` : '';
  }

  function isAutomation(n: Row) {
    return n.verb?.startsWith('automation.');
  }

  function isTicketVerb(n: Row) {
    return n.verb?.startsWith('case.') || n.verb?.startsWith('support.');
  }

  function isSupportNotification(n: Row) {
    return n.verb?.startsWith('support.');
  }

  /* Persist to a page action. `keepalive` so the write survives the navigation
     that a link click starts, clicking a notification both opens the ticket
     AND marks it read, and the read must not be cancelled mid-flight. */
  async function post(action: string, body: FormData) {
    const res = await fetch(action, { method: 'POST', body, keepalive: true });
    return deserialize(await res.text());
  }

  /* Marking read is optimistic, the dot clears immediately, then persisted.
     If the API refuses (or the network fails) the change is put back, so the
     page never shows a state the server did not accept. */
  async function markRead(n: Row) {
    if (n.read_at !== null) return;
    n.read_at = new Date().toISOString();
    const body = new FormData();
    body.set('id', n.id);
    try {
      const result = await post('?/read', body);
      if (result.type !== 'success') n.read_at = null;
    } catch {
      n.read_at = null;
    }
  }

  async function markAllRead() {
    const wasUnread = rows.filter((n) => n.read_at === null);
    if (wasUnread.length === 0) return;
    const now = new Date().toISOString();
    for (const n of wasUnread) n.read_at = now;
    try {
      const result = await post('?/readAll', new FormData());
      if (result.type !== 'success') for (const n of wasUnread) n.read_at = null;
    } catch {
      for (const n of wasUnread) n.read_at = null;
    }
  }
</script>

<PageHeader title="Notifications">
  {#snippet sub()}
    {#if unread.length}
      <span class="v2-num">{unread.length}</span> unread
    {:else}
      Nothing unread
    {/if}
  {/snippet}
  {#snippet actions()}
    <button
      class="v2-btn"
      type="button"
      onclick={() => (filter = filter === 'unread' ? 'all' : 'unread')}
    >
      {filter === 'unread' ? 'Show read too' : 'Unread only'}
    </button>
    <button class="v2-btn" type="button" disabled={!unread.length} onclick={markAllRead}>
      <Check />Mark all read
    </button>
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:16px;padding-bottom:32px">
    {#if visible.length === 0}
      <EmptyState
        title={filter === 'unread' ? 'Nothing unread' : 'No notifications'}
        body={filter === 'unread' ? `Everything here has been read. ${emptyText}` : emptyText}
      >
        {#snippet icon()}<BellOff size={21} />{/snippet}
        {#snippet actions()}
          {#if filter === 'unread' && rows.length}
            <button class="v2-btn" type="button" onclick={() => (filter = 'all')}>
              Show read too
            </button>
          {/if}
          {#if isOn(data.modules, 'tickets')}
            <a class="v2-btn" href={resolve('tickets')}>Go to tickets</a>
          {/if}
          <a class="v2-btn" href={resolve('help')}>Get help</a>
        {/snippet}
      </EmptyState>
    {:else}
      <ul class="feed">
        {#each visible as n (n.id)}
          {@const Icon = ICON[n.verb] ?? Bell}
          {@const auto = notificationText(n, data.org?.timezone ?? 'UTC')}
          {@const copy = leadCopy(n)}
          <li class="row" class:read={n.read_at !== null}>
            <span class="mark" aria-hidden="true">
              {#if n.read_at === null}<i class="dot"></i>{/if}
            </span>

            <span class="icon"><Icon size={14} /></span>

            <div class="body">
              <p class="line">
                {#if copy}
                  {@const [before, after] = copy}
                  {before}{#if n.resolved_link}<a
                      href={resolve(asInternalPath(n.resolved_link))}
                      onclick={() => markRead(n)}>{n.entity_name || 'A lead'}</a
                    >{:else}<span class="entity">{n.entity_name || 'A lead'}</span>{/if}{leadSource(
                    n
                  )}{after}
                {:else}
                  {#if isSupportNotification(n)}
                    <b class="system">BottleCRM Support</b>
                  {:else if n.actor}
                    <Avatar name={actorName(n)} size={17} />
                    <b>{actorName(n)}</b>
                  {:else}
                    <b class="system">The system</b>
                  {/if}
                  {#if isAutomation(n) || auto}
                    {#if n.resolved_link}
                      <a
                        class="auto-text"
                        href={resolve(asInternalPath(n.resolved_link))}
                        onclick={() => markRead(n)}>{auto?.text ?? AUTOMATION_FALLBACK}</a
                      >
                    {:else}
                      <span class="auto-text">{auto?.text ?? AUTOMATION_FALLBACK}</span>
                    {/if}
                  {:else}
                    {verbPhrase(n)}
                  {/if}
                  {#if isAutomation(n) || auto}
                    <!-- the sentence above carries the link -->
                  {:else if n.entity_name}
                    {#if n.resolved_link}
                      <a href={resolve(asInternalPath(n.resolved_link))} onclick={() => markRead(n)}
                        >{n.entity_name}</a
                      >
                    {:else}
                      <span class="entity">{n.entity_name}</span>
                    {/if}
                  {:else if isTicketVerb(n)}
                    <span class="entity v2-muted">a ticket that no longer has a name</span>
                  {/if}
                {/if}
              </p>

              {#if auto?.source}
                <p class="source">{auto.source}</p>
              {/if}

              {#if n.data.comment_excerpt}
                <!-- Quoted, not restyled as a card: it is somebody else's
                     words and should read like a quotation. -->
                <p class="excerpt">{n.data.comment_excerpt}</p>
              {/if}

              <p class="meta">
                <span>{relativeTime(n.created_at)}</span>
                {#if !n.known_verb}
                  <!-- A verb the backend does not produce today. Worth saying
                       once, quietly, rather than rendering a raw identifier and
                       letting somebody assume the feature exists.

                       The broken link is NOT flagged per row: it is true of
                       every row, so a badge on each one is a badge that says
                       nothing. It is counted once, below the list. -->
                  <span class="tag"><code>{n.verb}</code> has no producer</span>
                {/if}
              </p>
            </div>

            {#if n.read_at === null}
              <button class="v2-btn v2-btn-sm read-btn" type="button" onclick={() => markRead(n)}>
                Mark read
              </button>
            {/if}
          </li>
        {/each}
      </ul>

      {#if data.totals.broken_links}
        <!--
          The count, once, at the bottom, not repeated per row where it would
          become the loudest thing on a page about other people's messages.
        -->
        <p class="footnote">
          <span class="v2-num">{data.totals.broken_links}</span> of these were written before the
          producer was fixed and still carry a <code>/cases/…</code> link, which no client serves.
          They open as <code>/v2/tickets/…</code> here. New ones are written correctly at source by
          <code>cases/notifications.py</code>.
        </p>
      {/if}
    {/if}
  </div>
</div>

<style>
  .feed {
    list-style: none;
    margin: 0;
    padding: 0;
    border-top: 1px solid var(--v2-line);
  }
  .row {
    display: grid;
    grid-template-columns: 14px 22px 1fr auto;
    gap: 10px;
    align-items: start;
    padding: 13px 0;
    border-bottom: 1px solid var(--v2-line);
  }

  /* Unread is carried by a dot in its own column, so every row's text starts
     on the same x. A bold-vs-regular treatment makes read rows look like a
     different kind of thing rather than the same thing, already seen. */
  .mark {
    display: flex;
    justify-content: center;
    padding-top: 6px;
  }
  /* Ink, not ember. Ember is the action token, and on a page where every row
     is unread by default an ember dot on all of them would say "urgent" about
     the whole list and therefore about nothing. */
  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--v2-ink);
  }
  .icon {
    display: flex;
    justify-content: center;
    padding-top: 2px;
    color: var(--v2-slate);
  }
  .row.read .icon {
    opacity: 0.55;
  }

  .body {
    min-width: 0;
  }
  .line {
    margin: 0;
    font-size: 14px;
    line-height: 1.5;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 5px;
  }
  .line b {
    font-weight: 600;
  }
  .system {
    color: var(--v2-slate);
  }
  .line a {
    color: inherit;
    text-decoration: underline;
    text-underline-offset: 2px;
    text-decoration-color: var(--v2-line);
  }
  .line a:hover {
    text-decoration-color: currentColor;
  }
  .entity {
    font-weight: 500;
  }

  .excerpt {
    margin: 5px 0 0;
    padding-left: 10px;
    border-left: 2px solid var(--v2-line);
    font-size: 13px;
    color: var(--v2-slate);
    max-width: 72ch;
    line-height: 1.55;
  }

  .source {
    margin: 3px 0 0;
    font-size: 12px;
    color: var(--v2-slate);
  }
  .auto-text {
    min-width: 0;
    overflow-wrap: anywhere;
  }

  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin: 5px 0 0;
    font-size: 11.5px;
    color: var(--v2-slate);
  }
  .tag {
    display: inline-flex;
    align-items: center;
    gap: 3px;
  }
  .meta code,
  .footnote code {
    font-family: var(--v2-mono);
    font-size: 10.5px;
  }

  /* Appears on hover or keyboard focus, but is always in the accessibility
     tree. A control you can only reach with a mouse is not a control. */
  .read-btn {
    opacity: 0;
    transition: opacity 0.1s;
  }
  .row:hover .read-btn,
  .read-btn:focus-visible {
    opacity: 1;
  }

  .footnote {
    margin: 16px 0 0;
    font-size: 12px;
    color: var(--v2-slate);
    max-width: 76ch;
    line-height: 1.6;
  }

  @media (max-width: 640px) {
    .row {
      grid-template-columns: 10px 18px 1fr;
    }
    .read-btn {
      display: none;
    }
  }
</style>
