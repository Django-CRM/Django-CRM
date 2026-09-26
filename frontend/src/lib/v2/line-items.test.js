import { describe, it, expect } from 'vitest';
import {
  blankLine,
  documentTotals,
  lineAmount,
  lineTotals,
  linePayload
} from '$lib/v2/line-items.js';

const line = (over = {}) => ({
  ...blankLine(),
  name: 'Design',
  quantity: 2,
  unit_price: 150,
  ...over
});

describe('which lines are sent', () => {
  it('keeps a named line with a positive amount', () => {
    expect(lineTotals([line()]).usable).toHaveLength(1);
  });

  it('drops a blank, a zero quantity, a negative quantity and a negative price', () => {
    const { usable, lines } = lineTotals([
      line({ name: '  ' }),
      line({ quantity: 0 }),
      line({ quantity: -1, unit_price: -5 }),
      line({ unit_price: -5 })
    ]);
    // `-1 x -5` is a positive total, and the API refuses both numbers.
    expect(lines[2].gross).toBe(5);
    expect(usable).toEqual([]);
  });
});

describe("a line's own discount", () => {
  // The backend's worked numbers (test_line_discount_totals.py).
  const design = line({
    name: 'Design',
    quantity: 2,
    unit_price: 100,
    discount_type: 'PERCENTAGE',
    discount_value: 10
  });
  const hosting = line({
    name: 'Hosting',
    quantity: 1,
    unit_price: 50,
    discount_type: 'FIXED',
    discount_value: 5
  });

  it('takes a percentage or a flat amount off the line', () => {
    expect(lineAmount(design)).toBe(180);
    expect(lineAmount(hosting)).toBe(45);
    expect(lineAmount(line())).toBe(300);
  });

  it('counts toward the subtotal before the document discount, tax and shipping', () => {
    const { usable } = lineTotals([design, hosting]);
    expect(
      documentTotals({
        lines: usable,
        discountType: 'PERCENTAGE',
        discountValue: 10,
        taxRate: 20,
        shipping: 10
      })
    ).toEqual({ subtotal: 225, discountAmount: 22.5, taxAmount: 40.5, total: 253 });
  });

  it('keeps a line discounted to nothing', () => {
    const free = line({ discount_type: 'PERCENTAGE', discount_value: 100 });
    expect(lineTotals([free]).usable).toHaveLength(1);
  });

  it('sends the discount only on a line that has one', () => {
    const [discounted, plain] = linePayload([design, line()]);
    expect(discounted).toMatchObject({ discount_type: 'PERCENTAGE', discount_value: 10 });
    expect(plain).not.toHaveProperty('discount_type');
    expect(plain).not.toHaveProperty('discount_value');
  });
});

describe('the totals ladder, in the server order', () => {
  const lines = lineTotals([line(), line({ name: 'Build', quantity: 1, unit_price: 700 })]).usable;

  it('discounts the subtotal, then taxes what is left', () => {
    // Mirrors the backend test: 1000, 10% off = 900, 20% tax = 180.
    expect(
      documentTotals({ lines, discountType: 'PERCENTAGE', discountValue: 10, taxRate: 20 })
    ).toEqual({ subtotal: 1000, discountAmount: 100, taxAmount: 180, total: 1080 });
  });

  it('adds shipping untaxed and ignores a discount value with no type', () => {
    expect(documentTotals({ lines, discountValue: 50, taxRate: 10, shipping: 25 })).toEqual({
      subtotal: 1000,
      discountAmount: 0,
      taxAmount: 100,
      total: 1125
    });
  });
});

describe('the nested line_items body', () => {
  it('sends only the keys the create serializers accept', () => {
    const [row] = linePayload([line({ name: ' Design ', product: 'p1' })]);
    expect(row).toEqual({
      name: 'Design',
      description: '',
      quantity: 2,
      unit_price: 150,
      product: 'p1'
    });
  });
});
