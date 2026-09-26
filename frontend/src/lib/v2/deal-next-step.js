import { daysSince, shortDate } from '$lib/v2/format.js';

/**
 * A deal's next open task as a phrase for the pipeline card and list row, or
 * `null` to show nothing.
 *
 * Only a deal in an open stage is flagged: a won or lost deal has no next step
 * to take, so it shows nothing, whether or not a task is still open on it. The
 * decision reads `stage_kind`, which the API derives from the deal's own
 * pipeline stage, never the stage code, because an admin can call a won stage
 * anything. `next_activity` is `null` when the viewer has no open task on the
 * deal (the API counts only tasks they can open), which is the flag, and
 * `undefined` when the response did not compute it, which is not.
 *
 * @param {{ stage_kind?: string | null, next_activity?: any }} deal
 * @param {Date} [now]
 * @returns {{ text: string, late: boolean } | null}
 */
export function dealNextStep(deal, now = new Date()) {
  if (deal.stage_kind !== 'open') return null;
  const next = deal.next_activity;
  if (next === undefined) return null;
  if (next === null) return { text: 'No next step', late: true };
  const due = next.due_date ? shortDate(next.due_date, now) : 'no date';
  return { text: `${next.title} · ${due}`, late: (daysSince(next.due_date, now) ?? 0) > 0 };
}
