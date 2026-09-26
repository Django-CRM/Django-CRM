/**
 * Business hours: the wiring behind `/settings/business-hours`.
 *
 * Server-only. Reads `GET /business-hours/calendar/`, the org's default
 * calendar (created on first read if missing). This is the calendar every SLA
 * target is measured against.
 *
 * Reshape, not analytics. The backend stores the week as fourteen flat
 * TimeFields (`monday_open`/`monday_close` … `sunday_open`/`sunday_close`, a
 * null pair meaning "closed") and serializes each as "HH:MM:SS". The page reads
 * an ordered `days: [{ day, open, close }]` Monday-first, with times as "HH:MM",
 * so fold the flat fields into that array here and trim the seconds. Holidays
 * already arrive as `{ id, date, name }`.
 *
 * WRITE PATHS, AND WHO MAY USE THEM
 * `updateBusinessHours` runs the fold above backwards: `unfoldDays` turns the
 * page's `days` array back into the fourteen flat fields, Monday-first, null
 * for a closed day. It PUTs the calendar's OWN url, `/business-hours/calendar/
 * <id>/`, not the collection path this module's read uses; `BusinessCalendarView.put`
 * is `partial=True` and admin-only, and `is_default` is read-only on
 * `BusinessCalendarSerializer`, so it is never part of the outgoing body.
 * `addHoliday` and `removeHoliday` write the holiday sub-resource,
 * `/business-hours/calendar/<id>/holidays/` and its `<hid>/` detail, also
 * admin-only. A holiday add is idempotent on date: posting a date already on
 * the calendar returns the existing row with 200, not 201, and creates
 * nothing new; that is a normal outcome, not a failure to surface. A holiday
 * removal is a hard delete, the row is gone and the day counts as working
 * time again immediately. All three fail with 403 and
 * `{"error": "Only admins can update business hours."}` for a non-admin;
 * `can_edit` below only decides whether the edit controls are offered, since
 * the backend re-derives admin status from the request's own profile and is
 * what actually refuses.
 */
import { apiRequest } from '$lib/api-helpers.js';
import { viewerIsAdmin } from './organization.js';

/** Monday-first weekday label ↔ the model's field prefix. */
const WEEKDAYS = [
  ['Monday', 'monday'],
  ['Tuesday', 'tuesday'],
  ['Wednesday', 'wednesday'],
  ['Thursday', 'thursday'],
  ['Friday', 'friday'],
  ['Saturday', 'saturday'],
  ['Sunday', 'sunday']
];

/** "09:00:00" → "09:00"; null/"" stays null (a closed day). */
function toHm(value) {
  return value ? value.slice(0, 5) : null;
}

/**
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @returns {Promise<{ calendar: any, can_edit: boolean }>}
 */
export async function getBusinessHours({ cookies }) {
  const cal = await apiRequest('/business-hours/calendar/', {}, { cookies });
  return {
    calendar: {
      id: cal.id,
      name: cal.name,
      timezone: cal.timezone,
      is_default: cal.is_default,
      days: WEEKDAYS.map(([label, key]) => ({
        day: label,
        open: toHm(cal[`${key}_open`]),
        close: toHm(cal[`${key}_close`])
      })),
      holidays: (cal.holidays ?? []).map((h) => ({
        id: h.id,
        name: h.name,
        date: h.date
      }))
    },
    // A display hint (`viewerIsAdmin`), deciding only whether
    // the page offers "Edit hours" / "Add holiday". `BusinessCalendarView.put`
    // and the holiday views re-derive admin status from `request.profile`
    // server-side, and that is what actually refuses a non-admin's write.
    can_edit: viewerIsAdmin(cookies)
  };
}

/**
 * Fold the page's `days` array back into the model's fourteen flat fields.
 *
 * `getBusinessHours` above turns `monday_open` and the rest into
 * `days: [{ day, open, close }]` with "HH:MM" times; this is that in
 * reverse. A closed day is both fields null. One field null and the other
 * set stores a half-open day that the SLA clock reads as nonsense.
 * `BusinessCalendarSerializer.validate()` is the real backstop: it already
 * refuses a half-open day with a 400 (`(open_val is None) != (close_val is
 * None)`). This client-side check exists only to name the day in the
 * message, instead of surfacing the serializer's generic per-field error.
 *
 * @param {{ day: string, open: string | null, close: string | null }[]} days
 */
function unfoldDays(days) {
  /** @type {Record<string, string | null>} */
  const flat = {};
  for (const [label, key] of WEEKDAYS) {
    const entry = days.find((d) => d.day === label);
    const open = entry?.open || null;
    const close = entry?.close || null;
    if (Boolean(open) !== Boolean(close)) {
      throw new Error(`${label} needs both an opening and a closing time, or neither.`);
    }
    flat[`${key}_open`] = open;
    flat[`${key}_close`] = close;
  }
  return flat;
}

/**
 * Save the week plus the calendar's name and timezone.
 *
 * PUTs the calendar's own detail url, not the collection path
 * `getBusinessHours` reads from; `calendar.id` survives that read's reshape,
 * so the caller always has it. `is_default` is read-only on
 * `BusinessCalendarSerializer` and is deliberately never part of the body.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} calendarId
 * @param {{ day: string, open: string | null, close: string | null }[]} days
 * @param {{ name: string, timezone: string, [key: string]: unknown }} meta caller may pass
 *   extra fields (e.g. `is_default`, read off the loaded calendar); only
 *   `name` and `timezone` are ever read out of it
 */
export async function updateBusinessHours({ cookies }, calendarId, days, meta) {
  if (!calendarId) throw new Error('Which calendar? No calendar id was given.');
  const body = { ...unfoldDays(days), name: meta.name, timezone: meta.timezone };
  return await apiRequest(
    `/business-hours/calendar/${calendarId}/`,
    { method: 'PUT', body },
    { cookies }
  );
}

/**
 * Add a holiday to a calendar.
 *
 * `POST /business-hours/calendar/<id>/holidays/` is idempotent on `date`:
 * adding a date already on this calendar returns the existing row with
 * HTTP 200, not 201, and creates no duplicate. That is a normal outcome, not
 * an error, so callers should not treat it as one and this module does not
 * pre-check for an existing date on the client.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} calendarId
 * @param {{ date: string, name: string }} values
 */
export async function addHoliday({ cookies }, calendarId, values) {
  if (!values?.date) throw new Error('A holiday needs a date.');
  const body = { date: values.date, name: values.name };
  return await apiRequest(
    `/business-hours/calendar/${calendarId}/holidays/`,
    { method: 'POST', body },
    { cookies }
  );
}

/**
 * Remove a holiday.
 *
 * `BusinessHolidayDetailView.delete` is a hard delete: the row is gone, not
 * flagged off, and the day it named counts as working time again as soon as
 * the request succeeds.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} calendarId
 * @param {string} holidayId
 */
export async function removeHoliday({ cookies }, calendarId, holidayId) {
  if (!holidayId) throw new Error('Which holiday? No holiday id was given.');
  return await apiRequest(
    `/business-hours/calendar/${calendarId}/holidays/${holidayId}/`,
    { method: 'DELETE' },
    { cookies }
  );
}
