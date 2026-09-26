import { describe, expect, it } from 'vitest';
import { droppedValues, isShowing, pickFilters, viewHref } from './saved-views.js';

/** @type {import('./saved-views.js').Spec} */
const LEADS = { keys: ['status', 'tags', 'search', 'rating'], multi: ['status', 'tags'] };

/** @type {import('./saved-views.js').Spec} */
const TICKETS = {
  keys: ['status', 'priority', 'search'],
  multi: ['status'],
  implicit: { key: 'status', values: ['New', 'Assigned', 'Pending'], allParam: 'all' }
};

/** @type {import('./saved-views.js').Spec} */
const BOARD = { keys: ['assigned_to', 'search'], multi: ['assigned_to'], keep: ['view'] };

const page = (/** @type {string} */ qs) => new URL(`http://app.test/x${qs}`);

describe('pickFilters', () => {
  it('keeps the page keys, every value of a multi key, the first of a single one', () => {
    const params = new URLSearchParams(
      'status=assigned&status=in+process&rating=HOT&rating=COLD&search=%20&limit=25&tags=t1'
    );
    expect(pickFilters(params, LEADS)).toEqual({
      status: ['assigned', 'in process'],
      rating: ['HOT'],
      tags: ['t1']
    });
  });

  it("saves the ticket queue's default as its three statuses, and All as none", () => {
    expect(pickFilters(new URLSearchParams(''), TICKETS)).toEqual({
      status: ['New', 'Assigned', 'Pending']
    });
    expect(pickFilters(new URLSearchParams('all=1&priority=High'), TICKETS)).toEqual({
      priority: ['High']
    });
    expect(pickFilters(new URLSearchParams('status=Closed'), TICKETS)).toEqual({
      status: ['Closed']
    });
  });
});

describe('viewHref', () => {
  it('puts every value of a multi key back, and only the first of a single one', () => {
    expect(
      viewHref(page(''), { status: ['a', 'b'], rating: ['HOT', 'COLD'], city: ['x'] }, LEADS)
    ).toBe('/x?status=a&status=b&rating=HOT');
  });

  it('opens a ticket view with no status as All, and three statuses as the queue', () => {
    expect(viewHref(page(''), { priority: ['High'] }, TICKETS)).toBe('/x?priority=High&all=1');
    expect(viewHref(page('?all=1'), { status: ['New', 'Assigned', 'Pending'] }, TICKETS)).toBe(
      '/x?status=New&status=Assigned&status=Pending'
    );
  });

  it('keeps the board a board, and clears the filters already up', () => {
    expect(viewHref(page('?view=board&search=old'), { assigned_to: ['u1'] }, BOARD)).toBe(
      '/x?view=board&assigned_to=u1'
    );
    expect(viewHref(page('?search=old'), {}, BOARD)).toBe('/x');
  });
});

describe('droppedValues', () => {
  it('counts values, not keys', () => {
    expect(droppedValues({ status: ['a', 'b', 'c'] }, LEADS)).toBe(0);
    expect(droppedValues({ rating: ['HOT', 'COLD'] }, LEADS)).toBe(1);
    expect(droppedValues({ next_follow_up: ['2026-01-02'], city: ['a', 'b'] }, LEADS)).toBe(3);
    expect(droppedValues({}, LEADS)).toBe(0);
  });
});

describe('isShowing', () => {
  it('matches the same filters in any order, ignoring non-filter params', () => {
    const params = new URLSearchParams('status=in+process&status=assigned&limit=50');
    expect(isShowing(params, { status: ['assigned', 'in process'] }, LEADS)).toBe(true);
  });

  it('does not match a subset or a superset', () => {
    const params = new URLSearchParams('status=assigned&tags=t1');
    expect(isShowing(params, { status: ['assigned'] }, LEADS)).toBe(false);
    expect(isShowing(params, { status: ['assigned'], tags: ['t1'], search: ['x'] }, LEADS)).toBe(
      false
    );
  });

  it('never marks a view that loses values here as the one showing', () => {
    const params = new URLSearchParams('rating=HOT');
    expect(isShowing(params, { rating: ['HOT', 'COLD'] }, LEADS)).toBe(false);
  });

  it("reads the ticket queue's default and All the way they are saved", () => {
    const open = { status: ['New', 'Assigned', 'Pending'] };
    expect(isShowing(new URLSearchParams(''), open, TICKETS)).toBe(true);
    expect(isShowing(new URLSearchParams('all=1'), open, TICKETS)).toBe(false);
    expect(isShowing(new URLSearchParams('all=1'), {}, TICKETS)).toBe(true);
    expect(isShowing(new URLSearchParams(''), {}, TICKETS)).toBe(false);
  });
});
