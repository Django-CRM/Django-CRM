/**
 * The board of a custom object: its columns, and which of them take a drop.
 * Browser-safe, so the page and vitest can both use it.
 */
import type { components } from '#lib/enterprise-api-schema.js';

type Column = components['schemas']['CustomRecordBoardColumn'];
export type BoardCard = components['schemas']['CustomRecordBoardCard'];

export type Lane<Row = BoardCard> = {
  id: string;
  value: string | null;
  label: string;
  kind: Column['kind'];
  count: number;
  truncated: boolean;
  rows: Row[];
};

/** Whether the board field is required, which closes the "No value" column to drops. */
export function boardFieldRequired(
  fields: { key: string; is_required?: boolean }[] | null | undefined,
  key: string
): boolean {
  return (fields ?? []).find((f) => f.key === key)?.is_required === true;
}

/** One lane per column, in the API's order. A missing column list is an empty board. */
export function boardLanes(columns: Column[] | null | undefined): Lane[] {
  return (columns ?? []).map((c) => {
    const rows = c.records ?? [];
    return {
      id: c.kind === 'option' ? `option:${c.value}` : c.kind,
      value: c.kind === 'option' ? c.value : null,
      label: c.label,
      kind: c.kind,
      count: c.count ?? rows.length,
      truncated: (c.count ?? rows.length) > rows.length,
      rows: [...rows]
    };
  });
}

/**
 * Whether a card may land in the lane. "Other values" is read-only, and "No
 * value" is closed when the field is required.
 */
export function acceptsDrops(lane: { kind: string }, required: boolean): boolean {
  return lane.kind === 'option' || (lane.kind === 'none' && !required);
}

/**
 * What a card offers. Only a card the viewer may edit can be dragged or sent
 * with "Move to"; a locked one stays a plain card that still opens its record.
 * `writable` is the object's own flag: without it no card moves.
 */
export function cardControls(
  card: { canEdit: boolean },
  writable = true
): { drag: boolean; moveSelect: boolean } {
  const may = writable && card.canEdit;
  return { drag: may, moveSelect: may };
}

/** The lanes a card can be sent to from the "Move to" menu, not counting its own. */
export function moveTargets<Row>(lanes: Lane<Row>[], required: boolean, fromId: string) {
  return lanes.filter((l) => l.id !== fromId && acceptsDrops(l, required));
}

/** The form fields the `move` action reads, for a card dropped in a lane. */
export function moveFields(
  lane: { kind: string; value: string | null },
  record: string,
  aboveId?: string,
  belowId?: string
): Record<string, string> {
  const fields: Record<string, string> = { record, kind: lane.kind };
  if (lane.kind === 'option' && lane.value !== null) fields.value = lane.value;
  if (aboveId) fields.above_id = aboveId;
  if (belowId) fields.below_id = belowId;
  return fields;
}

/** The active dropdown fields an admin may choose as the board. */
export function boardFieldOptions(
  fields:
    { key: string; label: string; field_type: string; is_active?: boolean }[] | null | undefined
): { value: string; label: string }[] {
  return (fields ?? [])
    .filter((f) => f.field_type === 'dropdown' && f.is_active !== false)
    .map((f) => ({ value: f.key, label: f.label }));
}
