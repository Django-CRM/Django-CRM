/**
 * Closing a parent ticket, and what goes with it.
 *
 * `POST /cases/<id>/close-with-children/` closes the ticket and, when
 * `cascade` is true, every OPEN DESCENDANT of it. This module works out which
 * tickets those are, so the confirm step can name them before anything is
 * closed.
 *
 * **The tree endpoint does not return the ticket you are closing.** `GET
 * /cases/<id>/tree/` walks UP to the highest ancestor in the org and returns
 * that as `root`, with `focus_id` naming the ticket you asked about. So the
 * subtree that will actually be closed has to be found inside the response
 * first. A previous version of this feature (`CloseWithChildrenDialog.svelte`,
 * written, never wired to a button, deleted with this change) walked from
 * `root` and collected every open node except the focus. For a ticket that is
 * itself a child that listed its parent, its siblings and its cousins as
 * about to be closed, and none of them would have been. Overstating the blast
 * radius of a destructive action is worse than saying nothing.
 *
 * [openDescendants] mirrors `_open_descendants` in `cases/parent_views.py`
 * exactly, including the two things that are easy to get wrong:
 *
 * - a node counts only when it is open AND active, and
 * - recursion goes through closed nodes anyway, so an open grandchild under a
 *   closed child is still cascaded.
 */

import { RESTRICTED_TICKET_NAME } from '$lib/v2/enums.js';

/** A ticket closes to any status but this one. */
const CLOSED = 'Closed';

/**
 * What a ticket the viewer may not open is called in the list.
 *
 * `/tree/` redacts such a node to `{ id, name: null, restricted: true, status,
 * is_active }`: kept, so the count and the shape stay true, but nameless. The
 * close refuses the whole cascade when one of these would be closed, so the
 * list should say plainly that it is there.
 */
export const RESTRICTED_NAME = RESTRICTED_TICKET_NAME;

/**
 * The node for `id` inside the tree, or null.
 *
 * @param {any} root
 * @param {string} id
 */
export function findNode(root, id) {
  if (!root || !id) return null;
  if (root.id === id) return root;
  for (const child of root.children ?? []) {
    const hit = findNode(child, id);
    if (hit) return hit;
  }
  return null;
}

/**
 * The tickets a cascading close would actually close.
 *
 * @param {any} root the tree response's `root`
 * @param {string} id the ticket being closed
 * @returns {Array<{id: string, name: string, status: string, restricted: boolean}>}
 */
export function openDescendants(root, id) {
  const focus = findNode(root, id);
  if (!focus) return [];

  /** @type {Array<{id: string, name: string, status: string, restricted: boolean}>} */
  const out = [];
  /** @param {any} node */
  const walk = (node) => {
    for (const child of node.children ?? []) {
      // Open AND active, matching `_open_descendants`. An inactive row is a
      // merged duplicate and the backend skips it.
      if (child.status !== CLOSED && child.is_active !== false) {
        out.push({
          id: child.id,
          name: child.restricted ? RESTRICTED_NAME : child.name,
          status: child.status,
          restricted: Boolean(child.restricted)
        });
      }
      // Through closed children regardless: an open grandchild under a closed
      // child still cascades.
      walk(child);
    }
  };
  walk(focus);
  return out;
}

/**
 * True when the subtree hit the API's depth cap, so the list above is a floor
 * rather than the whole set.
 *
 * `_build_tree` stops at `Case.PARENT_MAX_DEPTH` and marks the node
 * `truncated`. The close itself has no such cap, so in that case more tickets
 * close than the confirm step can name, and it says so instead of implying a
 * complete list.
 *
 * @param {any} root
 * @param {string} id
 */
export function subtreeTruncated(root, id) {
  const focus = findNode(root, id);
  if (!focus) return false;
  /** @param {any} node @returns {boolean} */
  const walk = (node) =>
    Boolean(node.truncated) || (node.children ?? []).some((/** @type {any} */ c) => walk(c));
  return walk(focus);
}

/**
 * The line above the checkbox: what happens if it stays ticked.
 *
 * @param {{ count: number, truncated?: boolean }} args
 */
export function cascadeSummary({ count, truncated = false }) {
  if (count === 0) {
    return 'Nothing linked to this ticket is still open, so closing it changes nothing else.';
  }
  const noun = count === 1 ? 'ticket' : 'tickets';
  const tail = truncated
    ? ' There may be more further down than are listed here, and those close too.'
    : '';
  return `${count} linked ${noun} ${count === 1 ? 'is' : 'are'} still open and will be closed with it.${tail}`;
}

/**
 * What actually happened, from the response rather than from what was asked.
 *
 * The server returns `cascaded_case_ids`, the tickets it really closed. A
 * message built from the count on screen would claim a cascade that a
 * concurrent close had already made a no-op.
 *
 * @param {{ cascade: boolean, cascaded: number }} args
 */
export function closeResultMessage({ cascade, cascaded }) {
  if (!cascade) return 'Ticket closed.';
  if (cascaded === 0) {
    return 'Ticket closed. Nothing linked was open, so nothing else changed.';
  }
  const noun = cascaded === 1 ? 'ticket' : 'tickets';
  return `Ticket closed, and ${cascaded} linked ${noun} with it.`;
}

/**
 * How many tickets the response says were closed alongside.
 *
 * @param {any} body
 */
export function cascadedCount(body) {
  return (body?.cascaded_case_ids ?? []).length;
}
