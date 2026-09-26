<script>
  import { resolve } from '$app/paths';
  /**
   * New estimate.
   *
   * The invoice builder's shape with an estimate's fields: a validity date
   * instead of payment terms, an optional deal link, no shipping (the model
   * has none), and terms alongside the notes. The line-item card and the
   * totals ladder are the invoice builder's own (`LineItemsEditor`,
   * `$lib/v2/line-items.js`), so the two previews cannot drift apart.
   *
   * THE SERVER IS THE AUTHORITY ON THE MONEY. Everything computed here is a
   * preview of what `Estimate.recalculate_totals()` decides on save. The
   * number, public token, status (always Draft), org and creator are the
   * server's to derive and are not asked for.
   *
   * Opened from a deal (`?opportunity=<id>`) the form starts from that deal:
   * its account, currency, name, first contact the caller may open, and its
   * lines. Everything stays editable, and the API checks the account, contact
   * and deal against what this caller may open when it saves.
   */
  import { enhance } from '$app/forms';
  import { untrack } from 'svelte';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import SectionTabs from '$lib/v2/components/SectionTabs.svelte';
  import PortalLineItems from '$lib/v2/components/PortalLineItems.svelte';
  import LineItemsEditor from '$lib/v2/components/LineItemsEditor.svelte';
  import { CURRENCY_CODES } from '$lib/constants/filters.js';
  import { blankLine, documentTotals, lineTotals, linePayload, num } from '$lib/v2/line-items.js';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  const CURRENCIES = CURRENCY_CODES.filter((c) => c.value);
  const today = new Date().toISOString().slice(0, 10);
  const in30 = new Date(Date.now() + 30 * 86_400_000).toISOString().slice(0, 10);

  /*
   * The starting values, read once. A prefill naming a record the pickers do
   * not offer (not visible to this caller, or inactive) starts empty instead,
   * so the form never submits an id the person cannot see on screen.
   */
  const start = untrack(() => {
    const p = data.prefill;
    const account = p && data.accounts.some((a) => a.id === p.account_id) ? p.account_id : '';
    const contact =
      (p?.contact_ids ?? []).find((/** @type {string} */ id) =>
        data.contacts.some((c) => c.id === id && (!c.account_id || c.account_id === account))
      ) ?? '';
    const deal =
      p &&
      data.deals.some(
        (d) => d.id === p.opportunity_id && (!d.account_id || d.account_id === account)
      )
        ? p.opportunity_id
        : '';
    return {
      account,
      contact,
      deal,
      currency:
        p?.currency && CURRENCIES.some((c) => c.value === p.currency)
          ? p.currency
          : data.org?.currency || 'USD',
      title: p?.title ?? '',
      items: p?.items?.length ? p.items : [blankLine()]
    };
  });

  let accountId = $state(start.account);
  let contactId = $state(start.contact);
  let dealId = $state(start.deal);
  let currency = $state(start.currency);
  let title = $state(start.title);
  let issueDate = $state(today);
  let expiryDate = $state(in30);
  let discountType = $state('');
  let discountValue = $state(0);
  let taxRate = $state(0);
  let notes = $state('');
  let terms = $state('');
  let items = $state(start.items);

  /* The invoice builder's rule: a contact bound to another account is a 400. */
  let contactOptions = $derived(
    data.contacts.filter((c) => !c.account_id || c.account_id === accountId)
  );
  /* Deals on this account, plus deals with no account. */
  let dealOptions = $derived(data.deals.filter((d) => !d.account_id || d.account_id === accountId));

  let usableLines = $derived(lineTotals(items).usable);
  let totals = $derived(
    documentTotals({ lines: usableLines, discountType, discountValue, taxRate })
  );

  let datesBackwards = $derived(
    Boolean(expiryDate) && Boolean(issueDate) && expiryDate < issueDate
  );

  let ready = $derived(
    Boolean(accountId) &&
      Boolean(contactId) &&
      Boolean(title.trim()) &&
      usableLines.length > 0 &&
      !datesBackwards
  );

  /** The builder as the API body; only what `EstimateCreateSerializer` accepts. */
  let payload = $derived.by(() => {
    /** @type {Record<string, any>} */
    const body = {
      title: title.trim(),
      account_id: accountId,
      contact_id: contactId,
      currency,
      issue_date: issueDate,
      line_items: linePayload(usableLines)
    };
    if (dealId) body.opportunity_id = dealId;
    if (expiryDate) body.expiry_date = expiryDate;
    if (discountType) {
      body.discount_type = discountType;
      body.discount_value = num(discountValue);
    }
    if (num(taxRate)) body.tax_rate = num(taxRate);
    if (notes.trim()) body.notes = notes.trim();
    if (terms.trim()) body.terms = terms.trim();
    return body;
  });

  function accountChanged() {
    if (!contactOptions.some((c) => c.id === contactId)) contactId = '';
    if (!dealOptions.some((d) => d.id === dealId)) dealId = '';
  }
</script>

<PageHeader title="New estimate">
  {#snippet sub()}
    Nothing is sent until you send it. Saving leaves it as a draft
  {/snippet}
  {#snippet actions()}
    <a class="v2-btn" href={resolve('/invoices/estimates')}>Cancel</a>
    <button type="submit" form="estimate-form" class="v2-btn v2-btn-primary" disabled={!ready}>
      Save as draft
    </button>
  {/snippet}
</PageHeader>

<SectionTabs set="invoices" />

{#if form?.error}
  <div class="v2-pad" style="padding-top:12px">
    <p class="new-error" role="alert">{form.error}</p>
  </div>
{/if}

<form id="estimate-form" method="POST" action="?/create" use:enhance>
  <input type="hidden" name="payload" value={JSON.stringify(payload)} />
</form>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:18px;padding-bottom:32px">
    <div class="v2-split">
      <div>
        <div class="v2-card" style="padding:16px 18px">
          <div class="v2-label" style="margin-bottom:12px">Who and when</div>

          <div class="grid2">
            <label class="f">
              <span>Account</span>
              <select bind:value={accountId} onchange={accountChanged}>
                <option value="">Choose an account</option>
                {#each data.accounts as a (a.id)}
                  <option value={a.id}>{a.name}</option>
                {/each}
              </select>
            </label>

            <label class="f">
              <span>Contact</span>
              <select bind:value={contactId} disabled={!accountId}>
                <option value="">{accountId ? 'Choose a contact' : 'Pick an account first'}</option>
                {#each contactOptions as c (c.id)}
                  <option value={c.id}
                    >{c.name}{c.account_name ? ` · ${c.account_name}` : ''}</option
                  >
                {/each}
              </select>
            </label>

            <label class="f">
              <span>Title</span>
              <input bind:value={title} placeholder="What this estimate covers" required />
            </label>

            <label class="f">
              <span>Deal <span class="opt">(optional)</span></span>
              <select bind:value={dealId} disabled={!accountId}>
                <option value="">No deal</option>
                {#each dealOptions as d (d.id)}
                  <option value={d.id}>{d.name}</option>
                {/each}
              </select>
            </label>

            <label class="f">
              <span>Issue date</span>
              <input type="date" bind:value={issueDate} />
            </label>

            <label class="f">
              <span>Valid until</span>
              <input type="date" bind:value={expiryDate} min={issueDate} />
            </label>

            <label class="f">
              <span>Currency</span>
              <select bind:value={currency}>
                {#each CURRENCIES as c (c.value)}
                  <option value={c.value}>{c.label}</option>
                {/each}
              </select>
            </label>
          </div>

          {#if datesBackwards}
            <p class="warn" role="alert">The estimate cannot expire before it is issued.</p>
          {/if}
        </div>

        <LineItemsEditor bind:items products={data.products} {currency} />

        <div class="v2-card" style="padding:16px 18px;margin-top:14px">
          <div class="v2-label" style="margin-bottom:12px">Adjustments</div>
          <div class="grid2">
            <label class="f">
              <span>Discount</span>
              <select bind:value={discountType}>
                <option value="">None</option>
                <option value="PERCENTAGE">Percentage</option>
                <option value="FIXED">Fixed amount</option>
              </select>
            </label>
            {#if discountType}
              <label class="f">
                <span>{discountType === 'PERCENTAGE' ? 'Percent off' : 'Amount off'}</span>
                <input type="number" min="0" step="0.01" bind:value={discountValue} />
              </label>
            {/if}
            <label class="f">
              <span>Tax rate %</span>
              <input type="number" min="0" step="0.01" bind:value={taxRate} />
            </label>
          </div>
          <p class="hint">Tax applies to the subtotal after the discount.</p>
        </div>

        <div class="v2-card" style="padding:16px 18px;margin-top:14px">
          <label class="f">
            <span>Notes to the customer</span>
            <textarea rows="3" bind:value={notes} placeholder="Appears on the estimate"></textarea>
          </label>
          <label class="f" style="margin-top:12px">
            <span>Terms</span>
            <textarea rows="3" bind:value={terms} placeholder="Deposit, scope, what is excluded"
            ></textarea>
          </label>
        </div>
      </div>

      <div>
        <div class="v2-card preview">
          <div class="v2-card-head">
            <span class="v2-label">What the customer will see</span>
          </div>
          <div style="padding:14px 16px 16px">
            {#if usableLines.length}
              <PortalLineItems
                items={usableLines}
                {currency}
                subtotal={totals.subtotal}
                discountAmount={totals.discountAmount}
                {discountType}
                {discountValue}
                {taxRate}
                taxAmount={totals.taxAmount}
                total={totals.total}
              />
            {:else}
              <p class="empty">
                Add a line with a description and an amount, and the customer's copy appears here.
              </p>
            {/if}
          </div>
        </div>

        <div class="v2-card" style="padding:15px 16px;margin-top:14px">
          <div class="v2-label" style="margin-bottom:9px">Assigned on save</div>
          <dl class="derived">
            <dt>Estimate number</dt>
            <dd>EST-<span class="v2-num">{issueDate.replace(/-/g, '')}</span>-nnnn</dd>
            <dt>Customer link</dt>
            <dd>Generated, and only sent when you send the estimate</dd>
          </dl>
        </div>
      </div>
    </div>
  </div>
</div>

<style>
  .grid2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px 14px;
  }
  .f {
    display: block;
    min-width: 0;
  }
  .f > span {
    display: block;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--v2-slate);
    margin-bottom: 4px;
  }
  .opt {
    display: inline;
    font-weight: 400;
    text-transform: none;
    letter-spacing: 0;
  }
  input,
  select,
  textarea {
    width: 100%;
    min-height: 44px;
    padding: 7px 9px;
    font: inherit;
    font-size: 13px;
    color: var(--v2-ink);
    background: var(--v2-card);
    border: 1px solid var(--v2-line);
    border-radius: 6px;
  }
  textarea {
    resize: vertical;
    line-height: 1.5;
  }
  input:focus,
  select:focus,
  textarea:focus {
    outline: 2px solid var(--v2-ember);
    outline-offset: -1px;
  }
  input[type='number'] {
    font-family: var(--v2-mono);
    font-size: 12.5px;
  }
  .hint {
    margin: 10px 0 0;
    font-size: 11.5px;
    color: var(--v2-slate);
    line-height: 1.5;
  }
  .warn {
    margin: 12px 0 0;
    font-size: 12px;
    color: var(--v2-clay);
    line-height: 1.5;
  }
  #estimate-form {
    display: contents;
  }
  .new-error {
    margin: 0;
    padding: 9px 12px;
    font-size: 12.5px;
    color: var(--v2-clay);
    background: color-mix(in srgb, var(--v2-clay) 8%, transparent);
    border: 1px solid color-mix(in srgb, var(--v2-clay) 25%, transparent);
    border-radius: 7px;
  }
  .preview {
    position: sticky;
    top: 0;
  }
  .empty {
    margin: 0;
    font-size: 12.5px;
    color: var(--v2-slate);
    line-height: 1.5;
  }
  .derived {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 5px 14px;
    margin: 0;
    font-size: 12.5px;
  }
  .derived dt {
    color: var(--v2-slate);
  }
  .derived dd {
    margin: 0;
  }

  @media (max-width: 768px) {
    .grid2 {
      grid-template-columns: 1fr;
    }
  }
</style>
