<script>
  import { resolve } from '$app/paths';
  /**
   * Editing the organisation.
   *
   * Admin-only. A non-admin who reaches this URL directly gets an "Admins only"
   * state, not the form, but that is the courtesy, not the control: the save
   * posts to `PATCH /api/org/settings/`, which the backend refuses for anyone
   * whose role is not ADMIN. `result.error` reports that refusal.
   *
   * What this form owns is the org's own settings: the company profile printed
   * on invoices, the locale defaults, and the two org-wide case-handling
   * switches. What it deliberately does NOT own: the org API key (a credential,
   * rotated through its own audited action) and `is_active` (the org kill
   * switch). Neither is a field here, and the server would not accept them if
   * they were smuggled into the request.
   *
   * The two switches submit an explicit on/off, so turning one OFF is a real
   * choice the save records, not the "left blank, so unchanged" behaviour of
   * the text fields.
   */
  import { tick, untrack } from 'svelte';
  import { enhance } from '$app/forms';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import SettingsCrumb from '$lib/v2/components/SettingsCrumb.svelte';
  import NextAction from '$lib/v2/components/NextAction.svelte';
  import { CURRENCY_CODES } from '$lib/constants/filters.js';
  import { COUNTRIES } from '$lib/constants/countries.js';
  import { ChevronRight, TriangleAlert } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form: result } = $props();

  // A supported currency is required (the column is non-blank with a default),
  // so the empty "Select Currency" placeholder is dropped from the options.
  const currencyOptions = CURRENCY_CODES.filter((/** @type {any} */ c) => c.value);

  // The full list the backend COUNTRIES accepts, so every stored code has an
  // option. A code missing here would leave the select on its first entry and
  // the next save would silently clear the org's country.
  const countryOptions = COUNTRIES.map((c) => ({ value: c.code, label: c.name }));

  const org = untrack(() => data.org ?? {});
  // Always includes the org's current value, because the API builds the list
  // from the same database the value was validated against. A stored zone with
  // no matching option would make the select submit its first entry instead.
  const timezones = untrack(() => data.timezones ?? [{ name: 'UTC', label: 'UTC' }]);

  let form = $state(
    untrack(() => ({
      name: org.name ?? '',
      company_name: org.company_name ?? '',
      address_line: org.address_line ?? '',
      city: org.city ?? '',
      state: org.state ?? '',
      postcode: org.postcode ?? '',
      country: org.country ?? '',
      phone: org.phone ?? '',
      email: org.email ?? '',
      website: org.website ?? '',
      tax_id: org.tax_id ?? '',
      default_currency: org.default_currency || 'USD',
      default_country: org.default_country ?? '',
      timezone: org.timezone || 'UTC',
      // Booleans travel as strings so the select always submits an explicit value.
      csat_enabled: String(org.csat_enabled ?? true),
      auto_close_children_on_parent_close: String(org.auto_close_children_on_parent_close ?? false)
    }))
  );

  let touched = $state(/** @type {Record<string, boolean>} */ ({}));
  let submitted = $state(false);

  let errors = $derived.by(() => {
    /** @type {Record<string, string>} */
    const e = {};
    if (form.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email))
      e.email = 'That does not look like an email address.';
    // The model stores this in a URLField, which wants a real URL. A light check
    // here saves a server round-trip; the serializer is the actual rule.
    if (form.website && !/^https?:\/\/.+\..+/.test(form.website))
      e.website = 'Include the full address, starting with http:// or https://.';
    return e;
  });

  let valid = $derived(Object.keys(errors).length === 0);
  const show = (/** @type {string} */ field) => (touched[field] || submitted) && errors[field];

  /**
   * These checks are a UX hint. The serializer is the rule, and its 400 is what
   * `result.error` reports.
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
      // Keep the entered values on a server rejection so nothing is retyped.
      await update({ reset: false });
    };
  };
</script>

{#if data.forbidden}
  <PageHeader title="Organization">
    {#snippet crumb()}<SettingsCrumb />{/snippet}
  </PageHeader>
  <div class="v2-pad" style="padding-top:40px">
    <NextAction
      label="Admins only"
      text="Editing organization details is limited to admins. Ask an admin on your team if a company detail, currency, or survey setting needs changing."
    />
  </div>
{:else}
  <PageHeader title="Edit organization" center>
    {#snippet crumb()}
      <a href={resolve('/settings')}>Settings</a>
      <ChevronRight size={12} />
      <a href={resolve('/settings/organization')}>Organization</a>
    {/snippet}
  </PageHeader>

  <div class="v2-scroll v2-pad" style="padding-top:18px">
    <form class="v2-form" method="POST" action="?/save" use:enhance={check} novalidate>
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
            <!-- Counted, not assumed. Only two fields are checked here, but one
                 of them failing on its own is the common case. -->
            <div style="font-weight:600">
              {Object.keys(errors).length === 1 ? 'One field to check' : 'Two fields to check'}
            </div>
            <div class="v2-sub" style="margin-top:2px">Nothing has been saved yet.</div>
          </div>
        </div>
      {/if}

      <div class="v2-label" style="margin-bottom:12px">What customers see</div>
      <p class="v2-hint" style="margin-top:-4px;margin-bottom:14px">
        Printed on every invoice and estimate. Changes apply from now on; documents already sent
        keep what they were sent with.
      </p>

      <div class="v2-field">
        <label for="f-company">Legal name</label>
        <input
          id="f-company"
          name="company_name"
          class="v2-input"
          maxlength="255"
          bind:value={form.company_name}
        />
        <p class="v2-hint">The registered company name, as it should appear on a document.</p>
      </div>

      <div class="v2-field">
        <label for="f-name">Trading name</label>
        <input id="f-name" name="name" class="v2-input" maxlength="100" bind:value={form.name} />
        <p class="v2-hint">What this organisation is called across the app.</p>
      </div>

      <div class="pair">
        <div class="v2-field">
          <label for="f-tax">Tax ID</label>
          <input
            id="f-tax"
            name="tax_id"
            class="v2-input"
            maxlength="50"
            bind:value={form.tax_id}
          />
        </div>
        <div class="v2-field">
          <label for="f-phone">Phone</label>
          <input
            id="f-phone"
            name="phone"
            class="v2-input"
            maxlength="25"
            bind:value={form.phone}
          />
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
          <label for="f-website">Website</label>
          <input
            id="f-website"
            name="website"
            class="v2-input"
            type="url"
            bind:value={form.website}
            onblur={() => (touched.website = true)}
            aria-invalid={show('website') ? 'true' : undefined}
          />
          {#if show('website')}<p class="v2-error">{errors.website}</p>{/if}
        </div>
      </div>

      <div class="v2-field">
        <label for="f-address">Address</label>
        <input
          id="f-address"
          name="address_line"
          class="v2-input"
          maxlength="255"
          bind:value={form.address_line}
        />
      </div>

      <div class="triple">
        <div class="v2-field">
          <label for="f-city">City</label>
          <input id="f-city" name="city" class="v2-input" maxlength="100" bind:value={form.city} />
        </div>
        <div class="v2-field">
          <label for="f-state">State</label>
          <input
            id="f-state"
            name="state"
            class="v2-input"
            maxlength="100"
            bind:value={form.state}
          />
        </div>
        <div class="v2-field">
          <label for="f-postcode">Postcode</label>
          <input
            id="f-postcode"
            name="postcode"
            class="v2-input"
            maxlength="20"
            bind:value={form.postcode}
          />
        </div>
      </div>

      <div class="v2-field">
        <label for="f-country">Country</label>
        <select id="f-country" name="country" class="v2-input" bind:value={form.country}>
          <option value="">Not recorded</option>
          {#each countryOptions as c (c.value)}
            <option value={c.value}>{c.label}</option>
          {/each}
        </select>
      </div>

      <div class="v2-label" style="margin:24px 0 12px">Defaults</div>
      <div class="pair">
        <div class="v2-field">
          <label for="f-currency">Currency</label>
          <select
            id="f-currency"
            name="default_currency"
            class="v2-input"
            bind:value={form.default_currency}
          >
            {#each currencyOptions as c (c.value)}
              <option value={c.value}>{c.label}</option>
            {/each}
          </select>
          <p class="v2-hint">Applied to new invoices and estimates. Existing ones keep theirs.</p>
        </div>
        <div class="v2-field">
          <label for="f-defcountry">Country default</label>
          <select
            id="f-defcountry"
            name="default_country"
            class="v2-input"
            bind:value={form.default_country}
          >
            <option value="">Not set</option>
            {#each countryOptions as c (c.value)}
              <option value={c.value}>{c.label}</option>
            {/each}
          </select>
          <p class="v2-hint">Pre-filled on new addresses.</p>
        </div>
      </div>

      <div class="v2-field">
        <label for="f-timezone">Time zone</label>
        <select id="f-timezone" name="timezone" class="v2-input" bind:value={form.timezone}>
          {#each timezones as zone (zone.name)}
            <option value={zone.name}>{zone.label}</option>
          {/each}
        </select>
        <p class="v2-hint">
          When a day starts for this organisation. Changing it moves what counts as due today and
          overdue, for everyone here.
        </p>
      </div>

      <div class="v2-label" style="margin:24px 0 12px">Behaviour</div>

      <div class="v2-field">
        <label for="f-csat">Satisfaction surveys</label>
        <select id="f-csat" name="csat_enabled" class="v2-input" bind:value={form.csat_enabled}>
          <option value="true">Sending. A survey goes out after a ticket closes</option>
          <option value="false">Off, no surveys, org-wide</option>
        </select>
        <p class="v2-hint">
          Off stops every survey org-wide. There is no per-team exception and no notice on the
          ticket.
        </p>
      </div>

      <div class="v2-field">
        <label for="f-cascade">Close child tickets with the parent</label>
        <select
          id="f-cascade"
          name="auto_close_children_on_parent_close"
          class="v2-input"
          bind:value={form.auto_close_children_on_parent_close}
        >
          <option value="true">Offer it on. The close prompt starts ticked</option>
          <option value="false">Offer it off. The close prompt starts unticked</option>
        </select>
        <p class="v2-hint">
          Only sets how the prompt starts, and only on the mobile app, which is where closing a
          parent offers to close its open children. The person still confirms. Closing a parent on
          the web leaves its children open, with no prompt.
        </p>
      </div>

      <div class="actions">
        <button class="v2-btn v2-btn-primary" type="submit">Save changes</button>
        <a class="v2-btn" href={resolve('/settings/organization')}>Cancel</a>
      </div>
    </form>
  </div>
{/if}

<style>
  .pair {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
  }
  .triple {
    display: grid;
    grid-template-columns: 2fr 1fr 1fr;
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
    .pair,
    .triple {
      grid-template-columns: 1fr;
    }
  }
</style>
