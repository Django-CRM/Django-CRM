/**
 * Put a rendered saved reply into the composer at the caret, replacing any
 * selection, the way the phone does (`_pickMacro` in the mobile ticket
 * screen). Only the text box changes; sending is still the ordinary reply.
 *
 * A caret the browser did not report (null) means the end of the text.
 *
 * @param {string} text what the box holds now
 * @param {number | null} start selection start
 * @param {number | null} end selection end
 * @param {string} insert the rendered reply
 * @returns {{ text: string, caret: number }}
 */
export function insertAtCaret(text, start, end, insert) {
  const from = clamp(start ?? text.length, text.length);
  const to = Math.max(from, clamp(end ?? from, text.length));
  return { text: text.slice(0, from) + insert + text.slice(to), caret: from + insert.length };
}

/** @param {number} n @param {number} max */
function clamp(n, max) {
  return Math.min(Math.max(0, n), max);
}
