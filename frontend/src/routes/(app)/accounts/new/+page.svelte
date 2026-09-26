<script>
  import { resolve } from '$app/paths';
  /**
   * A new account.
   *
   * Deliberately short. An account earns its detail from the deals, people and
   * invoices that accumulate against it, and a fourteen-field form standing
   * between somebody and recording a company they just spoke to is how CRMs
   * end up full of records named "asdf". Name is the only thing required;
   * everything else is on the edit page when it is actually known.
   *
   * `org` and `created_by` are not on the form and are not in the request.
   * `AccountsListView.post` derives both from `request.profile`, which comes
   * from the JWT. The only place identity can safely come from.
   */
  import { tick, untrack } from 'svelte';
  import { enhance } from '$app/forms';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import DuplicateNotice from '$lib/v2/components/DuplicateNotice.svelte';
  import { ChevronRight, TriangleAlert } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form: result } = $props();

  /* What was typed survives a rejected submit, and `untrack` keeps a
     revalidation from wiping it back to the defaults underneath the cursor. */
  let form = $state(
    untrack(() => ({
      name: '',
      industry: data.defaults.industry,
      website: '',
      email: '',
      phone: '',
      number_of_employees: '',
      annual_revenue: '',
      city: '',
      country: data.defaults.country,
      description: '',
      assigned_to: '',
      ...(result?.values ?? {})
    }))
  );

  let touched = $state(/** @type {Record<string, boolean>} */ ({}));
  let submitted = $state(false);

  let errors = $derived.by(() => {
    /** @type {Record<string, string>} */
    const e = {};
    if (!form.name.trim()) e.name = 'Give the account the name you would search for.';

    if (form.annual_revenue !== '') {
      const n = Number(form.annual_revenue);
      if (!Number.isFinite(n)) e.annual_revenue = 'Annual revenue has to be a number.';
      else if (n < 0) e.annual_revenue = 'Annual revenue cannot be negative.';
    }
    if (form.number_of_employees !== '') {
      const n = Number(form.number_of_employees);
      if (!Number.isInteger(n)) e.number_of_employees = 'Headcount is a whole number.';
      else if (n < 0) e.number_of_employees = 'Headcount cannot be negative.';
    }
    if (form.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email))
      e.email = 'That does not look like an email address.';
    // The exact regex from `flexible_phone_validator`.
    if (form.phone && !/^[\d\s\-()+.]{7,25}$/.test(form.phone))
      e.phone = '7 to 25 characters: digits, spaces, brackets, dots, dashes. No extensions.';

    return e;
  });

  let valid = $derived(Object.keys(errors).length === 0);
  const show = (/** @type {string} */ field) => (touched[field] || submitted) && errors[field];

  /**
   * The name must be unique in the org, case-insensitively. That check needs
   * the database, so it happens on the server and arrives as `result.error`.
   * This only stops submissions that are wrong on their face.
   *
   * @type {import('./$types').SubmitFunction}
   */
  const check = async ({ cancel }) => {
    submitted = true;
    if (!valid) {
      cancel();
      await tick();
      /** @type {HTMLElement | null} */
      const first = document.querySelector('[aria-invalid="true"]');
      first?.focus();
      return;
    }
    return async ({ update }) => {
      await update({ reset: false });
    };
  };
</script>

<PageHeader title="New account" center>
  {#snippet crumb()}
    <a href={resolve('/accounts')}>Accounts</a>
    <ChevronRight size={12} />
    <span>New</span>
  {/snippet}
  {#snippet sub()}
    A company you sell to. Everything else attaches to it later.
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
          <div style="font-weight:600">The server refused this</div>
          <div class="v2-sub" style="margin-top:2px">{result.error}</div>
        </div>
      </div>
    {/if}

    <div class="v2-field">
      <label for="f-name">Account name</label>
      <input
        id="f-name"
        name="name"
        class="v2-input"
        bind:value={form.name}
        onblur={() => (touched.name = true)}
        aria-invalid={show('name') ? 'true' : undefined}
      />
      {#if show('name')}
        <p class="v2-error">{errors.name}</p>
      {:else}
        <p class="v2-hint">Has to be unique in this organisation, ignoring capitals.</p>
      {/if}
    </div>

    <div class="pair">
      <div class="v2-field">
        <label for="f-industry">Industry</label>
        <select id="f-industry" name="industry" class="v2-input" bind:value={form.industry}>
          <option value="">Not recorded</option>
          {#each data.industries as i (i.value)}
            <option value={i.value}>{i.label}</option>
          {/each}
        </select>
      </div>
      <div class="v2-field">
        <label for="f-owner">Owner</label>
        <select id="f-owner" name="assigned_to" class="v2-input" bind:value={form.assigned_to}>
          <option value="">Nobody</option>
          {#each data.owners as o (o.id)}
            <!-- The value is the Profile id, not the display name. -->
            <option value={o.id}>{o.name}</option>
          {/each}
        </select>
      </div>
    </div>

    <div class="pair">
      <div class="v2-field">
        <label for="f-email">Email</label>
        <input
          id="f-email"
          name="email"
          class="v2-input"
          type="email"
          bind:value={form.email}
          onblur={() => (touched.email = true)}
          aria-invalid={show('email') ? 'true' : undefined}
        />
        {#if show('email')}<p class="v2-error">{errors.email}</p>{/if}
      </div>
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
    </div>

    <div class="v2-field">
      <label for="f-website">Website</label>
      <input id="f-website" name="website" class="v2-input" type="url" bind:value={form.website} />
    </div>

    <div class="pair">
      <div class="v2-field">
        <label for="f-staff">Headcount</label>
        <input
          id="f-staff"
          name="number_of_employees"
          class="v2-input v2-num"
          type="text"
          inputmode="numeric"
          bind:value={form.number_of_employees}
          onblur={() => (touched.number_of_employees = true)}
          aria-invalid={show('number_of_employees') ? 'true' : undefined}
        />
        {#if show('number_of_employees')}<p class="v2-error">{errors.number_of_employees}</p>{/if}
      </div>
      <div class="v2-field">
        <label for="f-revenue">Annual revenue</label>
        <input
          id="f-revenue"
          name="annual_revenue"
          class="v2-input v2-num"
          type="text"
          inputmode="decimal"
          bind:value={form.annual_revenue}
          onblur={() => (touched.annual_revenue = true)}
          aria-invalid={show('annual_revenue') ? 'true' : undefined}
        />
        {#if show('annual_revenue')}<p class="v2-error">{errors.annual_revenue}</p>{/if}
      </div>
    </div>

    <div class="pair">
      <div class="v2-field">
        <label for="f-city">City</label>
        <input id="f-city" name="city" class="v2-input" bind:value={form.city} />
      </div>
      <div class="v2-field">
        <label for="f-country">Country</label>
        <select id="f-country" name="country" class="v2-input" bind:value={form.country}>
          <option value="">Not recorded</option>
          {#each data.countries as c (c.value)}
            <option value={c.value}>{c.label}</option>
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
        rows="3"
        bind:value={form.description}></textarea>
    </div>

    <DuplicateNotice
      module="accounts"
      values={{ name: form.name, email: form.email, phone: form.phone, website: form.website }}
    />

    <div class="actions">
      <button class="v2-btn v2-btn-primary" type="submit">Create account</button>
      <a class="v2-btn" href={resolve('/accounts')}>Cancel</a>
    </div>
  </form>
</div>

<style>
  .pair {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
  }
  .actions {
    display: flex;
    align-items: center;
    gap: 9px;
    margin-top: 22px;
    padding-bottom: 40px;
  }
  @media (max-width: 720px) {
    .pair {
      grid-template-columns: 1fr;
    }
  }
</style>
