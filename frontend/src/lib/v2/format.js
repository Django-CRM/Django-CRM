/** Formatting helpers for v2. Every number rendered goes through one of these. */

/** @param {number|string|null|undefined} n */
export function money(n, currency = 'USD') {
  const v = Number(n ?? 0);
  if (!Number.isFinite(v)) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: v % 1 === 0 ? 0 : 2
  }).format(v);
}

/** @param {number|string|null|undefined} n */
export function count(n) {
  const v = Number(n ?? 0);
  return Number.isFinite(v) ? v.toLocaleString('en-US') : '—';
}

/** @param {string|null|undefined} name */
export function initials(name) {
  return String(name ?? '')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0])
    .join('')
    .toUpperCase();
}

/**
 * ISO string → Date, or null when there is nothing to render.
 *
 * A date-only string ("2026-08-07") names a calendar day, not an instant, and
 * `new Date()` reads it as UTC midnight. A browser west of UTC then prints
 * that as the day before: a due date set for Friday reads as Thursday, and a
 * chart bucket the API labelled Friday sits under the wrong label. Appending a
 * time is what makes it parse as local midnight instead. Anything that carries
 * a time of day is a real instant and is left exactly as it was.
 */
function parseIso(value) {
  if (!value) return null;
  const d =
    typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
      ? new Date(`${value}T00:00:00`)
      : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * ISO date → "8 Aug", or "8 Aug 2023" when it is not this year, otherwise a
 * deal closed three years ago reads as if it closed last week.
 * Returns an em dash for null so table cells never collapse.
 */
export function shortDate(iso, now = new Date()) {
  const d = parseIso(iso);
  if (!d) return '—';
  const sameYear = d.getFullYear() === now.getFullYear();
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'short',
    ...(sameYear ? {} : { year: 'numeric' })
  }).format(d);
}

/** ISO date → "8 August 2026". */
export function longDate(iso) {
  const d = parseIso(iso);
  if (!d) return '—';
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'long',
    year: 'numeric'
  }).format(d);
}

/** Whole days between an ISO date and today. Negative = in the future. */
export function daysSince(iso, now = new Date()) {
  const d = parseIso(iso);
  if (!d) return null;
  return Math.floor((now.getTime() - d.getTime()) / 86400000);
}

/** "12 days ago" / "today" / "in 4 days", for a person, not a machine. */
export function relativeDays(iso, now = new Date()) {
  const n = daysSince(iso, now);
  if (n === null) return '—';
  if (n === 0) return 'today';
  if (n === 1) return 'yesterday';
  if (n > 1) return `${n} days ago`;
  if (n === -1) return 'tomorrow';
  return `in ${Math.abs(n)} days`;
}

/**
 * "just now" / "18 minutes ago" / "3 hours ago", then whole days.
 *
 * `relativeDays` is right for records, a lead touched at 09:00 and one
 * touched at 17:00 are both "today" and the difference does not change what
 * you do. A feed is the opposite: a mention from five minutes ago and one from
 * twenty hours ago are different events, and collapsing both to "today" is how
 * you end up re-reading the whole list to find what is new.
 */
export function relativeTime(iso, now = new Date()) {
  const d = parseIso(iso);
  if (!d) return '—';
  const mins = Math.floor((now.getTime() - d.getTime()) / 60000);
  if (mins < 0) return relativeDays(iso, now);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} minute${mins === 1 ? '' : 's'} ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} hour${hrs === 1 ? '' : 's'} ago`;
  return relativeDays(iso, now);
}

/**
 * A duration in minutes as "1h 48m".
 *
 * Logged time is read in hours: "108m" makes the reader do the division, and
 * two of them on the same screen makes them do it twice. Minutes below the
 * hour keep their own suffix so a short entry does not render as "0h 12m".
 *
 * @param {number|string|null|undefined} mins
 */
export function hoursMinutes(mins) {
  const v = Number(mins ?? 0);
  if (!Number.isFinite(v)) return '0m';
  const m = Math.max(0, Math.round(v));
  const h = Math.floor(m / 60);
  return h ? `${h}h ${m % 60}m` : `${m}m`;
}

/** Compact age for a table cell: "12d", "3h". */
export function shortAge(iso, now = new Date()) {
  const d = parseIso(iso);
  if (!d) return '—';
  const mins = Math.max(0, Math.floor((now.getTime() - d.getTime()) / 60000));
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}
