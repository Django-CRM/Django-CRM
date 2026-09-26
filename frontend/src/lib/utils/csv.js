/**
 * CSV files the browser builds itself, such as the import drawers' errors file.
 *
 * The same two rules as the server's exports (`backend/common/csv_export.py`)
 * and the mobile import sheet (`csvImportErrorsCsv`):
 *
 * - **A cell a spreadsheet would run as a formula is prefixed with `'`.** A
 *   cell starting with `=`, `+`, `-`, `@`, a tab or a carriage return, also
 *   after leading spaces, is a formula to Excel. An import error can quote the
 *   value it refused, so a row holding `=HYPERLINK(...)` would otherwise run on
 *   the machine of whoever opens the file.
 * - **A cell holding a quote, comma or line break is quoted, quotes doubled**,
 *   so a message with a comma in it stays one cell.
 */

const FORMULA_START = ['=', '+', '-', '@', '\t', '\r'];

/** @param {unknown} value */
export function csvCell(value) {
  let text = value === null || value === undefined ? '' : String(value);
  const trimmed = text.trimStart();
  if (FORMULA_START.some((c) => text.startsWith(c) || trimmed.startsWith(c))) {
    text = `'${text}`;
  }
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

/**
 * An import's row errors as the "Download errors" file: `row,field,message`.
 *
 * @param {{ row: number, field: string, message: string }[]} errors
 */
export function importErrorsCsv(errors) {
  return [['row', 'field', 'message'], ...errors.map((e) => [e.row, e.field, e.message])]
    .map((cells) => cells.map(csvCell).join(','))
    .join('\n');
}

/**
 * Save `errors` as `<plural>-import-errors.csv`. Does nothing for none.
 *
 * @param {{ row: number, field: string, message: string }[]} errors
 * @param {string} plural e.g. `leads`
 */
export function downloadImportErrors(errors, plural) {
  if (!errors?.length) return;
  const blob = new Blob([importErrorsCsv(errors)], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${plural}-import-errors.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
