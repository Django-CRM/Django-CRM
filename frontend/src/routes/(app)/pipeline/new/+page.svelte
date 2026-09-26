<script>
  import { resolve } from '$app/paths';
  import { untrack, tick } from 'svelte';
  import { enhance } from '$app/forms';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import { OPPORTUNITY_TYPE_LABEL } from '$lib/v2/enums.js';
  import { money } from '$lib/v2/format.js';
  import { ChevronDown, ChevronRight, TriangleAlert } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form: result } = $props();

  /**
   * v1's create form asked for 28 fields before it would accept a deal, so
   * people typed placeholders into the ones they did not know yet and the
   * data was wrong from the first save. This asks for the five that a deal
   * genuinely cannot exist without, and puts the rest behind a disclosure
   * that is closed by default.
   *
   * VALIDATION IS A UX HINT HERE, NOT A RULE.
   * Everything below only decides what this page shows. The serializer has to
   * enforce the same constraints server-side: required fields, the amount
   * being positive, the account belonging to this org, because curl, the
   * mobile client and a stale build all reach the API without passing
   * through this file. See CLAUDE.md, "API Validation & Authorization".
   *
   * Note what is NOT on this form: org, created_by, assigned-by. Those are
   * server-derived. If they ever appear in a request body, the serializer
   * must reject them rather than trust them.
   */
  // Seeded from the load once, on purpose: a half-typed form must not be
  // wiped if the page data revalidates underneath it. untrack() says that
  // out loud rather than leaving it as an accident.
  let form = $state(
    untrack(() => ({
      name: '',
      account: '',
      amount: '',
      closed_on: '',
      pipeline: data.defaults.pipeline,
      stage: data.defaults.stage,
      // behind the disclosure
      opportunity_type: 'NEW_BUSINESS',
      probability: '',
      lead_source: '',
      assigned_to: data.defaults.assigned_to,
      description: '',
      // A rejected submit comes back with what was typed. Retyping eight
      // fields because the ninth collided is how people learn to distrust a
      // create page.
      ...(untrack(() => result?.values) ?? {})
    }))
  );

  /** @type {any[]} */
  const pipelines = untrack(() => data.pipelines);
  /* A new deal cannot start closed, so only the chosen pipeline's open
     stages are offered. */
  let openStages = $derived(
    (pipelines.find((p) => p.id === form.pipeline)?.stages ?? []).filter(
      (/** @type {any} */ s) => s.kind === 'open'
    )
  );

  /**
   * Picking another pipeline starts the deal on that pipeline's first open
   * stage; a code from the previous pipeline may not exist in this one.
   *
   * @param {string} pipelineId
   */
  function pipelineChanged(pipelineId) {
    const next = pipelines.find((p) => p.id === pipelineId)?.stages ?? [];
    form.stage = next.find((/** @type {any} */ s) => s.kind === 'open')?.code ?? '';
  }

  let more = $state(false);
  let touched = $state(/** @type {Record<string, boolean>} */ ({}));
  let submitted = $state(false);

  const REQUIRED = ['name', 'account', 'amount', 'closed_on'];

  let errors = $derived.by(() => {
    /** @type {Record<string, string>} */
    const e = {};
    if (!form.name.trim()) e.name = 'Give the deal a name you would recognise in a list.';
    if (!form.account) e.account = 'Pick the account this deal belongs to.';

    const amount = Number(form.amount);
    if (form.amount === '') e.amount = 'How much is it worth?';
    else if (!Number.isFinite(amount) || amount <= 0)
      e.amount = 'Amount has to be a number greater than zero.';

    if (!form.closed_on) e.closed_on = 'When do you expect this to close?';
    else if (new Date(form.closed_on).getTime() < Date.now() - 86400000)
      e.closed_on = 'That date has passed. Pick the date you now expect.';

    const p = Number(form.probability);
    if (form.probability !== '' && (!Number.isFinite(p) || p < 0 || p > 100))
      e.probability = 'Probability is a percentage between 0 and 100.';

    return e;
  });

  let valid = $derived(Object.keys(errors).length === 0);
  const show = (field) => (touched[field] || submitted) && errors[field];

  /**
   * On success the action redirects to the new deal, so there is no success
   * state to render here. The next thing anyone does after creating a deal is
   * look at it.
   *
   * @type {import('./$types').SubmitFunction}
   */
  const check = async ({ cancel }) => {
    submitted = true;
    if (!valid) {
      // Send focus to the first field that needs work rather than only
      // colouring it. On a long form the error can be off screen.
      // await tick() matters: on the first submit the aria-invalid attributes
      // do not exist until Svelte flushes, so querying now finds nothing.
      cancel();
      await tick();
      /** @type {HTMLElement | null} */
      const first = document.querySelector('[aria-invalid="true"]');
      first?.focus();
    }
  };
</script>

<PageHeader title="New deal" center>
  {#snippet crumb()}
    <a href={resolve('/pipeline')}>Pipeline</a> ›
  {/snippet}
  {#snippet sub()}
    Five fields to start. Everything else can wait until you know it.
  {/snippet}
</PageHeader>

<div class="v2-scroll v2-pad" style="padding-top:18px">
  <form class="v2-form" method="POST" action="?/create" use:enhance={check} novalidate>
    {#if result?.error}
      <div
        class="v2-next"
        style="background:color-mix(in srgb, var(--v2-rust) 9%, transparent);border-color:color-mix(in srgb, var(--v2-rust) 28%, transparent);margin-bottom:18px"
        role="alert"
      >
        <TriangleAlert size={17} style="color:var(--v2-rust);flex:none" />
        <div class="v2-next-body">
          <div style="font-weight:600">The server refused this deal</div>
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
          <div class="v2-sub" style="margin-top:2px">
            They are marked below. Nothing has been saved.
          </div>
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
        aria-describedby={show('name') ? 'e-name' : 'h-name'}
        placeholder="Platform renewal"
      />
      {#if show('name')}
        <p class="v2-error" id="e-name">{errors.name}</p>
      {:else}
        <p class="v2-hint" id="h-name">
          What you would say out loud, “40 seats plus onboarding”, not “Opportunity 118”.
        </p>
      {/if}
    </div>

    <div class="v2-field">
      <label for="f-account">Account</label>
      <select
        id="f-account"
        name="account"
        class="v2-input"
        bind:value={form.account}
        onblur={() => (touched.account = true)}
        aria-invalid={show('account') ? 'true' : undefined}
        aria-describedby={show('account') ? 'e-account' : undefined}
      >
        <option value="">Choose an account…</option>
        {#each data.accounts as a (a.id)}
          <option value={a.id}>{a.name}</option>
        {/each}
      </select>
      {#if show('account')}<p class="v2-error" id="e-account">{errors.account}</p>{/if}
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
      <div class="v2-field">
        <label for="f-amount">Amount</label>
        <input
          id="f-amount"
          name="amount"
          class="v2-input v2-num"
          type="text"
          inputmode="decimal"
          bind:value={form.amount}
          onblur={() => (touched.amount = true)}
          aria-invalid={show('amount') ? 'true' : undefined}
          aria-describedby={show('amount') ? 'e-amount' : 'h-amount'}
          placeholder="42000"
        />
        {#if show('amount')}
          <p class="v2-error" id="e-amount">{errors.amount}</p>
        {:else}
          <p class="v2-hint" id="h-amount">
            {Number(form.amount) > 0
              ? money(Number(form.amount), data.org.currency)
              : data.org.currency}
          </p>
        {/if}
      </div>

      <div class="v2-field">
        <label for="f-close">Expected close</label>
        <input
          id="f-close"
          name="closed_on"
          class="v2-input"
          type="date"
          bind:value={form.closed_on}
          onblur={() => (touched.closed_on = true)}
          aria-invalid={show('closed_on') ? 'true' : undefined}
          aria-describedby={show('closed_on') ? 'e-close' : undefined}
        />
        {#if show('closed_on')}<p class="v2-error" id="e-close">{errors.closed_on}</p>{/if}
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
      <select id="f-stage" name="stage" class="v2-input" bind:value={form.stage}>
        {#each openStages as s (s.code)}
          <option value={s.code}>{s.label}</option>
        {/each}
      </select>
      <p class="v2-hint">
        A new deal cannot start closed. Won and lost are things you do to a deal that exists, so
        they are not offered here.
      </p>
    </div>

    <!--
      The disclosure below removes its fields from the DOM when closed, and a
      field that is not in the DOM submits nothing. That is right for every
      field in there except the owner: the hint under it promises "Defaults to
      you", and without this the common path, never opening the disclosure,
      would create an unassigned deal. So the default rides along in a hidden
      input whenever the select itself is not mounted.
    -->
    {#if !more}
      <input type="hidden" name="assigned_to" value={form.assigned_to} />
    {/if}

    <!-- Closed by default. Opening it is a choice; filling it is never a
         condition of saving. -->
    <div class="v2-disclosure">
      <button
        class="v2-btn v2-btn-quiet"
        type="button"
        style="padding-left:0"
        onclick={() => (more = !more)}
        aria-expanded={more}
        aria-controls="more-fields"
      >
        {#if more}<ChevronDown />{:else}<ChevronRight />{/if}
        More fields
        <span class="v2-sub" style="font-size:12px">. Type, probability, source, owner, notes</span>
      </button>

      {#if more}
        <div id="more-fields" style="margin-top:14px">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
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
                placeholder="50"
              />
              {#if show('probability')}<p class="v2-error">{errors.probability}</p>{/if}
            </div>
          </div>

          <div class="v2-field">
            <label for="f-owner">Owner</label>
            <select id="f-owner" name="assigned_to" class="v2-input" bind:value={form.assigned_to}>
              {#each data.owners as o (o.id)}
                <option value={o.id}>{o.name}</option>
              {/each}
            </select>
            <p class="v2-hint">
              Defaults to you. Who created the deal is recorded separately and is not editable.
            </p>
          </div>

          <div class="v2-field">
            <label for="f-source">Source</label>
            <input
              id="f-source"
              name="lead_source"
              class="v2-input"
              bind:value={form.lead_source}
              placeholder="Existing customer"
            />
          </div>

          <div class="v2-field">
            <label for="f-notes">Notes</label>
            <textarea
              id="f-notes"
              name="description"
              class="v2-input"
              rows="3"
              bind:value={form.description}></textarea>
          </div>
        </div>
      {/if}
    </div>

    <div style="display:flex;gap:8px;align-items:center;margin-top:22px">
      <button class="v2-btn v2-btn-primary" type="submit">Create deal</button>
      <a class="v2-btn" href={resolve('/pipeline')}>Cancel</a>
      <span class="v2-sub" style="margin-left:auto;font-size:12px">
        <span class="v2-num">{REQUIRED.filter((f) => !errors[f]).length}</span>
        of <span class="v2-num">{REQUIRED.length}</span> required fields done
      </span>
    </div>
  </form>
</div>
