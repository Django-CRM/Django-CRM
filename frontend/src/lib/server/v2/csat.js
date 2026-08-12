/**
 * The rating a customer clicked in the survey email.
 *
 * The email renders one star per point on the scale, each an ordinary link to
 * `/csat/<token>?rating=N`. This reads that N so the page can open with the
 * star already lit and ask only whether they want to add a comment.
 *
 * It PRE-SELECTS and nothing more. A link is a GET, and mail scanners,
 * corporate link-rewriters and preview bots all follow GETs before a human
 * sees the message, so treating the parameter as an answer would let anything
 * in the delivery path fill in the survey. The rating is recorded by the form
 * POST on the page, never by arriving at it.
 *
 * Lives here rather than in `+page.server.js` so vitest can reach it: that
 * harness resolves `$lib` and `$env/dynamic/public` and nothing else, so a
 * route module importing `$app/forms` for its actions cannot be loaded at all.
 * See the docstring in `vitest.config.js`, and `support.js` for the same move.
 */

// Mirrors CSAT_RATING_MIN / CSAT_RATING_MAX in backend/cases/tasks.py. The
// backend is the boundary that matters: it re-validates the POST against these
// same bounds, so a mismatch here degrades the pre-selection and cannot widen
// what is accepted.
export const RATING_MIN = 1;
export const RATING_MAX = 5;

/**
 * @param {URL} url
 * @returns {number | null} the clicked rating, or null when there wasn't one
 */
export function preselectedRating(url) {
  const raw = url.searchParams.get('rating');
  if (raw === null) return null;

  // Number('') is 0 and Number(' 3 ') is 3, so neither an empty parameter nor
  // a padded one can be trusted to the range check alone. Require the digits
  // and nothing else.
  if (!/^\d+$/.test(raw)) return null;

  const value = Number(raw);
  if (value < RATING_MIN || value > RATING_MAX) return null;
  return value;
}
