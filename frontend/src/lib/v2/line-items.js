/**
 * The arithmetic the invoice and estimate builders share.
 *
 * THE SERVER IS THE AUTHORITY ON THE MONEY. `Invoice.recalculate_totals()`
 * and `Estimate.recalculate_totals()` recompute every figure on save; what is
 * computed here is a preview of what they will decide, in their order:
 * subtotal (the sum of each line's net amount, see `lineAmount`), then the
 * discount, then tax on the discounted amount, then shipping added untaxed
 * (invoices only: an estimate has no shipping, so its builder passes none). If
 * the two ever disagree, the server is right and this file has a bug.
 */

/**
 * A line as the builders edit it. A line started from a deal also carries the
 * deal line's discount (`discount_type`, `discount_value`); a typed line has
 * none.
 * @typedef {{ name: string, description: string, quantity: any, unit_price: any, product: string | null, discount_type?: string, discount_value?: any }} Line
 */

/** @param {any} v */
export const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);

/** An empty line, the shape both builders edit. @returns {Line} */
export const blankLine = () => ({
  name: '',
  description: '',
  quantity: 1,
  unit_price: 0,
  product: null
});

/**
 * What a line adds to the subtotal: quantity x unit price less the line's own
 * discount, the server's `LineAmounts.net_amount`. PERCENTAGE takes that share;
 * any other type with a value takes it as a flat amount, as the server does.
 *
 * @param {Line} line
 */
export function lineAmount(line) {
  const gross = num(line.quantity) * num(line.unit_price);
  const value = num(line.discount_value);
  return gross - (line.discount_type === 'PERCENTAGE' ? gross * (value / 100) : value);
}

/**
 * Each line with its `gross` (quantity x unit price) and `amount` (after its
 * own discount), and the lines worth sending: a name, a quantity above zero
 * and a positive gross. The API refuses a quantity of zero or less and a
 * negative price (`LineAmountsMixin`), so a line that would be refused is never
 * offered to it; a half-typed row is dropped rather than blocking save. A line
 * discounted to nothing is still a line, so the check is on the gross.
 *
 * @param {Line[]} items
 */
export function lineTotals(items) {
  const lines = items.map((i) => ({
    ...i,
    gross: num(i.quantity) * num(i.unit_price),
    amount: lineAmount(i)
  }));
  const usable = lines.filter(
    (l) => l.name.trim() && num(l.quantity) > 0 && num(l.unit_price) >= 0 && l.gross > 0
  );
  return { lines, usable };
}

/**
 * The ladder over the usable lines, in the server's order. Do not reorder.
 *
 * @param {{ lines: Array<{amount: number}>, discountType?: string, discountValue?: any, taxRate?: any, shipping?: any }} args
 */
export function documentTotals({
  lines,
  discountType = '',
  discountValue = 0,
  taxRate = 0,
  shipping = 0
}) {
  const subtotal = lines.reduce((a, l) => a + l.amount, 0);
  const discountAmount =
    discountType === 'PERCENTAGE'
      ? subtotal * (num(discountValue) / 100)
      : discountType === 'FIXED'
        ? num(discountValue)
        : 0;
  const taxable = subtotal - discountAmount;
  const taxAmount = taxable * (num(taxRate) / 100);
  return { subtotal, discountAmount, taxAmount, total: taxable + taxAmount + num(shipping) };
}

/**
 * The usable lines as the API's nested `line_items`. Only the keys the line
 * create serializers accept; the server computes every per-line figure. A
 * line's discount goes only when it has one.
 *
 * @param {Line[]} usable
 */
export function linePayload(usable) {
  return usable.map((l) => {
    /** @type {Record<string, any>} */
    const row = {
      name: l.name.trim(),
      description: (l.description || '').trim(),
      quantity: num(l.quantity),
      unit_price: num(l.unit_price)
    };
    if (l.product) row.product = l.product;
    if (num(l.discount_value)) {
      row.discount_type = l.discount_type || '';
      row.discount_value = num(l.discount_value);
    }
    return row;
  });
}

/** Rounded to the cent, as the server stores money, so a float sum does not
    refuse a discount that exactly matches it. @param {number} v */
const cents = (v) => Math.round(v * 100) / 100;

/**
 * The server's bounds on a discount, in its order and its words
 * (`LineAmountsMixin.validate` for a line, `validate_document_discount` for a
 * document): not negative, a percentage not over 100, and a flat amount not
 * over what it comes off. Returns the message, or '' when the API would take
 * it. A hint only: the API still refuses the rest.
 *
 * @param {string} type
 * @param {any} value
 * @param {number} cap what a flat discount comes off
 * @param {string} capMessage
 */
function discountError(type, value, cap, capMessage) {
  const v = num(value);
  if (v < 0) return 'A discount cannot be negative.';
  if (type === 'PERCENTAGE') return v > 100 ? 'A percentage discount cannot exceed 100.' : '';
  return v > cents(cap) ? capMessage : '';
}

/**
 * A document's own discount against its subtotal. The builders send a
 * discount only once a type is chosen, so no type is nothing to check.
 *
 * @param {string} type
 * @param {any} value
 * @param {number} subtotal
 */
export const documentDiscountError = (type, value, subtotal) =>
  type ? discountError(type, value, subtotal, 'A discount cannot exceed the subtotal.') : '';

/**
 * A line's own discount against its gross. A line only carries one from the
 * deal it came from, and lowering its quantity or price afterwards can leave
 * a flat discount bigger than the line.
 *
 * @param {Line} line
 */
export const lineDiscountError = (line) =>
  num(line.discount_value)
    ? discountError(
        line.discount_type ?? '',
        line.discount_value,
        num(line.quantity) * num(line.unit_price),
        "A discount cannot exceed the line's amount."
      )
    : '';

/**
 * The server's bounds on a document's tax rate (a percentage, 0 to 100) and
 * shipping (never negative), in its words. '' when the API would take it.
 *
 * @param {any} value
 */
export function taxRateError(value) {
  const v = num(value);
  if (v < 0) return 'Tax rate cannot be negative.';
  return v > 100 ? 'Tax rate cannot exceed 100.' : '';
}

/** @param {any} value */
export const shippingError = (value) => (num(value) < 0 ? 'Shipping cannot be negative.' : '');
