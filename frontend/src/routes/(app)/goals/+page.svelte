<script>
  import { resolve } from '$app/paths';
  /**
   * Quota, read against the calendar.
   *
   * A percentage of target is meaningless on its own: 59% is excellent in week
   * two of a quarter and dire in week eleven. SalesGoal.status already knows
   * this. It compares progress against expected pace and returns on_track /
   * at_risk / behind, but v1 rendered only the raw percentage, so the model's
   * one interesting judgement never reached the screen. Here every bar carries
   * a pace marker showing how far through the period we are, and the gap
   * between fill and marker is the whole story.
   *
   * The elapsed-time figure is computed here from period_start and period_end,
   * which is date arithmetic on two fields already in the payload, not an
   * aggregate. Everything that counts records (progress_value,
   * progress_percent, status) stays server-side.
   */
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import StatCard from '$lib/v2/components/StatCard.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import Avatar from '$lib/v2/components/Avatar.svelte';
  import EmptyState from '$lib/v2/components/EmptyState.svelte';
  import { money, count, shortDate, daysSince } from '$lib/v2/format.js';
  import {
    GOAL_TYPE_LABEL,
    GOAL_STATUS_LABEL,
    GOAL_STATUS_TONE,
    PERIOD_TYPE_LABEL
  } from '$lib/v2/enums.js';
  import { Plus, Target, Trophy, History } from '@lucide/svelte';

  /** @type {{ data: any }} */
  let { data } = $props();

  let totals = $derived(data.totals);

  /**
   * The filter bar submits as a plain GET form, so the state lives in the URL:
   * a filtered view is linkable, survives a reload, and re-runs the server load
   * that applies it. The backend does the filtering (`period_type`, `search`,
   * `current`, `active` on the list endpoint); nothing here narrows an array
   * that was already fetched in full.
   */
  let filters = $derived(data.filters ?? { period_type: '', q: '', window: '' });
  let filtered = $derived(Boolean(filters.period_type || filters.q || filters.window));

  /** How many deal types this goal re-weighs, for the badge on its card. */
  const weightedTypes = (g) =>
    Object.entries(g.type_weights ?? {}).filter(([, w]) => Number(w) !== 1).length;

  /** How far through the period we are, 0-100. */
  function elapsedPercent(g) {
    const start = new Date(g.period_start).getTime();
    const end = new Date(g.period_end).getTime();
    if (!(end > start)) return 100;
    return Math.min(100, Math.max(0, Math.round(((Date.now() - start) / (end - start)) * 100)));
  }

  const isOver = (g) => (daysSince(g.period_end) ?? -1) > 0;

  /**
   * SalesGoal.status has no terminal "missed": once period_end passes,
   * expected_pace is pinned at 100, so a goal that ended weeks short keeps
   * reporting "behind" as though you could still do something about it. A
   * finished period is reported as met or missed instead.
   */
  const statusLabel = (g) =>
    isOver(g) ? (g.progress_percent >= 100 ? 'Target met' : 'Missed') : GOAL_STATUS_LABEL[g.status];

  const statusTone = (g) =>
    isOver(g) ? (g.progress_percent >= 100 ? 'moss' : 'slate') : GOAL_STATUS_TONE[g.status];

  const barColor = (g) => {
    const tone = statusTone(g);
    return tone === 'moss'
      ? 'var(--v2-moss)'
      : tone === 'rust'
        ? 'var(--v2-rust)'
        : tone === 'clay'
          ? 'var(--v2-clay)'
          : 'var(--v2-slate)';
  };

  /** Revenue goals are money; deals and activities goals are plain counts. */
  const value = (g, n) => (g.goal_type === 'REVENUE' ? money(n, data.org.currency) : count(n));
</script>

<PageHeader title="Goals">
  {#snippet sub()}
    <span class="v2-num">{money(totals.achieved, data.org.currency)}</span> of
    <span class="v2-num">{money(totals.target, data.org.currency)}</span> across
    <span class="v2-num">{count(totals.active)}</span> active goals
  {/snippet}
  {#snippet actions()}
    <a class="v2-btn" href={resolve('/goals/history')}><History />History</a>
    {#if data.can_edit}
      <a class="v2-btn v2-btn-primary" href={resolve('/goals/new')}><Plus />New goal</a>
    {/if}
  {/snippet}
</PageHeader>

<div class="v2-pad" style="padding-top:16px;flex:none">
  <div class="v2-stats">
    <StatCard
      label="Committed"
      value={money(totals.target, data.org.currency)}
      tone="ink"
      detail="Active goals only"
    />
    <StatCard
      label="Booked"
      value={money(totals.achieved, data.org.currency)}
      tone="moss"
      detail="Closed-won in period"
    />
    <StatCard
      label="Behind pace"
      value={count(totals.behind)}
      tone={totals.behind ? 'rust' : 'slate'}
      detail={totals.behind ? 'Slower than the calendar' : 'Everyone is on pace'}
    />
    <StatCard label="Active goals" value={count(totals.active)} tone="slate" />
  </div>

  <form class="filters" method="GET" data-sveltekit-keepfocus data-sveltekit-replacestate>
    <input
      class="v2-input"
      type="search"
      name="q"
      value={filters.q}
      placeholder="Search goals by name"
      aria-label="Search goals by name"
    />
    <select class="v2-input" name="period_type" aria-label="Filter by period">
      <option value="">Any period</option>
      {#each Object.entries(PERIOD_TYPE_LABEL) as [key, label] (key)}
        <option value={key} selected={filters.period_type === key}>{label}</option>
      {/each}
    </select>
    <select class="v2-input" name="window" aria-label="Filter by window">
      <option value="">All goals</option>
      <option value="current" selected={filters.window === 'current'}>Running today</option>
      <option value="active" selected={filters.window === 'active'}>Not paused</option>
    </select>
    <button class="v2-btn" type="submit">Filter</button>
    {#if filtered}
      <a class="v2-btn" href={resolve('/goals')}>Clear</a>
    {/if}
  </form>
</div>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-bottom:32px">
    {#if data.goals.length === 0}
      <!-- Three messages. The list is narrowed server-side to the goals a
           non-admin may see (their own and their teams'), so "No goals set" is
           a claim about the org that a member may be reading in front of an org
           full of goals that are simply not theirs. And with a filter applied
           neither claim is true: the goals exist, this view just excluded them,
           so saying "no goals set" would send someone off to create a duplicate
           of one they already have. -->
      {#if filtered}
        <EmptyState
          title="No goals match this filter"
          body="Nothing here matches the search and period you picked. Clearing the filter shows everything you can see."
        >
          {#snippet icon()}<Target size={21} />{/snippet}
          {#snippet actions()}
            <a class="v2-btn" href={resolve('/goals')}>Clear filter</a>
          {/snippet}
        </EmptyState>
      {:else}
        <EmptyState
          title={data.can_edit ? 'No goals set' : 'Nothing assigned to you'}
          body={data.can_edit
            ? 'A goal is a target and a period. Once one exists, closed-won deals count towards it automatically. Nobody has to update a number.'
            : 'Nothing is assigned to you or your teams. An administrator sets these, and closed-won deals count towards them automatically once one exists.'}
        >
          {#snippet icon()}<Target size={21} />{/snippet}
          {#snippet actions()}
            {#if data.can_edit}
              <a class="v2-btn v2-btn-primary" href={resolve('/goals/new')}>New goal</a>
            {/if}
          {/snippet}
        </EmptyState>
      {/if}
    {:else}
      <div class="v2-split v2-split-wide">
        <div>
          <div class="v2-label" style="margin-bottom:10px">This period</div>
          <div style="display:flex;flex-direction:column;gap:10px">
            {#each data.goals as g (g.id)}
              {@const elapsed = elapsedPercent(g)}
              {@const over = isOver(g)}
              <div class="v2-card" style="padding:15px 17px;opacity:{g.is_active ? 1 : 0.65}">
                <div style="display:flex;gap:10px;align-items:flex-start;margin-bottom:11px">
                  <div style="flex:1;min-width:0">
                    <div style="font-weight:600;font-size:13.5px">{g.name}</div>
                    <div class="v2-sub" style="font-size:11.5px;margin-top:2px">
                      {GOAL_TYPE_LABEL[g.goal_type]} · {PERIOD_TYPE_LABEL[g.period_type]} ·
                      {shortDate(g.period_start)} - {shortDate(g.period_end)}
                      <!-- Named on the card because a weighted goal's progress
                           does not add up to the deals behind it, and someone
                           checking the arithmetic against the pipeline needs to
                           know that before they file a bug. -->
                      {#if weightedTypes(g)}
                        · <span title="Some deal types count at an adjusted value"
                          >weighted ({weightedTypes(g)})</span
                        >
                      {/if}
                    </div>
                  </div>
                  <div style="display:flex;flex-direction:column;align-items:flex-end;gap:5px">
                    <Pill tone={statusTone(g)}>{statusLabel(g)}</Pill>
                    {#if data.can_edit}
                      <a
                        href={resolve(`/goals/${g.id}/edit`)}
                        style="font-size:11px;color:var(--v2-slate);text-decoration:none">Edit</a
                      >
                    {/if}
                  </div>
                </div>

                <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:6px">
                  <span class="v2-num" style="font-weight:650;font-size:15px">
                    {value(g, g.progress_value)}
                  </span>
                  <span class="v2-sub" style="font-size:12px">
                    of {value(g, g.target_value)}
                  </span>
                  <!-- The server's progress_percent, not a division done here.
                       SalesGoal floors it (int()) and caps it at 100, so
                       recomputing gives 60% where the model says 59% and a
                       reader has no way to tell which is real. Over-target is
                       readable from the two figures to the left. -->
                  <span
                    class="v2-num"
                    style="margin-left:auto;font-weight:600;font-size:12.5px;color:{barColor(g)}"
                  >
                    {g.progress_percent}%
                  </span>
                </div>

                <!-- Fill is attainment; the hairline is where the calendar
                     says you should be. Behind means the fill is left of the
                     line, and you can see that without reading a number. -->
                <div class="v2-bar">
                  <i style="width:{g.progress_percent}%;background:{barColor(g)}"></i>
                  {#if !over}
                    <span
                      class="v2-bar-pace"
                      style="left:calc({elapsed}% - 1px)"
                      title="{elapsed}% through the period"
                    ></span>
                  {/if}
                </div>
                <div class="v2-bar-legend">
                  <span>
                    {#if g.assigned_to}
                      {g.assigned_to.name}
                    {:else if g.team}
                      {g.team.name} (team)
                    {:else}
                      Whole org
                    {/if}
                  </span>
                  <span>
                    {#if over}
                      Period ended {shortDate(g.period_end)}
                    {:else}
                      <span class="v2-num">{elapsed}%</span> through the period
                    {/if}
                  </span>
                </div>
              </div>
            {/each}
          </div>
        </div>

        <div>
          <div class="v2-label" style="margin-bottom:10px">
            <Trophy size={12} style="vertical-align:-1px;margin-right:4px" />
            Leaderboard
          </div>
          <div class="v2-card" style="overflow:hidden">
            {#each data.leaderboard as row (row.goal_id)}
              <div
                style="display:flex;gap:11px;align-items:center;padding:11px 14px;border-bottom:1px solid var(--v2-line-soft)"
              >
                <span
                  class="v2-num v2-muted"
                  style="width:15px;text-align:right;font-size:12px;flex:none">{row.rank}</span
                >
                <Avatar name={row.user} size={26} />
                <div style="flex:1;min-width:0">
                  <div style="font-size:12.5px;font-weight:550">{row.user}</div>
                  <div class="v2-sub v2-num" style="font-size:11px">
                    {money(row.achieved, data.org.currency)} of {money(
                      row.target,
                      data.org.currency
                    )}
                  </div>
                </div>
                <!-- Uncapped on purpose: 104% is the interesting number, and
                     the model caps progress_percent at 100, so the leaderboard
                     carries its own. -->
                <span
                  class="v2-num"
                  style="font-weight:650;font-size:13px;color:{row.percent >= 100
                    ? 'var(--v2-moss)'
                    : 'var(--v2-ink)'}"
                >
                  {row.percent}%
                </span>
              </div>
            {:else}
              <!-- Newly reachable: the board is now scoped the way the list
                   is, so somebody with no current monthly goal of their own
                   sees nothing here rather than the whole org. An empty card
                   under a heading reads as a failure, so it says why. -->
              <p class="v2-sub" style="padding:14px;margin:0;font-size:12px">
                Nothing to rank yet. The board covers monthly goals running today, and shows the
                ones you can see: your own, and your teams'.
              </p>
            {/each}
          </div>

          {#if data.leaderboard.length}
            <p class="v2-sub" style="font-size:11.5px;margin-top:11px">
              Ranked on attainment against each person's own target, not on raw revenue. Otherwise
              the biggest patch wins every quarter regardless of who worked hardest.
            </p>
          {/if}
        </div>
      </div>
    {/if}
  </div>
</div>

<style>
  /*
    Phone first: one control per row at 390px, where a four-across bar would
    squeeze the search box to nothing. It widens into a single row from the
    768px breakpoint v2.css already uses.
  */
  .filters {
    display: grid;
    grid-template-columns: 1fr;
    gap: 8px;
    margin-top: 12px;
  }

  .filters :global(.v2-btn) {
    min-height: 44px;
    justify-content: center;
  }

  .filters :global(.v2-input) {
    min-height: 44px;
  }

  @media (min-width: 768px) {
    .filters {
      grid-template-columns: minmax(0, 1fr) auto auto auto auto;
      align-items: center;
    }
  }
</style>
