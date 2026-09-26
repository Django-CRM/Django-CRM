<script>
  import { resolve } from '$app/paths';
  /**
   * New invoice.
   *
   * ── THE ONE THING THIS PAGE IS FOR ───────────────────────────────────────
   * Getting the arithmetic and the due date right BEFORE the document goes to
   * a customer, because both are hard to take back. So the right-hand panel is
   * not a decoration: it renders the running totals through
   * `PortalLineItems`: the very same component the customer's portal page
   * uses. If the number here is wrong, it is wrong in the same way there, and
   * you find that out now rather than from a reply.
   *
   * ── WHAT IS DERIVED, AND BY WHOM ─────────────────────────────────────────
   * Three fields on `invoices.Invoice` are computed by `save()` and are NOT
   * asked for here, because asking would invite a value the server throws
   * away:
   *
   *   invoice_number   generate_invoice_number() → INV-YYYYMMDD-XXXX
   *   public_token     secrets.token_urlsafe(32), with a collision re-roll
   *   due_date         calculate_due_date() from issue_date + payment_terms
   *
   * And `recalculate_totals()` recomputes subtotal, discount, tax, total and
   * amount_due from the line items on every save. THE SERVER IS THE AUTHORITY
   * ON THE MONEY. Everything computed in this file is a preview of what it
   * will decide, mirroring its order exactly: subtotal, then discount, then
   * tax on the discounted amount, then shipping added untaxed. If the two ever
   * disagree, the server is right and this file has a bug.
   *
   * `org` and `created_by` are absent for a different reason: they are
   * server-derived from the JWT and the profile. A form that collects them is
   * a form that can be edited to claim them.
   */
  import { enhance } from '$app/forms';
  import { untrack } from 'svelte';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import SectionTabs from '$lib/v2/components/SectionTabs.svelte';
  import PortalLineItems from '$lib/v2/components/PortalLineItems.svelte';
  import LineItemsEditor from '$lib/v2/components/LineItemsEditor.svelte';
  import { PAYMENT_TERMS_LABEL } from '$lib/v2/enums.js';
  import { CURRENCY_CODES } from '$lib/constants/filters.js';
  import { longDate } from '$lib/v2/format.js';
  import { blankLine, documentTotals, lineTotals, linePayload, num } from '$lib/v2/line-items.js';
  import { Info } from '@lucide/svelte';

  /** @type {{ data: { products: any[], accounts: any[], contacts: any[], org: { currency: string } }, form: any }} */
  let { data, form } = $props();

  /* The estimate builder's list. The org's default currency to start, read
     once; one the list does not carry starts at USD, so the picker never
     shows a blank choice and submits nothing. */
  const CURRENCIES = CURRENCY_CODES.filter((c) => c.value);
  let currency = $state(
    untrack(() =>
      CURRENCIES.some((c) => c.value === data.org?.currency) ? data.org.currency : 'USD'
    )
  );
  const TERM_DAYS = { DUE_ON_RECEIPT: 0, NET_15: 15, NET_30: 30, NET_45: 45, NET_60: 60 };

  let accountId = $state('');
  let contactId = $state('');
  let title = $state('');
  let issueDate = $state(new Date().toISOString().slice(0, 10));
  let paymentTerms = $state('NET_30');
  let customDueDate = $state('');
  let discountType = $state('');
  let discountValue = $state(0);
  let taxRate = $state(0);
  let shipping = $state(0);
  let notes = $state('');

  let items = $state([blankLine()]);

  /**
   * Contacts whose primary account is this account, plus contacts with no
   * account (they attach to anyone). The server enforces the same rule. A
   * contact bound to another account is a 400, so offering only these keeps the
   * form from proposing a pairing the API will refuse.
   */
  let contactOptions = $derived(
    data.contacts.filter((c) => !c.account_id || c.account_id === accountId)
  );

  /* Per-line totals and the ladder, shared with the estimate builder. */
  let usableLines = $derived(lineTotals(items).usable);
  let totals = $derived(
    documentTotals({ lines: usableLines, discountType, discountValue, taxRate, shipping })
  );

  /**
   * `calculate_due_date()`. The interesting case is CUSTOM: it is not in the
   * server's `term_days` map, so `.get(terms, 30)` quietly returns 30 and the
   * invoice gets a Net-30 date nobody chose. The banner below says so.
   */
  let derivedDueDate = $derived.by(() => {
    if (!issueDate) return null;
    if (paymentTerms === 'CUSTOM') return customDueDate || null;
    // UTC arithmetic on the parsed date, so no mutable Date instance and no
    // local-timezone drift; the server recomputes this authoritatively anyway.
    const days = TERM_DAYS[paymentTerms] ?? 30;
    const parsed = Date.parse(issueDate);
    if (Number.isNaN(parsed)) return null;
    return new Date(parsed + days * 86_400_000).toISOString().slice(0, 10);
  });

  let customFallsBack = $derived(paymentTerms === 'CUSTOM' && !customDueDate);

  let ready = $derived(
    Boolean(accountId) && Boolean(contactId) && Boolean(title.trim()) && usableLines.length > 0
  );

  /**
   * The whole builder as the API body, carried in one hidden field so the
   * dynamic line-item list survives the form post intact. Only what the server
   * accepts is sent; org/created_by/number/token/totals are its to derive, and
   * status is server-forced to Draft, so none are here.
   */
  let payload = $derived.by(() => {
    /** @type {Record<string, any>} */
    const body = {
      account_id: accountId,
      contact_id: contactId,
      currency,
      issue_date: issueDate,
      payment_terms: paymentTerms,
      line_items: linePayload(usableLines)
    };
    // Required by the API (Invoice.invoice_title is not blank), so always sent;
    // `ready` blocks submit until it has a value.
    body.invoice_title = title.trim();
    if (paymentTerms === 'CUSTOM' && customDueDate) body.due_date = customDueDate;
    if (discountType) {
      body.discount_type = discountType;
      body.discount_value = num(discountValue);
    }
    if (num(taxRate)) body.tax_rate = num(taxRate);
    if (num(shipping)) body.shipping_amount = num(shipping);
    if (notes.trim()) body.notes = notes.trim();
    return body;
  });
</script>

<PageHeader title="New invoice">
  {#snippet sub()}
    Nothing is sent until you send it. Saving leaves it as a draft
  {/snippet}
  {#snippet actions()}
    <a class="v2-btn" href={resolve('/invoices')}>Cancel</a>
    <button type="submit" form="invoice-form" class="v2-btn v2-btn-primary" disabled={!ready}>
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

<!-- The builder posts as one JSON field so the dynamic line list travels whole.
     The submit button lives in the header and is wired to this form by id. -->
<form id="invoice-form" method="POST" action="?/create" use:enhance>
  <input type="hidden" name="payload" value={JSON.stringify(payload)} />
</form>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:18px;padding-bottom:32px">
    <div class="v2-split">
      <!-- ── the form ─────────────────────────────────────────────────── -->
      <div>
        <div class="v2-card" style="padding:16px 18px">
          <div class="v2-label" style="margin-bottom:12px">Who and when</div>

          <div class="grid2">
            <label class="f">
              <span>Account</span>
              <select bind:value={accountId} onchange={() => (contactId = '')}>
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
              <input bind:value={title} placeholder="What this invoice covers" required />
            </label>

            <label class="f">
              <span>Issue date</span>
              <input type="date" bind:value={issueDate} />
            </label>

            <label class="f">
              <span>Currency</span>
              <select bind:value={currency}>
                {#each CURRENCIES as c (c.value)}
                  <option value={c.value}>{c.label}</option>
                {/each}
              </select>
            </label>

            <label class="f">
              <span>Payment terms</span>
              <select bind:value={paymentTerms}>
                {#each Object.entries(PAYMENT_TERMS_LABEL) as [value, label] (value)}
                  <option {value}>{label}</option>
                {/each}
              </select>
            </label>

            {#if paymentTerms === 'CUSTOM'}
              <label class="f">
                <span>Due date</span>
                <input type="date" bind:value={customDueDate} />
              </label>
            {/if}
          </div>

          {#if customFallsBack}
            <!-- Not a style nit. CUSTOM is missing from the server's term_days
                 map and `.get(terms, 30)` supplies 30, so leaving this empty
                 produces a date the customer will hold you to. -->
            <p class="warn">
              <Info size={13} />
              <span>
                Custom terms with no date set becomes <b>Net 30</b> on save, the server falls back to
                30 days rather than leaving the date empty.
              </span>
            </p>
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
            <label class="f">
              <span>Shipping</span>
              <input type="number" min="0" step="0.01" bind:value={shipping} />
            </label>
          </div>
          <p class="hint">
            Tax applies to the subtotal after the discount. Shipping is added afterwards and is not
            taxed.
          </p>
        </div>

        <div class="v2-card" style="padding:16px 18px;margin-top:14px">
          <div class="v2-label" style="margin-bottom:8px">Notes to the customer</div>
          <textarea rows="3" bind:value={notes} placeholder="Appears on the invoice"></textarea>
        </div>
      </div>

      <!-- ── what the customer will get ───────────────────────────────── -->
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
                shippingAmount={num(shipping)}
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
          <div class="v2-label" style="margin-bottom:9px">When it falls due</div>
          {#if derivedDueDate}
            <div class="due">{longDate(derivedDueDate)}</div>
            <p class="hint" style="margin-top:5px">
              {#if paymentTerms === 'CUSTOM'}
                Set by hand.
              {:else}
                {PAYMENT_TERMS_LABEL[paymentTerms]} from the issue date. The server recalculates this
                on save from the same two fields.
              {/if}
            </p>
          {:else}
            <p class="hint" style="margin:0">Set an issue date to see the due date.</p>
          {/if}
        </div>

        <div class="v2-card" style="padding:15px 16px;margin-top:14px">
          <div class="v2-label" style="margin-bottom:9px">Assigned on save</div>
          <dl class="derived">
            <dt>Invoice number</dt>
            <dd>INV-<span class="v2-num">{issueDate.replace(/-/g, '')}</span>-nnnn</dd>
            <dt>Customer link</dt>
            <dd>Generated, and only sent when you send the invoice</dd>
          </dl>
          <p class="hint" style="margin-top:9px">
            Both come from the server so that two people saving at once cannot collide.
          </p>
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
  input,
  select,
  textarea {
    width: 100%;
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
    display: flex;
    gap: 7px;
    align-items: flex-start;
    margin: 12px 0 0;
    font-size: 12px;
    color: var(--v2-clay);
    line-height: 1.5;
  }
  #invoice-form {
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
  .warn :global(svg) {
    flex: none;
    margin-top: 2px;
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
  .due {
    font-size: 16px;
    font-weight: 640;
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

  @media (max-width: 560px) {
    .grid2 {
      grid-template-columns: 1fr;
    }
  }
</style>
