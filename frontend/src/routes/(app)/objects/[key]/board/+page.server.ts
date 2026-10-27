import { error, redirect } from '@sveltejs/kit';
import {
  checkKey,
  getObject,
  OBJECT_GONE,
  type ObjectField
} from '#lib/server/v2/custom-objects.js';
import { getBoard, moveAction } from '#lib/server/v2/object-board.js';
import { displayValue } from '#lib/server/v2/record-fields.js';
import { getOrgPeopleAndTeams, resolveMe } from '#lib/server/v2/org-people.js';
import { objectListQuery } from '#lib/server/v2/object-list.js';
import { getTags } from '#lib/server/v2/tags.js';
import { boardFieldRequired, boardLanes, type BoardCard } from '#lib/v2/object-board.js';
import type { Actions, PageServerLoad } from './$types';

const CARD_FIELDS = 2;

type Shown = ObjectField[];

/** The card a lane draws; `canEdit` is the API's word on whether the viewer may move it. */
function toCard(shown: Shown, r: BoardCard) {
  return {
    id: r.id,
    name: r.name,
    cells: shown
      .map((f) => displayValue(f, r.custom_fields?.[f.key]))
      .filter((text) => text !== ''),
    assignees: (r.assigned_to ?? []).map((p) => p.name || p.email),
    canEdit: r.can_edit === true
  };
}

function shownFields(definition: { board_field: string; fields: ObjectField[] }): Shown {
  return (definition.fields ?? [])
    .filter((f) => f.field_type !== 'link' && f.key !== definition.board_field)
    .slice(0, CARD_FIELDS);
}

export const load: PageServerLoad = async ({ cookies, params, url, locals }) => {
  const key = checkKey(params.key);
  const definition = await getObject({ cookies }, key);
  if (definition.is_active === false) error(404, OBJECT_GONE);
  const boardField = definition.board_field;
  if (!boardField) redirect(303, `/objects/${key}`);

  const filters = objectListQuery(url, definition.fields);
  const [board, orgPeople, tagList] = await Promise.all([
    getBoard({ cookies }, key, filters),
    getOrgPeopleAndTeams(cookies),
    getTags({ cookies }).catch(() => ({ tags: [] }))
  ]);

  const shown = shownFields(definition);
  const lanes = boardLanes(board?.columns).map((lane) => ({
    ...lane,
    rows: lane.rows.map((r) => toCard(shown, r))
  }));

  return {
    definition,
    fieldLabel: board?.field?.label ?? boardField,
    required: boardFieldRequired(definition.fields, boardField),
    lanes,
    people: orgPeople.people,
    tags: tagList.tags ?? [],
    meId: resolveMe(orgPeople.people, locals.user?.email)
  };
};

export const actions = {
  /** A moved card comes back as the page draws it, with its current `can_edit`. */
  async move(event) {
    const result = await moveAction(event);
    if (!('record' in result)) return result;
    // The move is saved by now, so a failed read leaves the dropped card as drawn.
    const definition = await getObject(event, checkKey(event.params.key)).catch(() => null);
    if (!definition) return { success: true };
    return { success: true, card: toCard(shownFields(definition), result.record) };
  }
} satisfies Actions;
