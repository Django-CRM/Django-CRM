<script>
  import { resolve } from '$app/paths';
  /**
   * New recurring schedule.
   *
   * Same shape as the one-off invoice builder (`invoices/new`): three FK
   * pickers, an optional line-item list, and the whole form serialised into one
   * hidden `payload` field so the dynamic list survives the POST intact. Reused
   * directly: the account/contact filtering rule and the line-item row markup.
   *
   * WHAT IS DIFFERENT FROM THE INVOICE BUILDER
   * A schedule has no line-item requirement: `RecurringInvoiceCreateSerializer`
   * lists `line_items` as optional, because a schedule can exist before anyone
   * has priced it out, so `ready` below never checks `usableLines.length`.
   *
   * `org`, `created_by`, `subtotal`, `total_amount` and `invoices_generated` are
   * absent for the same reason they are absent from the invoice builder: they
   * are server-derived, and a form that collects them is a form that can be
   * edited to claim them.
   *
   * NO ROLE GATE
   * `POST /invoices/recurring/` has `permission_classes = (IsAuthenticated,
   * HasOrgContext)`, no admin check, unlike invoice templates. Nothing on this
   * page is conditioned on `role`.
   *
   * THE ONE GUARD THE SERVER DOES NOT HAVE
   * `RecurringInvoiceCreateSerializer` never cross-validates `frequency` against
   * `custom_days`; a CUSTOM schedule with no interval is accepted and then
   * silently generates monthly. `ready` below requires `customDays` whenever
   * CUSTOM is chosen so the form does not walk into that gap, but this is a UX
   * guard over a real backend gap, not a mirror of a server rule.
   */
  import { enhance } from '$app/forms';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import SectionTabs from '$lib/v2/components/SectionTabs.svelte';
  import PortalLineItems from '$lib/v2/components/PortalLineItems.svelte';
  import { RECURRING_FREQUENCY_LABEL, PAYMENT_TERMS_LABEL } from '$lib/v2/enums.js';
  import { money } from '$lib/v2/format.js';
  import { Plus, Trash2 } from '@lucide/svelte';

  /** @type {{ data: { products: any[], accounts: any[], contacts: any[] }, form: any }} */
  let { data, form } = $props();

  const today = new Date().toISOString().slice(0, 10);

  /**
   * The currency codes `RecurringInvoice.currency` accepts (`common.utils.
   * CURRENCY_CODES`), the same set `$lib/server/v2/products.js` offers for the
   * catalogue. Repeated here rather than imported: a `.svelte` file cannot
   * import from `$lib/server/`, and this module has no other reason to share a
   * constant with the page, so it is not worth moving into `enums.js`.
   */
  // All 13 codes the backend accepts (`CURRENCY_CODES` in `common/utils.py`),
  // not a subset. Offering fewer would leave an org that bills in one of the
  // missing ones unable to create a schedule at all, for no reason.
  const CURRENCIES = [
    'USD',
    'EUR',
    'GBP',
    'INR',
    'CAD',
    'AUD',
    'JPY',
    'CNY',
    'CHF',
    'SGD',
    'AED',
    'BRL',
    'MXN'
  ];

  let accountId = $state('');
  let contactId = $state('');
  let title = $state('');
  let frequency = $state('MONTHLY');
  let customDays = $state('');
  let paymentTerms = $state('NET_30');
  let currency = $state('USD');
  let startDate = $state(today);
  let endDate = $state('');
  let nextGenerationDate = $state(today);
  let autoSend = $state(false);
  let discountType = $state('');
  let discountValue = $state(0);
  let taxRate = $state(0);
  let notes = $state('');
  let terms = $state('');

  let items = $state([{ name: '', description: '', quantity: 1, unit_price: 0, product: null }]);

  /**
   * Contacts whose primary account is this account, plus contacts with no
   * account (they attach to anyone).
   *
   * `c.account_id` (set in `+page.server.js` from `contacts.js`'s
   * `accountLink`) is the primary FK when the contact has one, and the first
   * M2M membership otherwise. The server's cross-check,
   * `RecurringInvoiceCreateSerializer.validate` (`invoices/serializer.py:
   * 1182-1193`), looks only at the real primary FK, `contact.account_id`, and
   * only rejects when that FK is set and differs from the chosen account: a
   * contact with no primary FK passes there for any account. So this filter
   * is deliberately stricter than the server, not a mirror of it: it can hide
   * a pairing the API would accept for a contact whose primary FK is unset
   * and whose first M2M membership is some other account. That is the safer
   * direction to be wrong in for a picker: under-offer rather than walk
   * someone into a 400.
   */
  let contactOptions = $derived(
    data.contacts.filter((c) => !c.account_id || c.account_id === accountId)
  );

  const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);

  /** Per-line amount, matching RecurringInvoiceLineItem.net_amount: this form
      sets no line discount, so it is quantity x unit_price. */
  let lines = $derived(items.map((i) => ({ ...i, amount: num(i.quantity) * num(i.unit_price) })));

  /* The same ladder the serializer's _recalculate_totals runs, in order. There
     is no shipping field on a recurring schedule, unlike the one-off invoice. */
  let subtotal = $derived(lines.reduce((a, l) => a + l.amount, 0));
  let discountAmount = $derived(
    discountType === 'PERCENTAGE'
      ? subtotal * (num(discountValue) / 100)
      : discountType === 'FIXED'
        ? num(discountValue)
        : 0
  );
  let taxable = $derived(subtotal - discountAmount);
  let taxAmount = $derived(taxable * (num(taxRate) / 100));
  let total = $derived(taxable + taxAmount);

  /** A line with a name and a positive amount is a line worth billing. */
  let usableLines = $derived(lines.filter((l) => l.name.trim() && l.amount > 0));

  /** Line items are optional on a schedule, so this never checks their count. */
  let ready = $derived(
    Boolean(accountId) &&
      Boolean(contactId) &&
      Boolean(title.trim()) &&
      (frequency !== 'CUSTOM' || Boolean(customDays))
  );

  /**
   * The whole builder as the API body, carried in one hidden field so the
   * dynamic line-item list survives the form post intact. Only what the server
   * accepts is sent; org/created_by/subtotal/total_amount/invoices_generated
   * are its to derive, so none are here.
   */
  let payload = $derived.by(() => {
    /** @type {Record<string, any>} */
    const body = {
      account_id: accountId,
      contact_id: contactId,
      title: title.trim(),
      frequency,
      payment_terms: paymentTerms,
      currency,
      start_date: startDate,
      next_generation_date: nextGenerationDate,
      auto_send: autoSend
    };
    if (frequency === 'CUSTOM' && customDays) body.custom_days = num(customDays);
    if (endDate) body.end_date = endDate;
    if (discountType) {
      body.discount_type = discountType;
      body.discount_value = num(discountValue);
    }
    if (num(taxRate)) body.tax_rate = num(taxRate);
    if (notes.trim()) body.notes = notes.trim();
    if (terms.trim()) body.terms = terms.trim();
    if (usableLines.length) {
      body.line_items = usableLines.map((l) => {
        /** @type {Record<string, any>} */
        const row = {
          name: l.name.trim(),
          description: (l.description || '').trim(),
          quantity: num(l.quantity),
          unit_price: num(l.unit_price)
        };
        if (l.product) row.product = l.product;
        return row;
      });
    }
    return body;
  });

  function addLine() {
    items.push({ name: '', description: '', quantity: 1, unit_price: 0, product: null });
  }

  function addProduct(id) {
    const p = data.products.find((x) => x.id === id);
    if (!p) return;
    items.push({
      name: p.name,
      description: p.sku,
      quantity: 1,
      unit_price: p.price,
      product: p.id
    });
  }

  function removeLine(i) {
    items.splice(i, 1);
    if (!items.length) addLine();
  }
</script>

<PageHeader title="New schedule">
  {#snippet sub()}
    Nothing generates until the first run date arrives. Saving creates the schedule
  {/snippet}
  {#snippet actions()}
    <a class="v2-btn" href={resolve('/invoices/recurring')}>Cancel</a>
    <button type="submit" form="recurring-form" class="v2-btn v2-btn-primary" disabled={!ready}>
      Save schedule
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
<form id="recurring-form" method="POST" action="?/create" use:enhance>
  <input type="hidden" name="payload" value={JSON.stringify(payload)} />
</form>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:18px;padding-bottom:32px">
    <div class="v2-split">
      <!-- the form -->
      <div>
        <div class="v2-card" style="padding:16px 18px">
          <div class="v2-label" style="margin-bottom:12px">Who and what</div>

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

            <label class="f" style="grid-column:1/-1">
              <span>Title</span>
              <input bind:value={title} placeholder="What this schedule is for" required />
            </label>
          </div>
        </div>

        <div class="v2-card" style="padding:16px 18px;margin-top:14px">
          <div class="v2-label" style="margin-bottom:12px">Cadence</div>

          <div class="grid2">
            <label class="f">
              <span>Frequency</span>
              <select bind:value={frequency}>
                {#each Object.entries(RECURRING_FREQUENCY_LABEL) as [value, label] (value)}
                  <option {value}>{label}</option>
                {/each}
              </select>
            </label>

            {#if frequency === 'CUSTOM'}
              <label class="f">
                <span>Every N days</span>
                <input type="number" min="1" step="1" bind:value={customDays} required />
              </label>
            {/if}

            <label class="f">
              <span>Start date</span>
              <input type="date" bind:value={startDate} />
            </label>

            <label class="f">
              <span>Next generation date</span>
              <input type="date" bind:value={nextGenerationDate} />
            </label>

            <label class="f">
              <span>End date <span class="opt">(optional)</span></span>
              <input type="date" bind:value={endDate} />
            </label>

            <label class="f">
              <span>Payment terms</span>
              <select bind:value={paymentTerms}>
                {#each Object.entries(PAYMENT_TERMS_LABEL) as [value, label] (value)}
                  <option {value}>{label}</option>
                {/each}
              </select>
            </label>

            <label class="f">
              <span>Currency</span>
              <select bind:value={currency}>
                {#each CURRENCIES as c (c)}
                  <option value={c}>{c}</option>
                {/each}
              </select>
            </label>
          </div>

          <label class="check">
            <input type="checkbox" bind:checked={autoSend} />
            <span>
              Send automatically when generated
              <span class="hint-inline">off leaves a draft for you to review and send</span>
            </span>
          </label>
        </div>

        <div class="v2-card" style="padding:16px 18px;margin-top:14px">
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">
            <div class="v2-label">Lines <span class="opt">(optional)</span></div>
            <select
              class="catalogue"
              value=""
              onchange={(e) => {
                addProduct(e.currentTarget.value);
                e.currentTarget.value = '';
              }}
            >
              <option value="">Add from catalogue…</option>
              {#each data.products as p (p.id)}
                <option value={p.id}>{p.name}, {money(p.price, currency)}</option>
              {/each}
            </select>
          </div>

          {#each items as item, i (i)}
            <div class="line">
              <div class="line-main">
                <input class="line-name" bind:value={item.name} placeholder="Description" />
                <input
                  class="line-desc"
                  bind:value={item.description}
                  placeholder="Detail the customer sees under the name (optional)"
                />
              </div>
              <label class="line-n">
                <span>Qty</span>
                <input type="number" min="0" step="1" bind:value={item.quantity} />
              </label>
              <label class="line-n">
                <span>Unit price</span>
                <input type="number" min="0" step="0.01" bind:value={item.unit_price} />
              </label>
              <div class="line-total v2-num">
                {money(num(item.quantity) * num(item.unit_price), currency)}
              </div>
              <button
                class="line-del"
                onclick={() => removeLine(i)}
                aria-label="Remove this line"
                title="Remove this line"
              >
                <Trash2 size={14} />
              </button>
            </div>
          {/each}

          <button class="v2-btn v2-btn-sm" style="margin-top:10px" onclick={addLine}>
            <Plus size={13} />Add a line
          </button>
        </div>

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
          <p class="hint">
            Tax applies to the subtotal after the discount, on every invoice raised.
          </p>
        </div>

        <div class="v2-card" style="padding:16px 18px;margin-top:14px">
          <div class="v2-label" style="margin-bottom:8px">Notes to the customer</div>
          <textarea rows="3" bind:value={notes} placeholder="Appears on every invoice raised"
          ></textarea>
          <div class="v2-label" style="margin:14px 0 8px">Terms</div>
          <textarea rows="3" bind:value={terms} placeholder="Payment terms and conditions"
          ></textarea>
        </div>
      </div>

      <!-- what each generated invoice will total, from the lines priced in so far -->
      <div>
        <div class="v2-card preview">
          <div class="v2-card-head">
            <span class="v2-label">Each invoice this schedule raises</span>
          </div>
          <div style="padding:14px 16px 16px">
            {#if usableLines.length}
              <PortalLineItems
                items={usableLines}
                {currency}
                {subtotal}
                {discountAmount}
                {discountType}
                {discountValue}
                {taxRate}
                {taxAmount}
                {total}
              />
            {:else}
              <p class="empty">
                Lines are optional here. Add one to preview what each generated invoice will total,
                or save the schedule without pricing it yet.
              </p>
            {/if}
          </div>
        </div>

        <div class="v2-card" style="padding:15px 16px;margin-top:14px">
          <div class="v2-label" style="margin-bottom:9px">On save</div>
          <dl class="derived">
            <dt>Subtotal, total</dt>
            <dd>Recalculated by the server on every save and every generated invoice</dd>
            <dt>Visibility</dt>
            <dd>
              Any signed-in teammate may create one. After that only the creator, an assignee, or an
              admin can see or change it, and this form has no way to add an assignee, so only you
              and an admin will see this schedule
            </dd>
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
    text-transform: none;
    font-weight: 500;
    letter-spacing: 0;
    color: var(--v2-slate);
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

  .check {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    margin-top: 14px;
    font-size: 13px;
    cursor: pointer;
  }
  .check input {
    width: auto;
    margin-top: 3px;
  }
  .hint-inline {
    display: block;
    font-size: 11.5px;
    color: var(--v2-slate);
  }

  .catalogue {
    width: auto;
    min-width: 0;
    margin-left: auto;
    font-size: 12px;
    padding: 5px 8px;
  }

  .line {
    display: grid;
    grid-template-columns: 72px 116px 1fr 26px;
    gap: 9px;
    align-items: end;
    padding: 9px 0;
    border-bottom: 1px solid var(--v2-line-soft);
  }
  .line-main {
    grid-column: 1 / -1;
    min-width: 0;
  }
  .line-desc {
    margin-top: 5px;
    font-size: 12px;
    color: var(--v2-slate);
  }
  .line-n > span {
    display: block;
    font-size: 10px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--v2-slate);
    margin-bottom: 3px;
  }
  .line-total {
    font-size: 13px;
    font-weight: 600;
    text-align: right;
    min-width: 74px;
    padding-bottom: 8px;
  }
  .line-del {
    background: none;
    border: 0;
    padding: 0 0 9px;
    color: var(--v2-slate);
    cursor: pointer;
  }
  .line-del:hover {
    color: var(--v2-rust);
  }

  .hint {
    margin: 10px 0 0;
    font-size: 11.5px;
    color: var(--v2-slate);
    line-height: 1.5;
  }
  #recurring-form {
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
    .line-del {
      min-width: 40px;
      min-height: 40px;
      padding: 0 0 9px;
    }
  }
  @media (max-width: 560px) {
    .line {
      grid-template-columns: 1fr 1fr auto 40px;
    }
    .grid2 {
      grid-template-columns: 1fr;
    }
  }
</style>
