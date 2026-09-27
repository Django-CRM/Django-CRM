/**
 * Calendar dates for a form's defaults, as `YYYY-MM-DD`.
 *
 * "Today" is the org's day, not UTC's and not the browser's: an invoice raised
 * at 09:00 in Kolkata is dated that day, although UTC still reads yesterday.
 * The zone comes from the layout (`data.org.timezone`, the JWT's org claim).
 */

/**
 * Today in `timeZone`. A zone the runtime does not know falls back to UTC, the
 * API's own default zone, rather than throwing on a form's first render.
 *
 * @param {string} timeZone IANA name, e.g. `Asia/Kolkata`
 * @param {Date} [now]
 */
export function todayIn(timeZone, now = new Date()) {
  let parts;
  try {
    parts = new Intl.DateTimeFormat('en-US', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit'
    }).formatToParts(now);
  } catch {
    return now.toISOString().slice(0, 10);
  }
  /** @param {string} type */
  const part = (type) => parts.find((p) => p.type === type)?.value;
  return `${part('year')}-${part('month')}-${part('day')}`;
}

/**
 * `days` after a `YYYY-MM-DD` date. Pure calendar arithmetic in UTC, so no
 * zone can move the answer.
 *
 * @param {string} isoDate
 * @param {number} days
 */
export function addDays(isoDate, days) {
  const d = new Date(`${isoDate}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}
