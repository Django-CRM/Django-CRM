import { fail, redirect } from '@sveltejs/kit';
import {
  getTicket,
  getTicketTree,
  closeTicketWithChildren,
  replyToTicket,
  updateTicket,
  getMergeTargets,
  mergeTicket,
  unmergeTicket,
  linkTicketParent,
  listTickets,
  getTicketWatchers,
  setWatching,
  suggestTicketArticles,
  setTicketArticle
} from '$lib/server/v2/tickets.js';
import {
  listUsableMacros,
  renderMacro,
  applyMacro,
  keptActions,
  applySummary
} from '$lib/server/v2/macros.js';
import { getOrgSettings } from '$lib/server/v2/organization.js';
import {
  listTicketTime,
  startTicketTimer,
  stopTimer,
  logTicketTime,
  setEntryBillable,
  deleteEntry
} from '$lib/server/v2/timesheet.js';
import {
  listTicketApprovals,
  requestApproval,
  approveApproval,
  rejectApproval,
  cancelApproval
} from '$lib/server/v2/approvals.js';
import { readableError } from '$lib/server/v2/form-errors.js';
import { openDescendants, subtreeTruncated, cascadedCount, closeResultMessage } from './close.js';
import { treeRows, subtreeIds, parentCandidates, linkRefusal } from './tree.js';

/**
 * The ticket, plus what closing it would take with it.
 *
 * The tree is fetched ONLY for a ticket in one (it has a parent or children),
 * and the org settings only for one with children, which is the only case a
 * close can cascade. Most tickets are in no tree, and extra requests on every
 * ticket open to answer a question that cannot arise are a cost paid for
 * nothing.
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
 * The ticket's approval requests come in the same wave and may fail the same
 * way (`null`, and the panel says so). The detail's `approvalRule` says
 * whether any rule gates closing it at all.
 *
 * Watch state (`watchers/`, which answers `is_current_user_watching`) rides
 * in the first wave. The composer's saved replies and the article
 * candidates are fetched only for someone who may reply, the rule that also
 * gates sending and linking; `?aq=` searches the candidates. All three may
 * fail without breaking the page.
 *
 * `?merge=1` opens the "Merge into..." picker and `&q=` searches it. The
 * access token is an httpOnly cookie, so the search is a GET that reloads
 * this page rather than a browser fetch, and the candidates come only from
 * `merge-targets/`. They are fetched only when the picker is open and the
 * API says this person may merge the ticket.
 *
 * `?link=1` (with `&lq=`) is the same arrangement for "Link under a parent":
 * candidates from the ticket list, which holds only tickets this person may
 * open, fetched only when the API says they may change this ticket
 * (`comment_permission`, the write rule `link/` takes).
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load({ cookies, params, locals, url }) {
  const [data, timeEntries, approvals, watchers] = await Promise.all([
    getTicket({ cookies }, params.id),
    listTicketTime({ cookies }, params.id).catch(() => null),
    listTicketApprovals({ cookies }, params.id).catch(() => null),
    getTicketWatchers({ cookies }, params.id).catch(() => null)
  ]);

  const merge = { open: false, q: '', targets: /** @type {any[]} */ ([]), error: '' };
  if (url.searchParams.has('merge') && data.canMerge) {
    merge.open = true;
    merge.q = (url.searchParams.get('q') ?? '').trim().slice(0, 200);
    try {
      merge.targets = await getMergeTargets({ cookies }, params.id, merge.q);
    } catch (/** @type {any} */ err) {
      merge.error = readableError(err, 'Could not load the tickets to merge into.');
    }
  }

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
  const hasChildren = Boolean(data.ticket?.child_count);
  const inTree = hasChildren || Boolean(data.ticket?.parent);

  const articleQuery = (url.searchParams.get('aq') ?? '').trim().slice(0, 200);
  const [tree, settings, macros, candidates] = await Promise.all([
    inTree ? getTicketTree({ cookies }, params.id).catch(() => null) : null,
    hasChildren ? getOrgSettings({ cookies }).catch(() => null) : null,
    data.canReply ? listUsableMacros({ cookies }).catch(() => []) : [],
    data.canReply
      ? suggestTicketArticles({ cookies }, params.id, articleQuery).catch(() => null)
      : null
  ]);
  const linkedIds = new Set(data.articles.map((/** @type {any} */ a) => a.id));
  const articlePicker = {
    q: articleQuery,
    // Null when the search failed; the card then says so.
    candidates: candidates?.filter((c) => !linkedIds.has(c.id)) ?? null
  };

  const link = { open: false, q: '', candidates: /** @type {any[]} */ ([]), error: '' };
  if (url.searchParams.has('link') && data.canReply) {
    link.open = true;
    link.q = (url.searchParams.get('lq') ?? '').trim().slice(0, 200);
    const query = new URLSearchParams({ limit: '20' });
    if (link.q) query.set('search', link.q);
    try {
      const { results } = await listTickets({ cookies }, query);
      link.candidates = parentCandidates(results, {
        exclude: subtreeIds(tree?.root, params.id),
        parentId: data.ticket.parent?.id ?? null
      });
    } catch (/** @type {any} */ err) {
      link.error = readableError(err, 'Could not load the tickets to link under.');
    }
  }

  // Null when the ticket is in no tree or the tree call failed; the page then
  // shows the parent row from the ticket itself and no child rows.
  const treeView = tree?.root
    ? { rows: treeRows(tree.root, params.id), rootId: tree.root.id }
    : null;

  const extras = { time, approvals, watchers, macros, articlePicker };
  if (!hasChildren) return { ...data, ...extras, merge, link, tree: treeView };

  return {
    ...data,
    ...extras,
    merge,
    link,
    tree: treeView,
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

/**
 * Every action a macro carries, applied to this ticket now. Shared by the
 * Apply button and a body-less macro sent through Insert without script.
 *
 * @param {import('@sveltejs/kit').Cookies} cookies
 * @param {string} macroId
 * @param {string} caseId
 */
async function applyNow(cookies, macroId, caseId) {
  try {
    const applied = await applyMacro({ cookies }, macroId, caseId, undefined);
    return { macroApplied: applySummary(applied) };
  } catch (/** @type {any} */ err) {
    return fail(400, { macroError: readableError(err, 'Could not apply this macro.') });
  }
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
   *
   * A picked macro's kept actions (`macro_id` plus one `macro_action` per
   * chip left on) apply last, the same way and for the same reason: the API
   * runs them through the ticket PATCH's gates, and a refusal there is
   * reported after the reply has gone. They are tried even when the status
   * change was refused. `sent: true` on those failures tells the composer the
   * reply is out, so it clears rather than inviting a second send.
   */
  reply: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const body = form.get('body')?.toString().trim() ?? '';
    const internal = form.get('internal') === 'on';
    const status = form.get('status')?.toString().trim() ?? '';
    const macroId = form.get('macro_id')?.toString() ?? '';
    const macroActions = macroId ? keptActions(form) : [];

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

    // The status change and the macro are independent follow-ups: a refused
    // status does not stop the macro from being tried, and every part that
    // failed is named in the one message.
    /** @type {string[]} */
    const failed = [];
    if (status) {
      try {
        await updateTicket({ cookies }, params.id, { status });
      } catch (/** @type {any} */ err) {
        failed.push(`the status stayed put: ${readableError(err, 'the server gave no reason.')}`);
      }
    }

    /** @type {string | undefined} */
    let macroNote;
    if (macroActions.length) {
      try {
        macroNote = applySummary(await applyMacro({ cookies }, macroId, params.id, macroActions));
      } catch (/** @type {any} */ err) {
        failed.push(
          `the macro's actions were not applied: ${readableError(err, 'the server gave no reason.')}`
        );
      }
    }

    if (failed.length) {
      return fail(400, {
        sent: true,
        macroNote,
        error: `Reply posted, but ${failed.join(' Also, ')}`
      });
    }
    return macroNote ? { sent: true, internal, macroNote } : { sent: true, internal };
  },

  /**
   * Move the ticket without saying anything.
   *
   * A close sends no `closed_on`: the API dates it today in the org's
   * timezone, which it knows and this server does not. Where an approval rule
   * covers the ticket, the API refuses and says which rule.
   */
  setStatus: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const status = form.get('status')?.toString().trim() ?? '';
    if (!status) return fail(400, { error: 'No status was chosen.' });

    try {
      await updateTicket({ cookies }, params.id, { status });
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
   * children took the heavier path for no reason. Both take the same approval
   * gate; this one also refuses the whole cascade (nothing closed) when any
   * ticket it would take is not the viewer's to close, and the refusal lands
   * in `form.error` like any other.
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

  /**
   * Merge this ticket into another. The source is always this page's ticket
   * (`params.id`); only the target comes from the form, and the API checks
   * that this person may merge both. On success the page goes to the target,
   * since this ticket's own URL now redirects there anyway.
   */
  merge: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const into = form.get('into')?.toString() ?? '';
    if (!into) return fail(400, { error: 'Pick a ticket to merge into.' });

    let result;
    try {
      result = await mergeTicket({ cookies }, params.id, into);
    } catch (/** @type {any} */ err) {
      return fail(err?.status === 403 ? 403 : 400, {
        error: readableError(err, 'Could not merge this ticket.')
      });
    }

    // The target the API merged into, not the form's value echoed back.
    redirect(303, `/tickets/${result?.target_case?.id ?? into}`);
  },

  /**
   * Undo a merge into this ticket. The source id comes from the form (the
   * "Merged from" row); the API checks this person may merge both tickets
   * again before moving anything back.
   */
  unmerge: async ({ cookies, request }) => {
    const form = await request.formData();
    const sourceId = form.get('source_id')?.toString() ?? '';
    if (!sourceId) return fail(400, { error: 'No merged ticket was chosen.' });

    let result;
    try {
      result = await unmergeTicket({ cookies }, sourceId);
    } catch (/** @type {any} */ err) {
      return fail(err?.status === 403 ? 403 : 400, {
        error: readableError(err, 'Could not unmerge this ticket.')
      });
    }

    const name = result?.source_case?.name;
    return { unmerged: name ? `Unmerged "${name}".` : 'Unmerged.' };
  },

  /**
   * Put this ticket under another one. The parent comes from the picker's
   * form; the API takes the write rule on this ticket and the read rule on the
   * parent, and refuses cycles, merged tickets and trees deeper than three.
   * On success the page reloads without `?link=1`, which closes the picker.
   */
  linkParent: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const parentId = form.get('parent_id')?.toString() ?? '';
    if (!parentId) return fail(400, { error: 'Pick a ticket to link this one under.' });

    try {
      await linkTicketParent({ cookies }, params.id, parentId);
    } catch (/** @type {any} */ err) {
      return fail(err?.status === 403 ? 403 : 400, {
        error: linkRefusal(err, 'Could not link this ticket.')
      });
    }

    redirect(303, `/tickets/${params.id}`);
  },

  /** Take this ticket out from under its parent. Needs write on this ticket
   *  only, so it works even when the parent is one the viewer cannot open. */
  detachParent: async ({ cookies, params }) => {
    try {
      await linkTicketParent({ cookies }, params.id, null);
    } catch (/** @type {any} */ err) {
      return fail(err?.status === 403 ? 403 : 400, {
        error: linkRefusal(err, 'Could not detach this ticket.')
      });
    }

    return { detached: 'Detached from its parent ticket.' };
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
  },

  /** Start watching. Anyone who may open the ticket may; the API decides. */
  watch: async ({ cookies, params }) => {
    try {
      await setWatching({ cookies }, params.id, true);
    } catch (/** @type {any} */ err) {
      return fail(400, { error: readableError(err, 'Could not watch this ticket.') });
    }
    return { watching: true };
  },

  /** Stop watching. */
  unwatch: async ({ cookies, params }) => {
    try {
      await setWatching({ cookies }, params.id, false);
    } catch (/** @type {any} */ err) {
      return fail(400, { error: readableError(err, 'Could not stop watching this ticket.') });
    }
    return { watching: false };
  },

  /** Link a knowledge-base article. The ticket's write rule, on the server. */
  linkArticle: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const articleId = form.get('article_id')?.toString() ?? '';
    if (!articleId) return fail(400, { articleError: 'Which article? None was given.' });
    try {
      await setTicketArticle({ cookies }, params.id, articleId, true);
    } catch (/** @type {any} */ err) {
      return fail(400, { articleError: readableError(err, 'Could not link this article.') });
    }
    return { articleLinked: true };
  },

  /** Unlink an article. Same rule as linking. */
  unlinkArticle: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const articleId = form.get('article_id')?.toString() ?? '';
    if (!articleId) return fail(400, { articleError: 'Which article? None was given.' });
    try {
      await setTicketArticle({ cookies }, params.id, articleId, false);
    } catch (/** @type {any} */ err) {
      return fail(400, { articleError: readableError(err, 'Could not unlink this article.') });
    }
    return { articleUnlinked: true };
  },

  /**
   * Expand a saved reply against this ticket and hand the text back to the
   * composer. Nothing is sent: the text lands in the reply box, and the
   * person sends it through `reply` like anything else they typed.
   *
   * Without script the picker only ever shows Insert, because which button
   * a macro gets is decided in the browser. So a macro with no text that
   * arrives here is applied instead, exactly as Apply would. Whether it has
   * text comes from the API's own list, never from the form; if that list
   * cannot be read, the render goes ahead as before.
   */
  renderMacro: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const macroId = form.get('macro_id')?.toString() ?? '';
    if (!macroId) return fail(400, { macroError: 'Pick a saved reply first.' });
    const usable = await listUsableMacros({ cookies }).catch(() => null);
    if (usable?.find((m) => m.id === macroId)?.has_body === false) {
      return applyNow(cookies, macroId, params.id);
    }
    try {
      return { macroText: await renderMacro({ cookies }, macroId, params.id), macroId };
    } catch (/** @type {any} */ err) {
      return fail(400, { macroError: readableError(err, 'Could not insert this saved reply.') });
    }
  },

  /**
   * Apply a macro that has no text, at once. With nothing to insert there is
   * no reply to wait for, so its actions are the whole of it. The API checks
   * the ticket's write rule and runs the PATCH's gates.
   */
  applyMacro: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const macroId = form.get('macro_id')?.toString() ?? '';
    if (!macroId) return fail(400, { macroError: 'Pick a saved reply first.' });
    return applyNow(cookies, macroId, params.id);
  },

  /**
   * Ask for the approval that closing this ticket needs. The API binds the
   * request to the rule gating the ticket, and refuses it (400 no rule, 403 no
   * write access, 409 one already pending) with a sentence that is shown as
   * it is. Errors land in `approvalError`, inside the panel, rather than at
   * the top of a long page.
   */
  requestApproval: async ({ cookies, params, request }) => {
    const form = await request.formData();
    const note = form.get('note')?.toString().trim() ?? '';
    try {
      await requestApproval({ cookies }, params.id, note);
    } catch (/** @type {any} */ err) {
      return fail(400, { approvalError: readableError(err, 'Could not request approval.') });
    }
    return { approvalRequested: true };
  },

  /** Approve a request on this ticket. The approver pool and the rule that a
   *  requester cannot decide their own request are the API's. */
  approveApproval: async ({ cookies, request }) => {
    const form = await request.formData();
    const id = form.get('approval_id')?.toString() ?? '';
    if (!id) return fail(400, { approvalError: 'Which approval? None was given.' });
    try {
      await approveApproval({ cookies }, id, '');
    } catch (/** @type {any} */ err) {
      return fail(400, { approvalError: readableError(err, 'Could not approve this request.') });
    }
    return { approvalDecided: 'approved' };
  },

  /** Reject a request. A reason is required here and by the API. */
  rejectApproval: async ({ cookies, request }) => {
    const form = await request.formData();
    const id = form.get('approval_id')?.toString() ?? '';
    const reason = form.get('reason')?.toString().trim() ?? '';
    if (!id) return fail(400, { approvalError: 'Which approval? None was given.' });
    if (!reason) return fail(400, { approvalError: 'A rejection needs a reason.' });
    try {
      await rejectApproval({ cookies }, id, reason);
    } catch (/** @type {any} */ err) {
      return fail(400, { approvalError: readableError(err, 'Could not reject this request.') });
    }
    return { approvalDecided: 'rejected' };
  },

  /** Withdraw a pending request. The API allows its requester or an admin. */
  withdrawApproval: async ({ cookies, request }) => {
    const form = await request.formData();
    const id = form.get('approval_id')?.toString() ?? '';
    if (!id) return fail(400, { approvalError: 'Which approval? None was given.' });
    try {
      await cancelApproval({ cookies }, id);
    } catch (/** @type {any} */ err) {
      return fail(400, { approvalError: readableError(err, 'Could not withdraw this request.') });
    }
    return { approvalDecided: 'cancelled' };
  }
};
