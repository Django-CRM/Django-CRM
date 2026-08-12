import { describe, expect, it } from 'vitest';

import { preselectedRating, RATING_MAX, RATING_MIN } from './csat.js';

/** @param {string} query */
function url(query) {
  return new URL(`https://app.example.com/csat/tok${query}`);
}

describe('preselectedRating', () => {
  it('reads the star clicked in the email', () => {
    for (let value = RATING_MIN; value <= RATING_MAX; value += 1) {
      expect(preselectedRating(url(`?rating=${value}`))).toBe(value);
    }
  });

  it('returns null when the link carries no rating', () => {
    expect(preselectedRating(url(''))).toBeNull();
    expect(preselectedRating(url('?other=3'))).toBeNull();
  });

  // Number('') is 0 and Number(' 3 ') is 3, so a range check on its own would
  // let an empty or padded parameter through as a rating.
  it('rejects anything that is not bare digits', () => {
    for (const raw of ['', ' ', ' 3 ', '3.0', '+3', '-1', '1e1', 'four', 'null']) {
      expect(preselectedRating(url(`?rating=${encodeURIComponent(raw)}`))).toBeNull();
    }
  });

  it('rejects values outside the scale', () => {
    for (const raw of ['0', '6', '10', '99999']) {
      expect(preselectedRating(url(`?rating=${raw}`))).toBeNull();
    }
  });

  // Repeated parameters are legal in a URL and searchParams.get returns the
  // first. Worth pinning: a link-rewriter appending its own rating must not be
  // able to change what the customer clicked into something else.
  it('takes the first value when the parameter repeats', () => {
    expect(preselectedRating(url('?rating=2&rating=5'))).toBe(2);
  });
});
