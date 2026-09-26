<script>
  import { resolve } from '$app/paths';
  /**
   * Editing a lead.
   *
   * The create form's job is to ask for as little as possible. An edit form's
   * job is different: the record already exists, somebody is changing one
   * thing about it, and the risk is not a slow form. It is a change whose
   * consequence is invisible. Two of those live on this record.
   *
   * ── ONE: STATUS IS NOT JUST A LABEL ──────────────────────────────────────
   * Setting status to "converted" is checked twice server-side;
   * `Lead.clean()` raises "Email is required to convert lead", and
   * `LeadCreateSerializer.__init__` flips `email` to required when the
   * incoming status is "converted". So the email field's requiredness is a
   * function of the status select, and this form mirrors that rather than
   * letting somebody discover it in a 400.
   *
   * ── TWO: CONVERSION IS A ONE-WAY DOOR ────────────────────────────────────
   * Conversion (`leads/services.py::convert_lead_to_account`) creates an
   * Account, a Contact and an Opportunity, migrates comments and attachments,
   * and then sets status to "converted". Nothing undoes any of that.
   *
   * `leads/workflow.py` declared this rule and was imported by nothing, so
   * for a long time the API accepted both a repeat conversion (which built a
   * *second* Opportunity, because `LeadDetailView.put` reruns the service
   * whenever the incoming status is "converted") and a move back out of it
   * (which orphaned everything conversion had made). Both are now refused by
   * `LeadCreateSerializer.validate_status`.
   *
   * The form still shows the consequence before you pick the status, because
   * being told "no" after pressing save is a worse way to learn a rule than
   * reading it beside the control. "closed" is deliberately NOT in the same
   * category. Reopening a closed lead is ordinary work and creates nothing.
   *
   * ── WHAT THIS FORM SENDS ─────────────────────────────────────────────────
   * `EDITABLE_FIELDS` plus the owner, over PATCH. Not `org`, not `created_by`,
   * not tags, contacts or teams, and nothing conversion produced. Those are
   * server-derived, and the action picks fields out by name so a hand-appended
   * key in the POST body is not forwarded.
   *
   * PATCH rather than PUT is deliberate: `LeadDetailView.put` calls `.clear()`
   * on assigned_to, tags, contacts and teams unconditionally, so saving a
   * phone-number correction through it would strip every tag on the lead.
   * `patch` guards each with `if "<field>" in params`.
   *
   * The checks below duplicate the API's rules so somebody learns about them
   * beside the control rather than in a 400. They are a courtesy, not the
   * enforcement. The serializer runs again on everything that arrives.
   */
  import { tick, untrack } from 'svelte';
  import { enhance } from '$app/forms';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import {
    LEAD_STATUSES,
    LEAD_STATUS_LABEL,
    LEAD_STATUS_TONE,
    LEAD_SOURCES,
    LEAD_SOURCE_LABEL,
    LEAD_IRREVERSIBLE_STATUSES,
    INDUSTRIES,
    industryLabel
  } from '$lib/v2/enums.js';
  import { money, longDate } from '$lib/v2/format.js';
  import { ChevronRight, TriangleAlert, Lock } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form: result } = $props();

  /* Read once, on purpose. `originalStatus` in particular has to be the status
     the record had when this form opened, if it tracked `data` a revalidation
     mid-edit would redefine what counts as "changed" underneath the person. */
  const { lead, server, originalStatus } = untrack(() => ({
    lead: data.lead,
    server: data.server,
    originalStatus: data.lead.status
  }));

  let form = $state(untrack(() => ({ ...data.form })));
  /* Copied per row, not shared: each entry's `value` is bound to an input, so
     the objects have to be the page's own rather than the load result's. */
  let customFields = $state(
    untrack(() => (data.customFields ?? []).map((/** @type {any} */ f) => ({ ...f })))
  );
  let touched = $state(/** @type {Record<string, boolean>} */ ({}));
  let submitted = $state(false);
  let saving = $state(false);

  let saved = $derived(result?.saved === true);
  /** The API's own refusal, when it disagrees with the checks below. */
  let serverMessage = $derived(result?.message ?? '');

  const displayName = `${lead.first_name ?? ''} ${lead.last_name ?? ''}`.trim() || 'Lead';

  /* Mirrors `LeadCreateSerializer.validate_status`: converted is the one
     status you can neither re-enter nor leave. Everything else can change. */
  const isConverted = LEAD_IRREVERSIBLE_STATUSES.includes(originalStatus);

  /* Mirrors LeadCreateSerializer.__init__. The status decides this. */
  let emailRequired = $derived(form.status === 'converted');

  let enteringConverted = $derived(!isConverted && form.status === 'converted');
  let statusChanged = $derived(form.status !== originalStatus);

  let errors = $derived.by(() => {
    /** @type {Record<string, string>} */
    const e = {};
    if (!form.first_name.trim() && !form.last_name.trim())
      e.last_name = 'A lead needs a name to be findable. First or last will do.';

    const email = form.email.trim().toLowerCase();
    if (emailRequired && !email)
      e.email = 'Converting creates a Contact, and a contact without an email cannot be reached.';
    else if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))
      e.email = 'That does not look like an email address.';
    else if (email && server.taken_emails.includes(email))
      e.email = 'Another lead in this org already uses that address.';

    /* Mirrors `flexible_phone_validator` in common/validators.py exactly.
       The check here used to be a length test, which passes plenty of values
       the API then refuses, extensions being the common one. A whole-form
       rejection reading "phone: Enter a valid phone number" after save is a
       worse way to learn that than a message beside the field. */
    if (form.phone && !/^[\d\s\-()+.]{7,25}$/.test(form.phone)) {
      e.phone =
        form.phone.length > 25
          ? `Phone is stored in 25 characters; this is ${form.phone.length}.`
          : 'Digits and separators only. The API rejects letters, so "x123" extensions have to go in the notes.';
    }

    const amount = form.opportunity_amount;
    if (amount !== '' && amount !== null) {
      const n = Number(amount);
      if (!Number.isFinite(n)) e.opportunity_amount = 'Estimated value has to be a number.';
      else if (n < 0) e.opportunity_amount = 'Estimated value cannot be negative.';
    }

    /* Mirrors the required-field loop at the end of
       `common.custom_fields.validate_payload`: a required definition errors
       when neither the payload nor the stored record has a value. A plain
       `required` attribute would do nothing here. The form is `novalidate`,
       so the page owns its own checks. Without this the API answers 400 with
       "is required" and the message has no field to sit beside. A checkbox is
       exempt: false is a value, so a required one can never be unsatisfied. */
    for (const f of customFields) {
      if (!f.is_required || f.field_type === 'checkbox') continue;
      if (String(f.value ?? '').trim() === '') e[`cf_${f.key}`] = `${f.label} is required.`;
    }

    return e;
  });

  let valid = $derived(Object.keys(errors).length === 0);
  const show = (field) => (touched[field] || submitted) && errors[field];

  /**
   * Progressive enhancement over a real POST. Without JavaScript the form
   * still submits and the action still validates; this only spares a full
   * page load and keeps focus on the first field that needs attention.
   *
   * @type {import('@sveltejs/kit').SubmitFunction}
   */
  const submit = async ({ cancel }) => {
    submitted = true;
    if (!valid) {
      cancel();
      await tick();
      const first = /** @type {HTMLElement | null} */ (
        document.querySelector('[aria-invalid="true"]')
      );
      first?.focus();
      return;
    }
    saving = true;
    return async ({ update }) => {
      await update({ reset: false });
      saving = false;
    };
  };
</script>

<PageHeader title="Edit {displayName}" center>
  {#snippet crumb()}
    <a href={resolve('/leads')}>Leads</a>
    <ChevronRight size={12} />
    <a href={resolve(`/leads/${lead.id}`)}>{displayName}</a>
  {/snippet}
  {#snippet sub()}
    Currently <Pill tone={LEAD_STATUS_TONE[originalStatus]}
      >{LEAD_STATUS_LABEL[originalStatus]}</Pill
    >
    · created {longDate(lead.created_at)}
  {/snippet}
</PageHeader>

<div class="v2-scroll v2-pad" style="padding-top:18px">
  <form class="v2-form" method="POST" action="?/save" use:enhance={submit} novalidate>
    {#if saved}
      <div class="v2-next" style="margin-bottom:18px" role="status">
        <div class="v2-next-body">
          {#if result?.account_id}
            <div class="v2-next-text">Converted.</div>
            <div class="v2-sub" style="margin-top:3px">
              Every edit on this form was saved to the lead first, and the lead was then converted
              from it. Open what the conversion made below.
            </div>
          {:else}
            <div class="v2-next-text">Saved.</div>
            <div class="v2-sub" style="margin-top:3px">
              Changes to “{displayName}” are on the record.
            </div>
          {/if}
          {#if result?.account_id}
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
              <a class="v2-btn" href={resolve(`/accounts/${result.account_id}`)}>View account</a>
              {#if result.contact_id}
                <a class="v2-btn" href={resolve(`/contacts/${result.contact_id}`)}>View contact</a>
              {/if}
              {#if result.opportunity_id}
                <a class="v2-btn" href={resolve(`/pipeline/${result.opportunity_id}`)}>View deal</a>
              {/if}
            </div>
          {/if}
        </div>
        <a class="v2-btn" href={resolve(`/leads/${lead.id}`)}>Back to the lead</a>
      </div>
    {/if}

    {#if serverMessage}
      <div
        class="v2-next"
        style="background:color-mix(in srgb, var(--v2-rust) 9%, transparent);border-color:color-mix(in srgb, var(--v2-rust) 28%, transparent);margin-bottom:18px"
        role="alert"
      >
        <TriangleAlert size={17} style="color:var(--v2-rust);flex:none" />
        <div class="v2-next-body">
          <div style="font-weight:600">The server refused this change</div>
          <div class="v2-sub" style="margin-top:2px">{serverMessage}</div>
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

    <div class="v2-section-label v2-label">Person</div>

    <div class="pair">
      <div class="v2-field">
        <label for="f-first">First name</label>
        <input id="f-first" name="first_name" class="v2-input" bind:value={form.first_name} />
      </div>
      <div class="v2-field">
        <label for="f-last">Last name</label>
        <input
          id="f-last"
          name="last_name"
          class="v2-input"
          bind:value={form.last_name}
          onblur={() => (touched.last_name = true)}
          aria-invalid={show('last_name') ? 'true' : undefined}
        />
        {#if show('last_name')}<p class="v2-error">{errors.last_name}</p>{/if}
      </div>
    </div>

    <div class="v2-field">
      <label for="f-email">
        Email
        {#if emailRequired}<span class="req">required to convert</span>{/if}
      </label>
      <input
        id="f-email"
        name="email"
        class="v2-input"
        type="email"
        bind:value={form.email}
        onblur={() => (touched.email = true)}
        aria-invalid={show('email') ? 'true' : undefined}
        aria-describedby={show('email') ? 'e-email' : 'h-email'}
      />
      {#if show('email')}
        <p class="v2-error" id="e-email">{errors.email}</p>
      {:else}
        <p class="v2-hint" id="h-email">
          One lead per address per org, ignoring case. The database enforces it, so a duplicate
          comes back as a rejected save rather than a second record.
        </p>
      {/if}
    </div>

    <div class="pair">
      <div class="v2-field">
        <label for="f-phone">Phone</label>
        <input
          id="f-phone"
          name="phone"
          class="v2-input"
          bind:value={form.phone}
          onblur={() => (touched.phone = true)}
          aria-invalid={show('phone') ? 'true' : undefined}
        />
        {#if show('phone')}<p class="v2-error">{errors.phone}</p>{/if}
      </div>
      <div class="v2-field">
        <label for="f-jobtitle">Job title</label>
        <input id="f-jobtitle" name="job_title" class="v2-input" bind:value={form.job_title} />
      </div>
    </div>

    <div class="v2-section-label v2-label">Company</div>

    <div class="pair">
      <div class="v2-field">
        <label for="f-company">Company</label>
        <input id="f-company" name="company_name" class="v2-input" bind:value={form.company_name} />
      </div>
      <div class="v2-field">
        <label for="f-industry">Industry</label>
        <select id="f-industry" name="industry" class="v2-input" bind:value={form.industry}>
          <option value="">Not specified</option>
          {#each INDUSTRIES as ind (ind)}
            <option value={ind}>{industryLabel(ind)}</option>
          {/each}
        </select>
      </div>
    </div>

    <div class="v2-field">
      <label for="f-website">Website</label>
      <input id="f-website" name="website" class="v2-input" bind:value={form.website} />
    </div>

    <div class="v2-section-label v2-label">Pipeline</div>

    <div class="pair">
      <div class="v2-field">
        <label for="f-status">
          Status
          {#if isConverted}<span class="locked"><Lock size={10} />Settled</span>{/if}
        </label>
        <!--
          Disabled rather than absent once the lead is converted. Removing the
          control would leave the person hunting for a way to change something
          that cannot be changed; showing it disabled, with the reason directly
          underneath, answers the question they came with.
        -->
        <select
          id="f-status"
          name={isConverted ? undefined : 'status'}
          class="v2-input"
          bind:value={form.status}
          disabled={isConverted}
          aria-describedby={isConverted ? 'status-locked' : undefined}
        >
          {#each LEAD_STATUSES as s (s)}
            <option value={s}>{LEAD_STATUS_LABEL[s]}</option>
          {/each}
        </select>
        <!--
          No hidden field carrying the current value here, deliberately. A
          disabled control submits nothing, PATCH is partial, and "absent"
          means "leave it alone", which is exactly right for a status that
          cannot change.

          Sending it would be worse than useless: `validate_status` raises on
          `value == current` when the current status is irreversible, because
          that is what a repeat conversion looks like. So posting
          `status=converted` alongside a corrected job title got the whole save
          rejected, and the panel below promises the opposite.
        -->
      </div>
      <div class="v2-field">
        <label for="f-source">Source</label>
        <select id="f-source" name="source" class="v2-input" bind:value={form.source}>
          <option value="">Not specified</option>
          {#each LEAD_SOURCES as s (s)}
            <option value={s}>{LEAD_SOURCE_LABEL[s]}</option>
          {/each}
        </select>
      </div>
    </div>

    <!--
      The consequence of the status select, stated where the select is rather
      than in a dialog after the fact, and only where there is one.
    -->
    {#if isConverted}
      <div class="consequence" style="--edge:var(--v2-moss)" id="status-locked">
        <div style="font-weight:600">This lead has already been converted</div>
        <p>
          Its account, contact and opportunity exist and carry the work now. The status stays where
          it is: reopening the lead would not remove any of them, and converting it again would
          build a second opportunity against the same account. The API refuses both.
        </p>
        <p style="margin-top:6px">
          Everything else on this form is still editable. A converted lead is a record, not a
          read-only one.
        </p>
      </div>
    {:else if enteringConverted}
      <div class="consequence" style="--edge:var(--v2-clay)">
        <div style="font-weight:600">Converting creates three records</div>
        <p>
          An Account, a Contact and an Opportunity, with this lead's comments and attachments moved
          across. The lead stays as a converted record, and this is the last time you can change its
          status. There is no endpoint that undoes any of it.
        </p>
        <p style="margin-top:6px">
          Any other change on this form, aside from custom fields, is dropped when it saves
          alongside a conversion. Reopen the lead afterwards to redo it.
        </p>
      </div>
    {:else if statusChanged}
      <p class="v2-hint" style="margin:-6px 0 4px">
        {LEAD_STATUS_LABEL[originalStatus]} → {LEAD_STATUS_LABEL[form.status]}
        {#if form.status === 'closed'}
          · reversible, nothing is created
        {/if}
      </p>
    {/if}

    <div class="pair">
      <div class="v2-field">
        <label for="f-amount">Estimated value</label>
        <input
          id="f-amount"
          name="opportunity_amount"
          class="v2-input v2-num"
          type="text"
          inputmode="decimal"
          bind:value={form.opportunity_amount}
          onblur={() => (touched.opportunity_amount = true)}
          aria-invalid={show('opportunity_amount') ? 'true' : undefined}
        />
        {#if show('opportunity_amount')}
          <p class="v2-error">{errors.opportunity_amount}</p>
        {:else}
          <p class="v2-hint">
            {Number(form.opportunity_amount) > 0
              ? money(Number(form.opportunity_amount), lead.currency)
              : 'What the deal would be worth if it lands.'}
          </p>
        {/if}
      </div>
      <div class="v2-field">
        <label for="f-owner">Owner</label>
        <!-- Bound to the Profile id. The mock bound this to a display name,
             which reads identically on screen and cannot be saved. -->
        <!-- What the select was rendered with. The action compares against it
             so an untouched owner is not sent at all; `assigned_to` is a
             many-to-many and this select is single, so sending it always would
             cut a two-person lead down to one on every save. -->
        <input type="hidden" name="assigned_to_original" value={data.form.assigned_to} />
        <select id="f-owner" name="assigned_to" class="v2-input" bind:value={form.assigned_to}>
          <option value="">Nobody</option>
          {#each data.owners as o (o.id)}
            <option value={o.id}>{o.name}</option>
          {/each}
        </select>
      </div>
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

    <!-- Per-org custom fields, in the order the org defined them. The input
         type follows field_type; every one submits a string (or nothing, for
         an unchecked box) and `_coerce_value` converts on the way in. -->
    {#if customFields.length > 0}
      <div class="v2-label v2-section-label">Details</div>
      {#each customFields as f (f.key)}
        <div class="v2-field">
          {#if f.field_type === 'checkbox'}
            <label class="cf-check">
              <input type="checkbox" name="cf_{f.key}" bind:checked={f.value} />
              <span>{f.label}</span>
            </label>
          {:else}
            <label for="f-cf-{f.key}">
              {f.label}
              {#if f.is_required}<span class="req">required</span>{/if}
            </label>
            {#if f.field_type === 'dropdown'}
              <select
                id="f-cf-{f.key}"
                name="cf_{f.key}"
                class="v2-input"
                bind:value={f.value}
                onblur={() => (touched[`cf_${f.key}`] = true)}
                aria-invalid={show(`cf_${f.key}`) ? 'true' : undefined}
              >
                <option value="">—</option>
                {#each f.options as o (o.value)}
                  <option value={o.value}>{o.label}</option>
                {/each}
              </select>
            {:else if f.field_type === 'textarea'}
              <textarea
                id="f-cf-{f.key}"
                name="cf_{f.key}"
                class="v2-input"
                rows="3"
                bind:value={f.value}
                onblur={() => (touched[`cf_${f.key}`] = true)}
                aria-invalid={show(`cf_${f.key}`) ? 'true' : undefined}></textarea>
            {:else}
              <input
                id="f-cf-{f.key}"
                name="cf_{f.key}"
                class="v2-input"
                type={f.field_type === 'number'
                  ? 'number'
                  : f.field_type === 'date'
                    ? 'date'
                    : 'text'}
                step={f.field_type === 'number' ? 'any' : undefined}
                bind:value={f.value}
                onblur={() => (touched[`cf_${f.key}`] = true)}
                aria-invalid={show(`cf_${f.key}`) ? 'true' : undefined}
              />
            {/if}
            {#if show(`cf_${f.key}`)}
              <p class="v2-error">{errors[`cf_${f.key}`]}</p>
            {/if}
          {/if}
        </div>
      {/each}
    {/if}

    <div class="actions">
      <button class="v2-btn v2-btn-primary" type="submit" disabled={saving}>
        {saving ? 'Saving…' : 'Save changes'}
      </button>
      <a class="v2-btn" href={resolve(`/leads/${lead.id}`)}>Cancel</a>
    </div>
  </form>
</div>

<style>
  .v2-section-label {
    margin: 22px 0 2px;
  }
  .v2-section-label:first-child {
    margin-top: 0;
  }
  .pair {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
  }
  /* A checkbox labels itself inline, the stacked label/input of the other
     fields leaves the box floating under its own caption. */
  .cf-check {
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
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
  /* A left edge rather than a filled panel: this is a consequence attached to
     the control above it, not a separate announcement. */
  .consequence {
    border-left: 2px solid var(--edge);
    padding: 2px 0 2px 12px;
    margin: -4px 0 4px;
  }
  .consequence p {
    margin: 4px 0 0;
    font-size: 13px;
    color: var(--v2-slate);
    max-width: 62ch;
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
  }
</style>
