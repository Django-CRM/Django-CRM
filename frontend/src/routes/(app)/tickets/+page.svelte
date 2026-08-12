<script>
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { invalidateAll } from '$app/navigation';
  import { SvelteSet } from 'svelte/reactivity';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import SectionTabs from '$lib/v2/components/SectionTabs.svelte';
  import FilterBar from '$lib/v2/components/FilterBar.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import Avatar from '$lib/v2/components/Avatar.svelte';
  import EmptyState from '$lib/v2/components/EmptyState.svelte';
  import BulkActionBar from '$lib/v2/components/BulkActionBar.svelte';
  import { count, shortAge } from '$lib/v2/format.js';
  import { PRIORITY_TONE, CASE_STATUS_TONE } from '$lib/v2/enums.js';
  import { Plus, LifeBuoy } from '@lucide/svelte';

  /** @type {{ data: any }} */
  let { data } = $props();

  let tickets = $derived(data.tickets);
  let totals = $derived(data.totals);

  // Bulk selection: loaded rows only, never the whole filtered set. SvelteSet
  // is reactive on mutation, so add/delete/clear below re-render without
  // reassigning the whole set.
  let selected = new SvelteSet();
  let banner = $state('');

  /** @param {string} id */
  function toggle(id) {
    if (selected.has(id)) selected.delete(id);
    else selected.add(id);
  }
  function toggleAll() {
    if (selected.size === tickets.length) {
      selected.clear();
    } else {
      selected.clear();
      for (const t of tickets) selected.add(t.id);
    }
  }
  /**
   * @param {'update'|'delete'} kind
   * @param {Record<string, number>} s
   */
  function summaryText(kind, s) {
    const parts = [
      `${kind === 'delete' ? s.deleted : s.updated} ${kind === 'delete' ? 'deleted' : 'updated'}`
    ];
    if (s.no_access) parts.push(`${s.no_access} skipped (no access)`);
    if (s.approval_required) parts.push(`${s.approval_required} need approval`);
    if (s.closed_on_required) parts.push(`${s.closed_on_required} missing close date`);
    if (s.invalid) parts.push(`${s.invalid} invalid`);
    return parts.join(' · ');
  }

  /**
   * How the first-reply clock stands.
   *
   * The deadline arrives from the server, where it is walked through the org's
   * business calendar and pushed forward by any time the ticket spent waiting
   * on the customer. Recomputing it here from `opened_at + hours`, which is
   * what the mock did, would put a second, quietly different answer on the
   * same screen.
   *
   * A progress bar only means something while there is still time on the
   * clock. Past the deadline a bar pinned at 100% says nothing about how bad
   * it is, so we stop drawing one and say how far over it went instead.
   *
   * @param {any} t
   */
  function responsePressure(t) {
    if (t.first_response_at) {
      const took =
        (new Date(t.first_response_at).getTime() - new Date(t.opened_at).getTime()) / 6e4;
      return { state: 'met', label: `Met in ${fmtMins(took)}`, tone: 'moss' };
    }
    if (!t.first_response_deadline) {
      return { state: 'none', label: 'No target', tone: 'slate' };
    }
    const now = Date.now();
    const opened = new Date(t.opened_at).getTime();
    const due = new Date(t.first_response_deadline).getTime();
    if (now >= due) {
      return { state: 'breached', label: `${fmtMins((now - due) / 6e4)} over`, tone: 'rust' };
    }
    const pct = Math.max(0, Math.min(100, Math.round(((now - opened) / (due - opened)) * 100)));
    return {
      state: 'running',
      pct,
      label: `${fmtMins((due - now) / 6e4)} left`,
      tone: pct >= 75 ? 'rust' : pct >= 50 ? 'clay' : 'slate'
    };
  }

  /** @param {number} m */
  function fmtMins(m) {
    const n = Math.max(0, Math.round(m));
    if (n < 60) return `${n}m`;
    if (n < 1440) return `${Math.round(n / 60)}h`;
    return `${Math.round(n / 1440)}d`;
  }

  const TONE_VAR = {
    moss: 'var(--v2-moss)',
    rust: 'var(--v2-rust)',
    clay: 'var(--v2-clay)',
    slate: 'var(--v2-slate)'
  };
</script>

<PageHeader title="Tickets">
  {#snippet sub()}
    <span class="v2-num">{count(totals.open)}</span> open ·
    <span class="v2-num" style="color:var(--v2-rust)">{totals.urgent}</span> urgent ·
    <!-- Not "breaching today". A breach depends on the org's business calendar
         and is a per-row calculation; nobody having replied yet is a fact the
         queue can establish, and it is the one that decides what to open. -->
    <span class="v2-num">{count(totals.awaiting_reply)}</span> with no reply yet
  {/snippet}
  {#snippet actions()}
    <a class="v2-btn v2-btn-primary" href={resolve('/tickets/new')}><Plus />New ticket</a>
  {/snippet}
</PageHeader>

{#if page.url.search}
  <p class="v2-sub" style="font-size:11.5px;margin:8px 0 0">
    These numbers describe the filtered queue.
  </p>
{/if}

<!-- Approvals and Analytics were buttons in this header that went nowhere.
     They are sibling pages, so they belong in a tab strip that also tells you
     which one you are on. -->
<SectionTabs set="tickets" />

{#if banner}
  <p class="v2-sub" style="font-size:12px;margin:8px 0 0">{banner}</p>
{/if}
{#if selected.size > 0}
  <BulkActionBar
    ids={[...selected]}
    people={data.people}
    tags={data.tags}
    onclear={() => selected.clear()}
    ondone={async () => {
      // The form action returned; show its summary, refresh, clear selection.
      const r = page.form;
      if (r?.ok) banner = summaryText(r.kind, r.summary);
      selected.clear();
      await invalidateAll();
    }}
  />
{/if}

<FilterBar
  page="tickets"
  url={page.url}
  people={data.people}
  tags={data.tags}
  meId={data.meId}
  meta="First-reply targets come from each ticket's SLA hours"
/>

<div class="v2-scroll">
  {#if tickets.length === 0}
    <!-- An empty queue is good news, so it does not read like a failure. -->
    <EmptyState
      title={data.showAll ? 'No tickets here yet' : 'The queue is clear'}
      body={data.showAll
        ? 'Nothing has been raised in this workspace. Tickets arrive here from email, the portal, and anyone who replies to a closed one.'
        : 'Nothing is waiting on your team right now. Closed and rejected tickets are still here. They are just not in the way.'}
    >
      {#snippet icon()}<LifeBuoy size={21} />{/snippet}
      {#snippet actions()}
        <a class="v2-btn v2-btn-primary" href={resolve('/tickets/new')}>New ticket</a>
        {#if !data.showAll}
          <a class="v2-btn" href={resolve('/tickets?all=1')}>Show closed too</a>
        {/if}
        <a class="v2-btn" href={resolve('/solutions')}>Knowledge base</a>
      {/snippet}
    </EmptyState>
  {:else}
    <div class="v2-table-wrap">
      <table class="v2-table">
        <thead>
          <tr>
            <th style="width:34px">
              <input
                type="checkbox"
                aria-label="Select all loaded"
                checked={tickets.length > 0 && selected.size === tickets.length}
                onchange={toggleAll}
              />
            </th>
            <th>Subject</th>
            <th>Priority</th>
            <th>Status</th>
            <th>Type</th>
            <th>Account</th>
            <th>Assignee</th>
            <th class="v2-r">Age</th>
            <th style="width:130px">First reply</th>
          </tr>
        </thead>
        <tbody>
          {#each tickets as t (t.id)}
            {@const p = responsePressure(t)}
            <tr>
              <td data-m="lead">
                <input
                  type="checkbox"
                  aria-label="Select ticket"
                  checked={selected.has(t.id)}
                  onchange={() => toggle(t.id)}
                />
              </td>
              <td data-m="title">
                <a class="v2-row-link" href={resolve(`/tickets/${t.id}`)}>
                  <span class="v2-table-primary">{t.name}</span>
                </a>
              </td>
              <td><Pill tone={PRIORITY_TONE[t.priority]}>{t.priority}</Pill></td>
              <td data-m="tag"><Pill tone={CASE_STATUS_TONE[t.status]}>{t.status}</Pill></td>
              <!-- Nullable on the model and null on plenty of rows, so it says
                   so rather than printing an empty cell. -->
              <td class="v2-muted" data-m="hide" style="font-size:12.5px">
                {t.case_type ?? '—'}
              </td>
              <td class="v2-muted" style="font-size:12.5px">
                {#if t.account}
                  <a class="v2-row-link" href={resolve(`/accounts/${t.account.id}`)}
                    >{t.account.name}</a
                  >
                {:else}
                  No account
                {/if}
              </td>
              <td data-m="hide">
                {#if t.assignee}
                  <Avatar name={t.assignee} size={22} />
                {:else}
                  <span class="v2-muted" style="font-size:12.5px">Unassigned</span>
                {/if}
              </td>
              <td class="v2-r v2-num v2-muted" data-m="meta">{shortAge(t.opened_at)}</td>
              <!-- Kept on a phone, unlike the other trailing columns: a running
                   first-reply clock is the one thing in this queue that decides
                   what to open next. It takes its own line so the meter has a
                   width to fill. -->
              <td data-m="bar">
                {#if p.state === 'running'}
                  <div style="display:flex;align-items:center;gap:8px">
                    <span
                      style="flex:1;height:4px;border-radius:3px;background:var(--v2-line);overflow:hidden;display:block"
                    >
                      <i
                        style="display:block;height:100%;width:{p.pct}%;background:{TONE_VAR[
                          p.tone
                        ]}"
                      ></i>
                    </span>
                    <span class="v2-num" style="font-size:11px;color:{TONE_VAR[p.tone]}"
                      >{p.label}</span
                    >
                  </div>
                {:else}
                  <span class="v2-num" style="font-size:11.5px;color:{TONE_VAR[p.tone]}"
                    >{p.label}</span
                  >
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <p class="v2-sub v2-pad" style="font-size:12px;padding-bottom:24px">
      Showing <span class="v2-num">{tickets.length}</span> of
      <span class="v2-num">{count(totals.count)}</span>
      {#if !data.showAll}
        · <a href={resolve('/tickets?all=1')} style="color:inherit">include closed</a>
      {:else}
        · <a href={resolve('/tickets')} style="color:inherit">open only</a>
      {/if}
    </p>
  {/if}
</div>
