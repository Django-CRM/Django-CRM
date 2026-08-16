import { fail } from '@sveltejs/kit';
import {
  getTicket,
  getTicketTree,
  closeTicketWithChildren,
  replyToTicket,
  updateTicket
} from '$lib/server/v2/tickets.js';
import { getOrgSettings } from '$lib/server/v2/organization.js';
import {
  listTicketTime,
  startTicketTimer,
  stopTimer,
  logTicketTime,
  setEntryBillable,
  deleteEntry
} from '$lib/server/v2/timesheet.js';
import { readableError } from '$lib/server/v2/form-errors.js';
import { openDescendants, subtreeTruncated, cascadedCount, closeResultMessage } from './close.js';

/**
 * The ticket, plus what closing it would take with it.
 *
 * The tree and the org settings are fetched ONLY for a ticket that has
 * children. Most tickets have none, and two extra requests on every ticket
 * open to answer a question that cannot arise is a cost paid for nothing.
 *
 * Neither extra is allowed to break the page: a ticket that will not render
 * because its tree call failed is a worse outcome than a close button that
 * falls back to the plain one. Both fall back quietly, and the close action
 * re-derives everything server-side anyway, so nothing here is trusted.
 *
 * The time entries ride along in the same wave. They are their own request,
 * the ticket envelope carries only the `time_summary` totals, and they are
 * allowed to fail: a ticket that will not render because the time panel could
 * not load is the same bad trade as the tree below. `null` means the fetch
 * failed and the panel says so; `[]` means nobody has logged anything.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, params, locals }) {
  const [data, timeEntries] = await Promise.all([
    getTicket({ cookies }, params.id),
    listTicketTime({ cookies }, params.id).catch(() => null)
  ]);

  const time = {
    entries: timeEntries,
    // Server-derived from the JWT, never the client. Display-only: it tells
    // the panel which running timer is this person's to stop, since an admin
    // sees the whole team's rows. The API decides who may actually stop one.
    viewerUserId: locals.user?.id ?? null,
    // What a running timer's elapsed minutes are counted from. The browser
    // clock only adds the minutes since this page loaded, the same split the
    // timesheet page uses: how long somebody has been working is not a
    // question a machine with the wrong date gets to answer.
    now: new Date().toISOString()
  };

  // `child_count` sits on the ticket itself here. The `server` block with a
  // `child_count` of its own belongs to the EDIT page's loader, and reading it
  // from this one is silently always-undefined, so the panel never appeared.
  if (!data.ticket?.child_count) return { ...data, time };

  const [tree, settings] = await Promise.all([
    getTicketTree({ cookies }, params.id).catch(() => null),
    getOrgSettings({ cookies }).catch(() => null)
  ]);

  return {
    ...data,
    time,
    close: {
      descendants: openDescendants(tree?.root, params.id),
      truncated: subtreeTruncated(tree?.root, params.id),
      // `getOrgSettings` returns `{ org, can_edit }`, so the setting is one
      // level in. The checkbox's starting position, and the only place this
      // org setting reaches a web user. False when the org has not set it or
      // the fetch failed: a cascade nobody asked for must not start ticked.
      cascade_default: settings?.org?.auto_close_children_on_parent_close === true
    }
  };
}

/** @type {import('./$types').Actions} */
export const actions = {
  /**
   * Post a reply, or an internal note.
   *
   * A reply may also move the ticket; "answer and set to Pending" is one
   * decision, not two, so the status change goes with it when the composer
   * asked for one. The reply is posted first: if the status change is refused
   * (the close gate, say) the customer has still been answered, which is the
   * order that loses the least.
   */
  reply: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const body = form.get('body')?.toString().trim() ?? '';
    const internal = form.get('internal') === 'on';
    const status = form.get('status')?.toString().trim() ?? '';

    const picked = form.get('attachment');
    const file =
      picked && typeof picked === 'object' && 'size' in picked && picked.size > 0 ? picked : null;

    // A ticket accepts a file on its own, the API saves the attachment in a
    // block separate from the comment, so this refuses only the empty case.
    if (!body && !file) {
      return fail(400, {
        body,
        internal,
        error: 'Write something or attach a file before sending.'
      });
    }

    try {
      await replyToTicket({ cookies }, params.id, { body, internal, file });
    } catch (/** @type {any} */ err) {
      return fail(400, { body, internal, error: readableError(err, 'Could not post this reply.') });
    }

    if (status) {
      try {
        await updateTicket({ cookies }, params.id, { status });
      } catch (/** @type {any} */ err) {
        return fail(400, {
          sent: true,
          error: readableError(err, `Reply posted, but the status stayed put.`)
        });
      }
    }

    return { sent: true, internal };
  },

  /**
   * Move the ticket without saying anything.
   *
   * Closing needs a date; `Case.clean()` has always said so and the serializer
   * now enforces it, so the button supplies today rather than bouncing the
   * user into a form to type a date they were never going to change. Where an
   * approval rule covers the ticket, the API refuses and says which rule.
   */
  setStatus: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const status = form.get('status')?.toString().trim() ?? '';
    if (!status) return fail(400, { error: 'No status was chosen.' });

    /** @type {Record<string, any>} */
    const values = { status };
    if (status === 'Closed') values.closed_on = new Date().toISOString().slice(0, 10);

    try {
      await updateTicket({ cookies }, params.id, values);
    } catch (/** @type {any} */ err) {
      return fail(400, { error: readableError(err, 'Could not change the status.') });
    }

    return { moved: status };
  },

  /**
   * Close a parent ticket, and optionally its open descendants.
   *
   * A separate action from `setStatus` rather than a flag on it. `setStatus`
   * PATCHes the case; this posts to `close-with-children/`, which closes the
   * subtree in one transaction and writes a `PARENT_CLOSED_CASCADE` activity
   * row on each child. Folding the two together would mean a ticket with no
   * children took the heavier path for no reason, and the approval gate on the
   * ordinary close lives on the PATCH.
   *
   * `cascade` is read from the checkbox, so an unticked box sends `false` and
   * closes the parent alone. It is never omitted: the API reads the org
   * default only when the key is absent, which would let a setting decide
   * something the person confirming had just decided themselves.
   *
   * What is reported back is `cascaded_case_ids` from the response, not the
   * count that was on screen. Between rendering the page and pressing the
   * button somebody else may have closed those children, and claiming to have
   * closed three tickets that were already closed is a lie about a
   * destructive action.
   */
  closeWithChildren: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const cascade = form.get('cascade') === 'on';
    const comment = form.get('resolution_comment')?.toString().trim() ?? '';

    let result;
    try {
      result = await closeTicketWithChildren({ cookies }, params.id, {
        cascade,
        resolution_comment: comment
      });
    } catch (/** @type {any} */ err) {
      return fail(400, { error: readableError(err, 'Could not close this ticket.') });
    }

    return {
      moved: 'Closed',
      closed: closeResultMessage({ cascade, cascaded: cascadedCount(result) })
    };
  },

  /*
   * The five time-panel writes below all report through `timeError` rather
   * than the `error` the reply and close actions use. That banner sits at the
   * top of the page and the panel does not, so on a phone a shared key puts
   * the reason a delete was refused a full screen above the button that was
   * pressed.
   */

  /**
   * Start the clock on this ticket.
   *
   * One timer per person, org-wide: the API answers 409 when there is already
   * one running and names the ticket it is on. That id is passed back so the
   * panel can link to it, because the fix for "you already have a timer
   * running" is on that other ticket, not this one.
   */
  startTimer: async ({ cookies, params }) => {
    try {
      await startTicketTimer({ cookies }, params.id);
    } catch (/** @type {any} */ err) {
      return fail(err?.status === 409 ? 409 : 400, {
        timeError: readableError(err, 'Could not start the timer.'),
        runningTicketId: err?.body?.running_case_id ?? null
      });
    }

    return { timeStarted: true };
  },

  /** Stop a running timer. The API rejects one that is already stopped, which
   *  is what a double-submit looks like, so the panel disables the button
   *  while this is in flight. */
  stopTimer: async ({ cookies, request }) => {
    const form = await request.formData();
    const entryId = form.get('entry_id')?.toString() ?? '';

    try {
      await stopTimer({ cookies }, entryId);
    } catch (/** @type {any} */ err) {
      return fail(400, { timeError: readableError(err, 'Could not stop the timer.') });
    }

    return { timeStopped: true };
  },

  /**
   * Log time that was worked without the timer running.
   *
   * The currency is the org's, from the JWT, not from the form: what an entry
   * is billed in is a fact about the org, and a client that could name it
   * could bill an hour in a currency nobody trades.
   */
  logTime: async ({ cookies, params, request, locals }) => {
    const form = await request.formData();
    const minutes = form.get('minutes')?.toString() ?? '';
    const description = form.get('description')?.toString() ?? '';
    const billable = form.get('billable') === 'on';
    const hourlyRate = form.get('hourly_rate')?.toString() ?? '';

    try {
      await logTicketTime({ cookies }, params.id, {
        minutes,
        description,
        billable,
        hourlyRate,
        currency: /** @type {any} */ (locals).org_settings?.default_currency ?? null
      });
    } catch (/** @type {any} */ err) {
      return fail(400, { timeError: readableError(err, 'Could not log this time.') });
    }

    return { timeLogged: true };
  },

  /** Flip one entry between billable and not. The next value comes from the
   *  form rather than being derived from what was rendered, so two clicks in
   *  quick succession cannot land on the same value twice. */
  setBillable: async ({ cookies, request }) => {
    const form = await request.formData();
    const entryId = form.get('entry_id')?.toString() ?? '';
    const billable = form.get('billable') === 'true';

    try {
      await setEntryBillable({ cookies }, entryId, billable);
    } catch (/** @type {any} */ err) {
      return fail(400, { timeError: readableError(err, 'Could not change this entry.') });
    }

    return { timeUpdated: true };
  },

  /** Delete an entry. Refused by the API once the entry has been invoiced,
   *  and that message is worth showing as it is: it says what to undo first. */
  deleteTime: async ({ cookies, request }) => {
    const form = await request.formData();
    const entryId = form.get('entry_id')?.toString() ?? '';

    try {
      await deleteEntry({ cookies }, entryId);
    } catch (/** @type {any} */ err) {
      return fail(400, { timeError: readableError(err, 'Could not delete this entry.') });
    }

    return { timeDeleted: true };
  }
};
