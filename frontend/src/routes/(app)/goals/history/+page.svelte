<script>
  /**
   * How did we do, one finished period at a time.
   *
   * The goals list answers "how are we doing"; nothing answered this. A closed
   * period cannot move any more, so the interesting number is not pace but
   * whether the number was made, and by how many people.
   *
   * Attainment is counted per goal, not on the pooled total: three reps, two of
   * whom missed while the third doubled up, is not the same story as "the team
   * made its number", and only the per-goal count tells them apart. Both are on
   * screen for that reason.
   *
   * Every figure here is the API's own rollup. Nothing is re-aggregated in the
   * browser.
   */
  import { resolve } from '$app/paths';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import EmptyState from '$lib/v2/components/EmptyState.svelte';
  import { money, count, shortDate } from '$lib/v2/format.js';
  import { GOAL_TYPE_LABEL, PERIOD_TYPE_LABEL } from '$lib/v2/enums.js';
  import { History } from '@lucide/svelte';

  /** @type {{ data: any }} */
  let { data } = $props();

  /**
   * Revenue is money, deals and activities are counts. A period row carries its
   * own `goal_type` for the same reason: the API groups history by period *and*
   * type, because pooling a currency target with a deals target produced a
   * single meaningless number, and formatting one as the other would put a
   * currency symbol in front of a count of deals.
   */
  const unit = (goalType, n) => (goalType === 'REVENUE' ? money(n, data.org.currency) : count(n));

  const value = (g, n) => unit(g.goal_type, n);

  const met = (g) => g.target_value > 0 && g.progress_value >= g.target_value;

  const tone = (percent) => (percent >= 100 ? 'moss' : percent >= 80 ? 'clay' : 'rust');

  const barColor = (percent) =>
    percent >= 100 ? 'var(--v2-moss)' : percent >= 80 ? 'var(--v2-clay)' : 'var(--v2-rust)';

  /** The owner of a goal, in the words the list page uses. */
  const owner = (g) =>
    g.assigned_to ? g.assigned_to.name : g.team ? `${g.team.name} (team)` : 'Whole org';
</script>

<PageHeader title="Goal history">
  {#snippet crumb()}<a href={resolve('/goals')}>Goals</a> ›{/snippet}
  {#snippet sub()}
    Finished periods, newest first. A closed period is settled, so these are results rather than
    pace.
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:16px;padding-bottom:32px">
    {#if data.history.length === 0}
      <!-- Says which set is empty. The endpoint narrows a member to their own
           goals and their teams', so "nothing has finished yet" would be a
           claim about the org that a member cannot actually see. -->
      <EmptyState
        title="No finished periods yet"
        body="Once a goal's period ends it moves here with what it attained. Goals still running are on the goals page."
      >
        {#snippet icon()}<History size={21} />{/snippet}
        {#snippet actions()}
          <a class="v2-btn" href={resolve('/goals')}>Back to goals</a>
        {/snippet}
      </EmptyState>
    {:else}
      <div class="periods">
        {#each data.history as period (period.period_start + period.period_end + period.goal_type)}
          <section class="v2-card" style="padding:15px 17px">
            <header>
              <div style="flex:1;min-width:0">
                <div style="font-weight:600;font-size:13.5px">
                  {shortDate(period.period_start)} - {shortDate(period.period_end)}
                </div>
                <div class="v2-sub" style="font-size:11.5px;margin-top:2px">
                  {PERIOD_TYPE_LABEL[period.period_type]} · {GOAL_TYPE_LABEL[period.goal_type]} ·
                  <span class="v2-num">{period.attained_count}</span>
                  of <span class="v2-num">{period.goals_count}</span>
                  {period.goals_count === 1 ? 'goal' : 'goals'} met
                </div>
              </div>
              <Pill tone={tone(period.percent)}>{period.percent}% of target</Pill>
            </header>

            <div class="v2-bar" style="margin-top:11px">
              <!-- `percent` is uncapped so an over-attaining period reports
                   what it did, but the fill is a bar and a bar cannot be more
                   than full. The number above it carries the overshoot. -->
              <i
                style="width:{Math.min(period.percent, 100)}%;background:{barColor(period.percent)}"
              ></i>
            </div>
            <div class="v2-bar-legend">
              <span class="v2-num">{unit(period.goal_type, period.achieved)}</span>
              <span>of <span class="v2-num">{unit(period.goal_type, period.target)}</span></span>
            </div>

            <ul class="goals">
              {#each period.goals as g (g.id)}
                <li>
                  <span class="name">{g.name}</span>
                  <span class="v2-sub who">{owner(g)} · {GOAL_TYPE_LABEL[g.goal_type]}</span>
                  <span class="v2-num figures">
                    {value(g, g.progress_value)} / {value(g, g.target_value)}
                  </span>
                  <Pill tone={met(g) ? 'moss' : 'slate'}>{met(g) ? 'Met' : 'Missed'}</Pill>
                </li>
              {/each}
            </ul>
          </section>
        {/each}
      </div>

      <p class="v2-sub" style="font-size:11.5px;margin-top:14px">
        The twelve most recent periods you can see. Attainment is recomputed from the closed-won
        deals of each period, so it matches what the goals page showed while the period was running.
      </p>
    {/if}
  </div>
</div>

<style>
  .periods {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  header {
    display: flex;
    gap: 10px;
    align-items: flex-start;
  }

  .goals {
    list-style: none;
    margin: 12px 0 0;
    padding: 12px 0 0;
    border-top: 1px solid var(--v2-line-soft);
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  /*
    Phone first: each goal stacks into a labelled block at 390px, so nothing is
    pushed off the right edge, and becomes one row from the 768px breakpoint
    v2.css already uses.
  */
  .goals li {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 2px 10px;
    align-items: center;
    font-size: 12.5px;
  }

  .name {
    font-weight: 550;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .who {
    font-size: 11.5px;
    grid-column: 1;
  }

  .figures {
    grid-column: 1;
    font-size: 12px;
  }

  @media (min-width: 768px) {
    .goals li {
      grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr) auto auto;
    }

    .who,
    .figures {
      grid-column: auto;
    }

    .figures {
      text-align: right;
    }
  }
</style>
