/**
 * Parent and child tickets on a ticket's page (PARITY B3).
 *
 * The phone has had this on `ticket_detail_screen.dart` all along: a parent
 * row, the tree the ticket sits in, link under a parent, detach. The web had
 * two components for it that nothing imported. This module is the part of the
 * web version that is not markup, so it can be tested on its own, the way
 * `close.js` is.
 *
 * Everything the page decides here comes from the API:
 *
 * - The tree is `GET /cases/<id>/tree/`, which starts at the TOP of the tree,
 *   not at this ticket, and names `focus_id`. A node the viewer may not open
 *   arrives redacted (`name: null`, `restricted: true`) and stays in place, so
 *   a readable grandchild under it keeps its real depth.
 * - Whether link and detach are offered is `comment_permission` from the
 *   detail payload, the ticket's write rule, which is the rule `link/` takes.
 * - What may be picked as a parent is the list endpoint's answer, which is
 *   already only tickets this person may open.
 */

import { RESTRICTED_TICKET_NAME } from '$lib/v2/enums.js';
import { findNode } from './close.js';

/**
 * The tree as rows, top down, for an indented list.
 *
 * A restricted node keeps its status: `/tree/` sends it on purpose, and the
 * phone shows it too. It never gets a name or a link, because opening it would
 * only answer 404.
 *
 * @param {any} root the tree response's `root`
 * @param {string} focusId the ticket this page is about
 * @returns {Array<{id: string, name: string, status: string|null, depth: number,
 *   restricted: boolean, focus: boolean, truncated: boolean}>}
 */
export function treeRows(root, focusId) {
  /** @type {ReturnType<typeof treeRows>} */
  const rows = [];
  /** @param {any} node @param {number} depth */
  const walk = (node, depth) => {
    const restricted = Boolean(node.restricted);
    rows.push({
      id: node.id,
      name: restricted ? RESTRICTED_TICKET_NAME : (node.name ?? ''),
      status: node.status ?? null,
      depth,
      restricted,
      focus: node.id === focusId,
      truncated: Boolean(node.truncated)
    });
    for (const child of node.children ?? []) walk(child, depth + 1);
  };
  if (root) walk(root, 0);
  return rows;
}

/**
 * The ticket and everything under it: none of these may become its parent,
 * since that would make a cycle. The API refuses one anyway; this keeps the
 * picker from offering it.
 *
 * @param {any} root
 * @param {string} id
 * @returns {Set<string>}
 */
export function subtreeIds(root, id) {
  const ids = new Set([id]);
  /** @param {any} node */
  const walk = (node) => {
    for (const child of node.children ?? []) {
      ids.add(child.id);
      walk(child);
    }
  };
  const focus = findNode(root, id);
  if (focus) walk(focus);
  return ids;
}

/**
 * What the "Link under a parent" picker offers: list rows minus this ticket,
 * its own subtree, the parent it already has, and anything merged away (the
 * API refuses a merged parent).
 *
 * @param {Array<{id: string, status: string}>} rows from `listTickets`
 * @param {{ exclude: Set<string>, parentId?: string|null }} args
 */
export function parentCandidates(rows, { exclude, parentId = null }) {
  return rows.filter(
    (row) => !exclude.has(row.id) && row.id !== parentId && row.status !== 'Duplicate'
  );
}

/**
 * The sentence to show when `link/` refuses.
 *
 * Its refusals are `{"parent_id": "<sentence>"}` (hidden or missing parent,
 * self, cycle, depth, merged) or `{"detail": "<sentence>"}`, and `apiRequest`
 * would prefix the first with the key. The key means nothing to a person.
 *
 * @param {any} err the error thrown by `apiRequest`
 * @param {string} fallback
 */
export function linkRefusal(err, fallback) {
  const body = err?.body ?? {};
  for (const said of [body.parent_id, body.detail]) {
    const text = Array.isArray(said) ? said[0] : said;
    if (typeof text === 'string' && text.trim()) return text.trim();
  }
  return fallback;
}
