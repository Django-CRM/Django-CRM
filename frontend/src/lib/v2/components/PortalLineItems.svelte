<script>
  /**
   * The line-item table and the totals ladder, shared by the invoice and
   * estimate portal pages.
   *
   * It exists as one component for one reason: the ladder has an ORDER, and
   * that order is a server contract. `Invoice.recalculate_totals()` and
   * `Estimate.recalculate_totals()` both do
   *
   *     subtotal   = Σ line amount           ← after each line's own discount
   *     discount   = PERCENTAGE ? subtotal × value/100 : value
   *     taxable    = subtotal − discount
   *     tax        = taxable × tax_rate/100        ← taxable, not subtotal
   *     total      = taxable + tax + shipping      ← shipping is NOT taxed
   *
   * Two pages each rendering their own version of that is two pages that
   * eventually disagree with each other and with the PDF. The rows below are
   * emitted in exactly that sequence, and a row is omitted only when the
   * server sent a zero for it, never reordered, never folded together.
   *
   * Nothing here recomputes a total. Every figure is the one the server sent,
   * because the invoice a customer receives and the PDF their accounts payable
   * team files must be the same document, and the only way to guarantee that
   * is to have one authority for the arithmetic.
   */
  import { money } from '$lib/v2/format.js';

  /**
   * @type {{
   *   items: Array<{name: string, description: string, quantity: number, unit_price: number, amount: number}>,
   *   currency: string,
   *   subtotal: number,
   *   discountAmount?: number,
   *   discountType?: string,
   *   discountValue?: number,
   *   taxRate?: number,
   *   taxAmount?: number,
   *   shippingAmount?: number,
   *   total: number
   * }}
   */
  let {
    items,
    currency,
    subtotal,
    discountAmount = 0,
    discountType = '',
    discountValue = 0,
    taxRate = 0,
    taxAmount = 0,
    shippingAmount = 0,
    total
  } = $props();

  /**
   * "10% off" reads as a fact; "Discount 1,800" reads as a number you have to
   * take on faith. When the server says PERCENTAGE, say the percentage.
   */
  let discountLabel = $derived(
    discountType === 'PERCENTAGE' ? `Discount (${discountValue}%)` : 'Discount'
  );
</script>

<table class="v2-doc-items">
  <thead>
    <tr>
      <th>Item</th>
      <th class="v2-r">Qty</th>
      <th class="v2-r">Unit price</th>
      <th class="v2-r">Amount</th>
    </tr>
  </thead>
  <tbody>
    {#each items as item, i (i)}
      <tr>
        <td>
          <span class="v2-doc-item-name">{item.name}</span>
          {#if item.description}
            <span class="v2-doc-item-desc">{item.description}</span>
          {/if}
        </td>
        <td class="v2-r v2-num">{item.quantity}</td>
        <td class="v2-r v2-num">{money(item.unit_price, currency)}</td>
        <td class="v2-r v2-num">{money(item.amount, currency)}</td>
      </tr>
    {/each}
  </tbody>
</table>

<div class="v2-doc-totals">
  <!-- Each label/amount pair is wrapped in a div. Valid inside a dl, and it
       keeps the rule above the total a single continuous line. A two-column
       grid draws that border as two segments with the column gap showing
       through the middle, which reads as a mistake because it is one. -->
  <dl>
    <div>
      <dt>Subtotal</dt>
      <dd class="v2-num">{money(subtotal, currency)}</dd>
    </div>

    {#if discountAmount}
      <div>
        <dt>{discountLabel}</dt>
        <dd class="v2-num">−{money(discountAmount, currency)}</dd>
      </div>
    {/if}

    {#if taxAmount}
      <div>
        <dt>Tax ({taxRate}%)</dt>
        <dd class="v2-num">{money(taxAmount, currency)}</dd>
      </div>
    {/if}

    {#if shippingAmount}
      <div>
        <dt>Shipping</dt>
        <dd class="v2-num">{money(shippingAmount, currency)}</dd>
      </div>
    {/if}

    <div class="v2-doc-total-row">
      <dt>Total</dt>
      <dd class="v2-num">{money(total, currency)}</dd>
    </div>
  </dl>
</div>

<style>
  .v2-doc-items {
    width: 100%;
    border-collapse: collapse;
    margin-top: 4px;
  }
  .v2-doc-items th {
    font-size: 11px;
    font-weight: 600;
    color: var(--v2-slate);
    text-align: left;
    padding: 0 0 7px;
    border-bottom: 1px solid var(--v2-line);
  }
  .v2-doc-items td {
    padding: 11px 0;
    font-size: 13px;
    vertical-align: top;
    border-bottom: 1px solid var(--v2-line-soft);
  }
  .v2-doc-items :global(.v2-r) {
    text-align: right;
  }
  .v2-doc-items th.v2-r,
  .v2-doc-items td.v2-r {
    text-align: right;
    padding-left: 14px;
    white-space: nowrap;
  }
  .v2-doc-item-name {
    display: block;
    font-weight: 500;
  }
  .v2-doc-item-desc {
    display: block;
    margin-top: 2px;
    font-size: 12px;
    color: var(--v2-slate);
    line-height: 1.45;
  }

  .v2-doc-totals {
    display: flex;
    justify-content: flex-end;
    margin-top: 14px;
  }
  .v2-doc-totals dl {
    margin: 0;
    min-width: 240px;
  }
  .v2-doc-totals dl > div {
    display: flex;
    justify-content: space-between;
    gap: 28px;
    padding: 3.5px 0;
  }
  .v2-doc-totals dt {
    font-size: 12.5px;
    color: var(--v2-slate);
  }
  .v2-doc-totals dd {
    margin: 0;
    font-size: 12.5px;
    text-align: right;
  }
  .v2-doc-totals .v2-doc-total-row {
    margin-top: 5px;
    padding-top: 9px;
    border-top: 1px solid var(--v2-line);
  }
  .v2-doc-totals .v2-doc-total-row dt,
  .v2-doc-totals .v2-doc-total-row dd {
    font-size: 14px;
    font-weight: 650;
    color: var(--v2-ink);
  }

  @media (max-width: 520px) {
    /* Unit price is the column a phone can lose: quantity × unit price is
       recoverable from the amount, the amount is not recoverable from
       anything. */
    .v2-doc-items th:nth-child(3),
    .v2-doc-items td:nth-child(3) {
      display: none;
    }
  }
</style>
