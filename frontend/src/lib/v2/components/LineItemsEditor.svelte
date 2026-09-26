<script>
  /**
   * The line-item card the invoice and estimate builders share: add from the
   * catalogue, type a line, set quantity and price, remove a line.
   *
   * `items` is bound, so the page owns the list and derives its totals from
   * it (see `$lib/v2/line-items.js`). A catalogue pick copies the price AND
   * links the product: the line stores its own `unit_price` so a later
   * catalogue change never rewrites a sent document, and the `product` FK
   * (server-validated to this org) records where it came from.
   */
  import { money } from '$lib/v2/format.js';
  import { blankLine, lineAmount, num } from '$lib/v2/line-items.js';
  import { Plus, Trash2 } from '@lucide/svelte';

  /**
   * @type {{
   *   items: Array<{name: string, description: string, quantity: any, unit_price: any, product: string | null, discount_type?: string, discount_value?: any}>,
   *   products: Array<{id: string, name: string, sku?: string, price: any}>,
   *   currency: string
   * }}
   */
  let { items = $bindable(), products, currency } = $props();

  function addLine() {
    items.push(blankLine());
  }

  /** @param {string} id */
  function addProduct(id) {
    const p = products.find((x) => x.id === id);
    if (!p) return;
    items.push({
      name: p.name,
      description: p.sku ?? '',
      quantity: 1,
      unit_price: p.price,
      product: p.id
    });
  }

  /** @param {number} i */
  function removeLine(i) {
    items.splice(i, 1);
    if (!items.length) addLine();
  }
</script>

<div class="v2-card" style="padding:16px 18px;margin-top:14px">
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">
    <div class="v2-label">Lines</div>
    <select
      class="catalogue"
      value=""
      aria-label="Add a line from the catalogue"
      onchange={(e) => {
        addProduct(e.currentTarget.value);
        e.currentTarget.value = '';
      }}
    >
      <option value="">Add from catalogue…</option>
      {#each products as p (p.id)}
        <option value={p.id}>{p.name}, {money(p.price, currency)}</option>
      {/each}
    </select>
  </div>

  {#each items as item, i (i)}
    <div class="line">
      <div class="line-main">
        <input
          class="line-name"
          bind:value={item.name}
          placeholder="Description"
          aria-label="Line description"
        />
        <input
          class="line-desc"
          bind:value={item.description}
          placeholder="Detail the customer sees under the name (optional)"
          aria-label="Line detail"
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
        {money(lineAmount(item), currency)}
        {#if num(item.discount_value)}
          <!-- Carried from the deal the line came from; it counts toward the total. -->
          <span class="line-off">
            less {item.discount_type === 'PERCENTAGE'
              ? `${num(item.discount_value)}%`
              : money(num(item.discount_value), currency)}
          </span>
        {/if}
      </div>
      <button
        type="button"
        class="line-del"
        onclick={() => removeLine(i)}
        aria-label="Remove this line"
        title="Remove this line"
      >
        <Trash2 size={14} />
      </button>
    </div>
  {/each}

  <button type="button" class="v2-btn v2-btn-sm" style="margin-top:10px" onclick={addLine}>
    <Plus size={13} />Add a line
  </button>
</div>

<style>
  input,
  select {
    width: 100%;
    padding: 7px 9px;
    font: inherit;
    font-size: 13px;
    color: var(--v2-ink);
    background: var(--v2-card);
    border: 1px solid var(--v2-line);
    border-radius: 6px;
  }
  input:focus,
  select:focus {
    outline: 2px solid var(--v2-ember);
    outline-offset: -1px;
  }
  input[type='number'] {
    font-family: var(--v2-mono);
    font-size: 12.5px;
  }
  .catalogue {
    width: auto;
    min-width: 0;
    margin-left: auto;
    font-size: 12px;
    padding: 5px 8px;
  }
  .line {
    /* Two rows, always. Squeezing the description into the leftover 1fr
       beside four numeric columns leaves it about 85px wide, the widest
       field in the record rendered as the narrowest box on the screen. The
       numbers are fixed-width because they are short; the description gets
       the whole row because it is not. */
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
  .line-off {
    display: block;
    font-size: 11px;
    font-weight: 400;
    color: var(--v2-slate);
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

  @media (max-width: 768px) {
    /* Removing a line is destructive and had a 23px target. Widened to 44px,
       with the column widened to match so it does not steal room from the
       inputs. Qty and unit price share the row; the total keeps its own
       column so it never wraps under the inputs it belongs to. */
    .line {
      grid-template-columns: 1fr 1fr auto 44px;
    }
    input,
    select {
      min-height: 44px;
    }
    .line-del {
      min-width: 44px;
      min-height: 44px;
      padding: 0 0 9px;
    }
  }
</style>
