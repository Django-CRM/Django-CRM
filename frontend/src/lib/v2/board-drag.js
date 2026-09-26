/**
 * Read-only cards on a drag-and-drop board.
 *
 * Every kanban card the API sends carries `can_move`, the server's own answer
 * to whether this viewer may move it (a ticket watcher, for one, can open a
 * ticket but not move it). A card that says false is drawn with `data-locked`
 * and offers no "Move to" menu; this module keeps it from being dragged.
 *
 * svelte-dnd-action has no per-item switch. It listens for `mousedown`,
 * `touchstart` and a Space or Enter `keydown` on every card in a zone, so the
 * zone runs `holdLockedCard` in the capture phase for those three events: an
 * event that starts inside a locked card is stopped before it reaches the
 * card's own listeners. Nothing is `preventDefault`ed, so the card's link
 * still opens. The server refuses the move either way; this only stops the
 * board from offering one it would refuse.
 */

/** The keys svelte-dnd-action starts a keyboard drag with. */
const DRAG_KEYS = new Set([' ', 'Enter']);

/**
 * Whether a card may be dragged. Anything but an explicit `true` is locked,
 * so a payload without the field offers no drag rather than one the server
 * may refuse.
 *
 * @param {{ can_move?: unknown }} row
 */
export function canMoveCard(row) {
  return row?.can_move === true;
}

/**
 * Capture-phase handler for a board zone's `mousedown`, `touchstart` and
 * `keydown`. Stops the event when it starts inside a `[data-locked]` card.
 *
 * @param {{ type: string, key?: string, target: any, stopPropagation: () => void }} event
 */
export function holdLockedCard(event) {
  if (event.type === 'keydown' && !DRAG_KEYS.has(event.key ?? '')) return;
  if (event.target?.closest?.('[data-locked]')) event.stopPropagation();
}
