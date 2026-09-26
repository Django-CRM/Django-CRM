<script>
  import { resolve } from '$app/paths';
  /**
   * Service health. The questions, in the order a support lead asks them:
   * is the queue growing, are we answering in time (the first reply, then
   * every reply after it), are customers satisfied, who is carrying it, and
   * what is it made of. Every figure covers the same days: the satisfaction
   * and next-response figures are fetched from the window's first day (see
   * `service.js`).
   *
   * No chart library. Every mark here is a div sized by a percentage, which
   * keeps the page honest about how little it is actually drawing, and a
   * fourteen-bar series does not need an axis, a legend and 90kb of SVG.
   *
   * WHAT THIS PAGE DOES NOT DO
   * It never averages across priorities. "SLA attainment: 92%" folds four
   * different promises into one number that describes none of them; an urgent
   * incident answered in ninety minutes and a low-priority question answered
   * in ninety minutes are not the same event. Attainment is reported per
   * priority, against that priority's own target, or not at all.
   */
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import SectionTabs from '$lib/v2/components/SectionTabs.svelte';
  import StatCard from '$lib/v2/components/StatCard.svelte';
  import Avatar from '$lib/v2/components/Avatar.svelte';
  import { count, shortDate } from '$lib/v2/format.js';
  import { Clock } from '@lucide/svelte';

  /** @type {{ data: any }} */
  let { data } = $props();

  let canView = $derived(data.can_view);
  let totals = $derived(data.totals);
  // Floor at 1 so an empty or all-zero window scales cleanly to flat bars
  // rather than dividing by a zero (or -Infinity) peak.
  let peak = $derived(Math.max(1, ...data.volume.map((d) => Math.max(d.opened, d.closed))));

  /**
   * Minutes → "41m" / "2h 20m" / "1d 3h" / "1d". A number of minutes is not a
   * duration, and neither is "1d 0h". A zero remainder is dropped rather than
   * printed, at every scale.
   */
  function duration(mins) {
    if (mins == null) return '—';
    if (mins < 60) return `${mins}m`;
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    if (h < 24) return m ? `${h}h ${m}m` : `${h}h`;
    const d = Math.floor(h / 24);
    return h % 24 ? `${d}d ${h % 24}h` : `${d}d`;
  }

  // Attainment among *decided* cases (met vs missed). A priority with no
  // decided cases yet (all in-flight, or none at all) has no percentage to
  // report, so it returns null and the row shows "—" rather than "NaN%".
  const attainment = (r) => {
    const decided = r.met + r.missed;
    return decided ? Math.round((r.met / decided) * 100) : null;
  };
  const barColor = (pct) =>
    pct == null
      ? 'var(--v2-line)'
      : pct >= 95
        ? 'var(--v2-moss)'
        : pct >= 85
          ? 'var(--v2-clay)'
          : 'var(--v2-rust)';

  /** Net change over the window. Opened minus closed is the backlog's direction. */
  let net = $derived(totals.opened - totals.closed);

  /** The next-response endpoint reports hours; `duration` reads whole minutes. */
  const minutesOf = (hours) => (hours == null ? null : Math.round(hours * 60));

  // Best first, so the bars read down from the answer most tickets hope for.
  const RATINGS = [5, 4, 3, 2, 1];
  const ratingShare = (n) => (data.csat.count ? Math.round((n / data.csat.count) * 100) : 0);
</script>

<PageHeader title="Service analytics">
  {#snippet sub()}
    {#if canView}
      Last <span class="v2-num">{totals.window_days}</span> days
    {:else}
      Service health
    {/if}
  {/snippet}
</PageHeader>

<SectionTabs set="tickets" />

{#if !canView}
  <div class="v2-pad" style="padding-top:40px">
    <!-- Centred, not left-hugging: this is all a non-admin sees on this page,
         so a capped card pinned to the left leaves the rest of a wide screen
         empty. margin-inline centres the column. -->
    <div class="v2-card" style="padding:20px 22px;max-width:520px;margin-inline:auto">
      <strong>This dashboard is for administrators.</strong>
      <p>
        Opened and closed volume, response attainment, customer satisfaction and the queue breakdown
        are whole-organisation figures, so they are limited to admins. Your own tickets are on the <a
          href={resolve('/tickets')}>Tickets</a
        > tab.
      </p>
    </div>
  </div>
{:else}
  <div class="v2-pad" style="padding-top:16px;flex:none">
    <div class="v2-stats">
      <StatCard label="Opened" value={count(totals.opened)} tone="ink" />
      <StatCard label="Closed" value={count(totals.closed)} tone="moss" />
      <StatCard
        label="Backlog"
        value={count(totals.open_now)}
        tone={net > 0 ? 'clay' : 'slate'}
        detail={net > 0
          ? `Grew by ${net} over the window`
          : net < 0
            ? `Shrank by ${Math.abs(net)} over the window`
            : 'Level over the window'}
      />
      <StatCard
        label="Median resolution"
        value={`${totals.median_resolution_hours}h`}
        tone="slate"
        detail="Median, not mean. One three-week ticket should not move it"
      />
    </div>
  </div>

  <div class="v2-scroll">
    <div class="v2-pad" style="padding-bottom:32px">
      <!-- Volume -->
      <div class="v2-card" style="padding:16px 18px 14px;margin-bottom:18px">
        <div style="display:flex;align-items:baseline;gap:14px;margin-bottom:14px">
          <div class="v2-label">Opened and closed, per day</div>
          <span class="v2-sub" style="font-size:11.5px;margin-left:auto">
            <i class="v2-swatch v2-swatch-in"></i>opened
            <i class="v2-swatch" style="margin-left:10px"></i>closed
          </span>
        </div>
        <div class="v2-cols">
          {#each data.volume as d (d.date)}
            <div class="v2-col" title="{shortDate(d.date)}, {d.opened} opened, {d.closed} closed">
              <i class="in" style="height:{(d.opened / peak) * 100}%"></i>
              <i class="out" style="height:{(d.closed / peak) * 100}%"></i>
            </div>
          {/each}
        </div>
        <div class="v2-cols-axis">
          {#each data.volume as d, i (d.date)}
            <!-- Every other label. Fourteen dates at 10px overlap; seven do not. -->
            <span>{i % 2 === 0 ? shortDate(d.date) : ''}</span>
          {/each}
        </div>
      </div>

      <div class="v2-split" style="margin-bottom:18px">
        <!-- First response -->
        <div class="v2-card" style="padding:16px 18px">
          <div class="v2-label" style="margin-bottom:4px">First response, against target</div>
          <p class="v2-sub" style="font-size:11.5px;margin:0 0 14px">
            Each priority carries its own target from the escalation policy, so each one is scored
            against its own promise.
          </p>
          {#each data.firstResponse as r (r.priority)}
            {@const pct = attainment(r)}
            <div style="margin-bottom:14px">
              <div
                style="display:flex;align-items:baseline;gap:8px;font-size:12.5px;margin-bottom:5px"
              >
                <b style="font-weight:600">{r.priority}</b>
                <span class="v2-sub" style="font-size:11.5px">
                  target {duration(r.target_minutes)} · median {duration(r.median_minutes)}
                </span>
                <span
                  class="v2-num"
                  style="margin-left:auto;font-weight:650;color:{pct == null
                    ? 'var(--v2-slate)'
                    : barColor(pct)}"
                >
                  {pct == null ? '—' : `${pct}%`}
                </span>
              </div>
              <div class="v2-bar">
                <i style="width:{pct ?? 0}%;background:{barColor(pct)}"></i>
              </div>
              <div class="v2-bar-legend">
                <span><span class="v2-num">{r.met}</span> in time</span>
                <span>
                  {#if r.missed}
                    <span class="v2-num" style="color:var(--v2-rust)">{r.missed}</span> late
                  {:else}
                    none late
                  {/if}
                </span>
              </div>
            </div>
          {/each}
        </div>

        <!-- Mix -->
        <div class="v2-card" style="padding:16px 18px">
          <div class="v2-label" style="margin-bottom:4px">What the queue is made of</div>
          <p class="v2-sub" style="font-size:11.5px;margin:0 0 14px">
            Incidents and problems are work; questions are usually a gap in the knowledge base.
          </p>
          {#each data.byType as t (t.case_type)}
            {@const share = Math.round(
              (t.count / data.byType.reduce((a, x) => a + x.count, 0)) * 100
            )}
            <div style="margin-bottom:13px">
              <div style="display:flex;align-items:baseline;font-size:12.5px;margin-bottom:5px">
                <span>{t.case_type}</span>
                <span class="v2-sub v2-num" style="margin-left:auto;font-size:12px">
                  {t.count} · {share}%
                </span>
              </div>
              <div class="v2-bar"><i style="width:{share}%"></i></div>
            </div>
          {/each}

          {#if data.byType.find((t) => t.case_type === 'Question')}
            <p class="v2-sub" style="font-size:11.5px;margin:16px 0 0">
              <a href={resolve('/solutions')} style="color:inherit">
                {data.byType.find((t) => t.case_type === 'Question').count} questions in this window
              </a>. The ones that repeat belong in the knowledge base.
            </p>
          {/if}
        </div>
      </div>

      <div class="v2-split" style="margin-bottom:18px">
        <!-- Next response. Built like the first-response card above, scored the
             same way: per priority, against that priority's own target. -->
        <div class="v2-card" style="padding:16px 18px">
          <div class="v2-label" style="margin-bottom:4px">Next response, against target</div>
          <p class="v2-sub" style="font-size:11.5px;margin:0 0 14px">
            The wait after a customer writes back, once the first reply has gone. Counted around the
            clock, against each priority's <a
              href={resolve('/settings/escalation')}
              style="color:inherit">next reply target</a
            >. A reply still overdue on an open ticket counts as late.
          </p>
          {#each data.nextResponse as r (r.priority)}
            {@const pct = attainment(r)}
            <div style="margin-bottom:14px">
              <div
                style="display:flex;align-items:baseline;gap:8px;font-size:12.5px;margin-bottom:5px"
              >
                <b style="font-weight:600">{r.priority}</b>
                <span class="v2-sub" style="font-size:11.5px">
                  target {duration(minutesOf(r.target_hours))} · median {duration(
                    minutesOf(r.median_hours)
                  )}
                </span>
                <span
                  class="v2-num"
                  style="margin-left:auto;font-weight:650;color:{pct == null
                    ? 'var(--v2-slate)'
                    : barColor(pct)}"
                >
                  {pct == null ? '—' : `${pct}%`}
                </span>
              </div>
              <div class="v2-bar">
                <i style="width:{pct ?? 0}%;background:{barColor(pct)}"></i>
              </div>
              <div class="v2-bar-legend">
                <span><span class="v2-num">{r.met}</span> in time</span>
                <span>
                  {#if r.missed}
                    <span class="v2-num" style="color:var(--v2-rust)">{r.missed}</span> late
                  {:else}
                    none late
                  {/if}
                </span>
              </div>
            </div>
          {/each}
        </div>

        <!-- Satisfaction -->
        <div class="v2-card" style="padding:16px 18px">
          <div class="v2-label" style="margin-bottom:4px">Customer satisfaction</div>
          {#if data.csat.count === 0}
            <p class="v2-sub" style="font-size:11.5px;margin:0">
              No ratings came back in this window. A survey goes to the ticket's contact when it
              closes, if surveys are switched on in <a
                href={resolve('/settings/organization')}
                style="color:inherit">organisation settings</a
              >.
            </p>
          {:else}
            <p class="v2-sub" style="font-size:11.5px;margin:0 0 10px">
              What customers answered in the survey sent when their ticket closed, on a scale of 1
              to 5.
            </p>
            <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:14px">
              <span class="v2-stat-value" style="margin:0">
                {data.csat.average == null ? '—' : data.csat.average.toFixed(1)}
              </span>
              <span class="v2-sub" style="font-size:12px">
                average from <span class="v2-num">{count(data.csat.count)}</span>
                {data.csat.count === 1 ? 'rating' : 'ratings'}
              </span>
            </div>
            {#each RATINGS as rating (rating)}
              {@const n = data.csat.distribution[rating] ?? 0}
              <div style="margin-bottom:11px">
                <div style="display:flex;align-items:baseline;font-size:12.5px;margin-bottom:5px">
                  <span>{rating} out of 5</span>
                  <span class="v2-sub v2-num" style="margin-left:auto;font-size:12px">
                    {n} · {ratingShare(n)}%
                  </span>
                </div>
                <div class="v2-bar"><i style="width:{ratingShare(n)}%"></i></div>
              </div>
            {/each}
          {/if}
        </div>
      </div>

      <!-- Per agent -->
      <div class="v2-label" style="margin-bottom:10px">Who is carrying it</div>
      <div class="v2-table-wrap">
        <table class="v2-table">
          <thead>
            <tr>
              <th>Agent</th>
              <th class="v2-r">Open now</th>
              <th class="v2-r">Closed this week</th>
              <th class="v2-r">Median first response</th>
              <th class="v2-r">Missed target</th>
            </tr>
          </thead>
          <tbody>
            {#each data.byAgent as a (a.id ?? a.name)}
              <tr>
                <td>
                  <span style="display:flex;gap:8px;align-items:center">
                    {#if a.id}
                      <Avatar name={a.name} size={24} />
                    {:else}
                      <!-- Unassigned is not a person and does not get a face. -->
                      <span
                        style="width:24px;height:24px;border-radius:50%;border:1px dashed var(--v2-line);flex:none"
                      ></span>
                    {/if}
                    <span class="v2-table-primary">{a.name}</span>
                  </span>
                </td>
                <td class="v2-r v2-num">{a.open}</td>
                <td class="v2-r v2-num">{a.closed_this_week}</td>
                <td class="v2-r v2-num">{duration(a.median_first_response_minutes)}</td>
                <td
                  class="v2-r v2-num"
                  style={a.breached ? 'color:var(--v2-rust);font-weight:600' : ''}
                >
                  {a.breached || '—'}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>

      <!--
      The clock these figures are measured on. Without it "answered in 4h" is
      ambiguous: a ticket opened at 17:20 on Friday and answered at 09:10 on
      Monday is either fifteen hours late or fifty minutes early, and only the
      calendar says which.
    -->
      <div style="display:flex;gap:9px;align-items:flex-start;margin-top:16px">
        <Clock size={15} style="color:var(--v2-slate);flex:none;margin-top:2px" />
        <p class="v2-sub" style="font-size:12px;margin:0">
          {#if totals.business_hours_applied}
            These figures count elapsed time around the clock, evenings and weekends included. Each
            ticket's own SLA deadline is counted inside {totals.calendar_name}, so a reply on time
            there can show as late here.
            <a href={resolve('/settings/business-hours')} style="color:inherit"
              >Change the calendar</a
            >.
          {:else}
            Elapsed time is counted around the clock. No business-hours calendar is set, so evenings
            and weekends count against a target.
            <a href={resolve('/settings/business-hours')} style="color:inherit">Set up a calendar</a
            >.
          {/if}
        </p>
      </div>
    </div>
  </div>
{/if}
