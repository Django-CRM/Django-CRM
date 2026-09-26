<script>
  import { resolve } from '$app/paths';
  /**
   * Editing a deal.
   *
   * Three fields on this form are not what they appear to be, and all three
   * were found by reading `opportunity/models.py` rather than v1's edit page,
   * which offers Amount, Stage and Close date as three plain inputs with no
   * indication that any of them behaves specially.
   *
   * ── AMOUNT IS NOT ALWAYS YOURS TO SET ────────────────────────────────────
   * `Opportunity.recalculate_amount()` sets `amount = SUM(line_items.total)`
   * and flips `amount_source` to CALCULATED whenever any line items exist. A
   * number typed here is then discarded on the next recalculation. So when
   * the deal has line items the input is disabled and says why, instead of
   * accepting a value that will not survive.
   *
   * ── STAGE RESETS A CLOCK SOMEBODY ELSE IS WATCHING ───────────────────────
   * `Opportunity.save()` sets `stage_changed_at = now()` on any stage change,
   * and `stage_changed_at` is what `days_in_current_stage` and
   * `get_aging_status()` are computed from. The pipeline board colours every
   * card by that status. Moving a stage therefore turns a stalled deal green
   *, not because anything improved, but because the clock restarted. That is
   * a legitimate thing to do and an illegitimate thing to do by accident, so
   * the form shows the number that is about to be discarded.
   *
   * ── CLOSING REQUIRES MORE THAN PICKING "CLOSED" ──────────────────────────
   * A closed stage (kind won or lost, whatever the org named it) needs
   * `closed_on`, and a won one needs `amount`. Both are mirrored below so the
   * requirement appears when you pick the stage rather than after you submit.
   *
   * ── STAGES BELONG TO A PIPELINE ──────────────────────────────────────────
   * The stage list is the chosen pipeline's, from the API. Moving the deal to
   * another pipeline resets the stage to that pipeline's first open stage,
   * because the server refuses a pipeline change that does not name a stage
   * there, and a code from the old pipeline may mean nothing in the new one.
   *
   * `Opportunity.clean()` declares both, but DRF never calls `clean()`, so
   * until this module was wired the API accepted a won deal worth nothing with
   * no close date, and only the model's unit tests knew otherwise. They are
   * enforced in `OpportunityCreateSerializer.validate()` now. The rule below is
   * still only a hint; the serializer is what decides.
   *
   * NOT ON THIS FORM, because the server owns them: `amount_source`,
   * `stage_changed_at`, `closed_by`, `kanban_order`, `org`, `created_by`.
   * The amount input is `disabled` when line items own it, and a disabled
   * control submits nothing, which is exactly what PATCH needs, since absent
   * means "leave it alone".
   */
  import { tick, untrack } from 'svelte';
  import { enhance } from '$app/forms';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import { OPPORTUNITY_TYPE_LABEL, AGING_TONE, AGING_LABEL } from '$lib/v2/enums.js';
  import { money, longDate } from '$lib/v2/format.js';
  import { ChevronRight, TriangleAlert, Lock } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form: result } = $props();

  /* Read once, on purpose. `originalStage` has to stay the stage this form
     opened on. That is what "the clock is about to reset" is measured
     against, and a revalidation must not quietly redefine it. */
  const { deal, server, originalStage, originalPipeline, pipelines } = untrack(() => ({
    deal: data.deal,
    server: data.server,
    originalStage: data.deal.stage,
    originalPipeline: data.deal.pipeline,
    pipelines: /** @type {any[]} */ (data.pipelines)
  }));

  let form = $state(untrack(() => ({ ...data.form })));
  let touched = $state(/** @type {Record<string, boolean>} */ ({}));
  let submitted = $state(false);
  let saved = $state(false);

  const amountIsCalculated = server.amount_source === 'CALCULATED';

  let stages = $derived(
    pipelines.find((p) => p.id === form.pipeline)?.stages ?? /** @type {any[]} */ ([])
  );
  let chosen = $derived(stages.find((/** @type {any} */ s) => s.code === form.stage));
  let isClosed = $derived(chosen?.kind === 'won' || chosen?.kind === 'lost');
  let isClosedWon = $derived(chosen?.kind === 'won');
  let stageChanged = $derived(form.stage !== originalStage || form.pipeline !== originalPipeline);

  /**
   * A new pipeline starts the deal on its first open stage. Read from the
   * event, not from `form.pipeline`, so it does not depend on whether the
   * binding or this handler sees the change first.
   *
   * @param {string} pipelineId
   */
  function pipelineChanged(pipelineId) {
    const next = pipelines.find((p) => p.id === pipelineId)?.stages ?? [];
    form.stage = next.find((/** @type {any} */ s) => s.kind === 'open')?.code ?? '';
  }

  let errors = $derived.by(() => {
    /** @type {Record<string, string>} */
    const e = {};
    if (!form.name.trim()) e.name = 'Give the deal a name you would recognise in a list.';
    if (!form.account) e.account = 'A deal has to belong to an account.';

    /* Only validate what the form can actually send. When the amount is
       server-calculated the input is disabled, so a complaint about it would
       be a complaint about something nobody can fix here. */
    if (!amountIsCalculated) {
      const n = Number(form.amount);
      if (form.amount === '' || form.amount === null) {
        if (isClosedWon) e.amount = 'A won deal has to record what it was worth.';
      } else if (!Number.isFinite(n)) e.amount = 'Amount has to be a number.';
      else if (n < 0) e.amount = 'Amount cannot be negative.';
      else if (n === 0 && isClosedWon) e.amount = 'A won deal has to record what it was worth.';
    }

    if (isClosed && !form.closed_on)
      e.closed_on = `${chosen?.label ?? 'A closed stage'} needs the date it closed.`;

    const p = Number(form.probability);
    if (form.probability !== '' && (!Number.isFinite(p) || p < 0 || p > 100))
      e.probability = 'Probability is a percentage between 0 and 100.';

    return e;
  });

  let valid = $derived(Object.keys(errors).length === 0);
  const show = (field) => (touched[field] || submitted) && errors[field];

  /**
   * Stop the submit when this form's own checks fail, and send focus to the
   * first field that needs work. On a long form the error can be off screen.
   * `await tick()` matters: on the first submit the aria-invalid attributes do
   * not exist until Svelte flushes, so querying now would find nothing.
   *
   * These checks are a UX hint. The serializer is the rule, and its 400 is
   * what `result.error` below reports.
   *
   * @type {import('./$types').SubmitFunction}
   */
  const check = async ({ cancel }) => {
    submitted = true;
    saved = false;
    if (!valid) {
      cancel();
      await tick();
      /** @type {HTMLElement | null} */
      const first = document.querySelector('[aria-invalid="true"]');
      first?.focus();
      return;
    }
    return async ({ update, result: outcome }) => {
      // reset:false keeps what was typed when the server rejects it.
      await update({ reset: false });
      saved = outcome.type === 'success';
    };
  };
</script>

<PageHeader title="Edit {deal.name}" center>
  {#snippet crumb()}
    <a href={resolve('/pipeline')}>Pipeline</a>
    <ChevronRight size={12} />
    <a href={resolve(`/pipeline/${deal.id}`)}>{deal.name}</a>
  {/snippet}
  {#snippet sub()}
    {deal.account.name} · <span class="v2-num">{money(deal.amount, deal.currency)}</span> ·
    {deal.stage_label} for <span class="v2-num">{server.days_in_current_stage}</span> days
  {/snippet}
</PageHeader>

<div class="v2-scroll v2-pad" style="padding-top:18px">
  <form class="v2-form" method="POST" action="?/save" use:enhance={check} novalidate>
    {#if saved}
      <div class="v2-next" style="margin-bottom:18px" role="status">
        <div class="v2-next-body">
          <div class="v2-next-text">Saved.</div>
          <div class="v2-sub" style="margin-top:3px">
            “{deal.name}” has been updated.
          </div>
        </div>
        <a class="v2-btn" href={resolve(`/pipeline/${deal.id}`)}>Back to the deal</a>
      </div>
    {/if}

    {#if result?.error}
      <div
        class="v2-next"
        style="background:color-mix(in srgb, var(--v2-rust) 9%, transparent);border-color:color-mix(in srgb, var(--v2-rust) 28%, transparent);margin-bottom:18px"
        role="alert"
      >
        <TriangleAlert size={17} style="color:var(--v2-rust);flex:none" />
        <div class="v2-next-body">
          <div style="font-weight:600">The server refused this change</div>
          <div class="v2-sub" style="margin-top:2px">{result.error}</div>
        </div>
      </div>
    {/if}

    {#if submitted && !valid}
      <div
        class="v2-next"
        style="background:color-mix(in srgb, var(--v2-rust) 9%, transparent);border-color:color-mix(in srgb, var(--v2-rust) 28%, transparent);margin-bottom:18px"
        role="alert"
      >
        <TriangleAlert size={17} style="color:var(--v2-rust);flex:none" />
        <div class="v2-next-body">
          <div style="font-weight:600">
            {Object.keys(errors).length} field{Object.keys(errors).length === 1 ? '' : 's'} still need{Object.keys(
              errors
            ).length === 1
              ? 's'
              : ''} you
          </div>
          <div class="v2-sub" style="margin-top:2px">Nothing has been saved.</div>
        </div>
      </div>
    {/if}

    <div class="v2-field">
      <label for="f-name">Deal name</label>
      <input
        id="f-name"
        name="name"
        class="v2-input"
        bind:value={form.name}
        onblur={() => (touched.name = true)}
        aria-invalid={show('name') ? 'true' : undefined}
      />
      {#if show('name')}<p class="v2-error">{errors.name}</p>{/if}
    </div>

    <div class="pair">
      <div class="v2-field">
        <label for="f-account">Account</label>
        <select
          id="f-account"
          name="account"
          class="v2-input"
          bind:value={form.account}
          aria-invalid={show('account') ? 'true' : undefined}
        >
          {#each data.accounts as a (a.id)}
            <option value={a.id}>{a.name}</option>
          {/each}
        </select>
        {#if show('account')}<p class="v2-error">{errors.account}</p>{/if}
      </div>
      <div class="v2-field">
        <label for="f-type">Type</label>
        <select
          id="f-type"
          name="opportunity_type"
          class="v2-input"
          bind:value={form.opportunity_type}
        >
          {#each Object.entries(OPPORTUNITY_TYPE_LABEL) as [key, label] (key)}
            <option value={key}>{label}</option>
          {/each}
        </select>
      </div>
    </div>

    {#if pipelines.length > 1}
      <div class="v2-field">
        <label for="f-pipeline">Pipeline</label>
        <select
          id="f-pipeline"
          name="pipeline"
          class="v2-input"
          bind:value={form.pipeline}
          onchange={(e) => pipelineChanged(e.currentTarget.value)}
        >
          {#each pipelines as p (p.id)}
            <option value={p.id}>{p.name}</option>
          {/each}
        </select>
      </div>
    {/if}

    <div class="v2-field">
      <label for="f-stage">Stage</label>
      <select
        id="f-stage"
        name="stage"
        class="v2-input"
        bind:value={form.stage}
        aria-describedby={stageChanged ? 'stage-effect' : undefined}
      >
        {#each stages as s (s.code)}
          <option value={s.code}>{s.label}</option>
        {/each}
      </select>

      <!--
        The cost of the change, shown before it is made. `days_in_current_stage`
        and the aging pill on the board are both derived from stage_changed_at,
        which save() resets, so this is the number that is about to disappear.
      -->
      {#if stageChanged}
        <div class="consequence" style="--edge:var(--v2-clay)" id="stage-effect">
          <div style="font-weight:600">
            {deal.stage_label} → {chosen?.label ?? form.stage}
          </div>
          <p>
            The stage clock restarts. This deal currently reads
            <Pill tone={AGING_TONE[server.aging_status]} dot>
              {AGING_LABEL[server.aging_status]} · {server.days_in_current_stage} days
            </Pill>
            on the board; after saving it reads 0 days and On&nbsp;pace, because aging is measured from
            the last stage change and not from the last time anyone did anything.
          </p>
        </div>
      {/if}
    </div>

    <div class="pair">
      <div class="v2-field">
        <label for="f-amount">
          Amount
          {#if amountIsCalculated}<span class="locked"><Lock size={10} />From line items</span>{/if}
        </label>
        <input
          id="f-amount"
          name="amount"
          class="v2-input v2-num"
          type="text"
          inputmode="decimal"
          disabled={amountIsCalculated}
          value={amountIsCalculated ? server.line_item_total : form.amount}
          oninput={(e) => (form.amount = e.currentTarget.value)}
          onblur={() => (touched.amount = true)}
          aria-invalid={show('amount') ? 'true' : undefined}
          aria-describedby={amountIsCalculated ? 'h-amount' : undefined}
        />
        {#if show('amount')}
          <p class="v2-error">{errors.amount}</p>
        {:else if amountIsCalculated}
          <p class="v2-hint" id="h-amount">
            <a href={resolve(`/pipeline/${deal.id}`)}>{server.line_item_count} line items</a> add up
            to
            <span class="v2-num">{money(server.line_item_total, deal.currency)}</span>. The server
            refuses a different figure on a deal with line items, so this cannot be typed over. Edit
            the line items instead.
          </p>
        {:else}
          <p class="v2-hint">
            Typed by hand. Adding line items to this deal would take the field over.
          </p>
        {/if}
      </div>

      <div class="v2-field">
        <label for="f-prob">Probability</label>
        <input
          id="f-prob"
          name="probability"
          class="v2-input v2-num"
          type="text"
          inputmode="numeric"
          bind:value={form.probability}
          onblur={() => (touched.probability = true)}
          aria-invalid={show('probability') ? 'true' : undefined}
        />
        {#if show('probability')}<p class="v2-error">{errors.probability}</p>{/if}
      </div>
    </div>

    <div class="pair">
      <div class="v2-field">
        <label for="f-closed">
          {isClosed ? 'Closed on' : 'Expected close'}
          {#if isClosed}<span class="req">required</span>{/if}
        </label>
        <input
          id="f-closed"
          name="closed_on"
          class="v2-input"
          type="date"
          bind:value={form.closed_on}
          onblur={() => (touched.closed_on = true)}
          aria-invalid={show('closed_on') ? 'true' : undefined}
        />
        {#if show('closed_on')}
          <p class="v2-error">{errors.closed_on}</p>
        {:else if isClosed}
          <p class="v2-hint">The date it actually closed, not the date you are recording it.</p>
        {/if}
      </div>
      <div class="v2-field">
        <label for="f-owner">Owner</label>
        <!-- What the select was rendered with. The action compares against it
             so an untouched owner is not sent at all; `assigned_to` is a
             many-to-many and this select is single, so sending it always would
             cut a two-person deal down to one on every save. -->
        <input type="hidden" name="assigned_to_original" value={data.form.assigned_to} />
        <select id="f-owner" name="assigned_to" class="v2-input" bind:value={form.assigned_to}>
          <option value="">Nobody</option>
          {#each data.owners as o (o.id)}
            <!-- The value is the Profile id. The mock used the display name,
                 which looks identical on screen and cannot be saved. -->
            <option value={o.id}>{o.name}</option>
          {/each}
        </select>
      </div>
    </div>

    <div class="v2-field">
      <label for="f-source">Source</label>
      <input id="f-source" name="lead_source" class="v2-input" bind:value={form.lead_source} />
    </div>

    <div class="v2-field">
      <label for="f-notes">Notes</label>
      <textarea
        id="f-notes"
        name="description"
        class="v2-input"
        rows="4"
        bind:value={form.description}></textarea>
    </div>

    <div class="actions">
      <button class="v2-btn v2-btn-primary" type="submit">Save changes</button>
      <a class="v2-btn" href={resolve(`/pipeline/${deal.id}`)}>Cancel</a>
      <span class="v2-sub" style="margin-left:auto;font-size:12px">
        Last stage change {longDate(server.stage_changed_at)}
      </span>
    </div>
  </form>
</div>

<style>
  .pair {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
  }
  .req,
  .locked {
    margin-left: 6px;
    font-size: 10px;
    letter-spacing: 0.09em;
    text-transform: uppercase;
    color: var(--v2-clay);
  }
  .locked {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    color: var(--v2-slate);
  }
  .consequence {
    border-left: 2px solid var(--edge);
    padding: 2px 0 2px 12px;
    margin-top: 8px;
  }
  .consequence p {
    margin: 4px 0 0;
    font-size: 13px;
    color: var(--v2-slate);
    max-width: 62ch;
    line-height: 1.55;
  }
  .actions {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-top: 22px;
  }
  @media (max-width: 720px) {
    .pair {
      grid-template-columns: 1fr;
    }
    .actions span {
      display: none;
    }
  }
</style>
