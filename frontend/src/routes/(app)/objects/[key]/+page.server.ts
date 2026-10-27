import { error } from '@sveltejs/kit';
import {
  checkKey,
  getObject,
  listRecords,
  mayMassImport,
  OBJECT_GONE
} from '#lib/server/v2/custom-objects.js';
import { forwardCsvImport } from '#lib/server/v2/csv-import.js';
import { displayValue } from '#lib/server/v2/record-fields.js';
import { getOrgPeopleAndTeams, resolveMe } from '#lib/server/v2/org-people.js';
import { objectListQuery } from '#lib/server/v2/object-list.js';
import { loadSavedViews, savedViewActions } from '#lib/server/v2/saved-views.js';
import { getTags } from '#lib/server/v2/tags.js';
import { objectSavedViewSpec } from '#lib/v2/object-filters.js';
import { isOrgAdmin } from '#lib/admin.js';
import type { Actions, PageServerLoad } from './$types';

const LIST_COLUMNS = 4;

export const load: PageServerLoad = async ({ cookies, params, url, locals }) => {
  const key = checkKey(params.key);
  const asked = Number(url.searchParams.get('page'));
  const pageNumber = Number.isInteger(asked) && asked >= 1 ? asked : 1;

  const definition = await getObject({ cookies }, key);
  if (definition.is_active === false) error(404, OBJECT_GONE);
  const columns = (definition.fields ?? [])
    .filter((f) => f.field_type !== 'link')
    .slice(0, LIST_COLUMNS);

  const filters = objectListQuery(url, definition.fields);
  const q = filters.get('q') ?? '';

  const [{ count, results }, orgPeople, tagList, savedViews, canImport] = await Promise.all([
    listRecords({ cookies }, key, { page: pageNumber, filters }),
    getOrgPeopleAndTeams(cookies),
    // A failed tag fetch costs the Tag filter, not the list.
    getTags({ cookies }).catch(() => ({ tags: [] })),
    loadSavedViews({ cookies, url }, `obj:${key}`, objectSavedViewSpec(definition.fields)),
    mayMassImport({ cookies }, isOrgAdmin(locals.profile))
  ]);
  return {
    definition,
    hasBoard: definition.board_field !== '',
    columns: columns.map((f) => ({ key: f.key, label: f.label })),
    page: {
      count,
      results: results.map((r) => ({
        id: r.id,
        name: r.name,
        cells: columns.map((f) => displayValue(f, r.custom_fields?.[f.key])),
        assignees: (r.assigned_to ?? []).map((p) => p.name || p.email),
        tags: (r.tags ?? []).map((t) => t.name)
      }))
    },
    people: orgPeople.people,
    tags: tagList.tags ?? [],
    savedViews,
    canImport,
    meId: resolveMe(orgPeople.people, locals.user?.email),
    q,
    pageNumber
  };
};

/** The saved-views menu and the import dialog's two steps, for this object's list. */
export const actions = {
  ...savedViewActions(async (event) => {
    const key = checkKey(event.params.key ?? '');
    const definition = await getObject(event, key);
    return { module: `obj:${key}`, spec: objectSavedViewSpec(definition.fields) };
  }),
  importPreview: (event) =>
    forwardCsvImport(
      event,
      `/objects/${checkKey(event.params.key)}/records/import/preview/`,
      'importPreview'
    ),
  importCommit: (event) =>
    forwardCsvImport(
      event,
      `/objects/${checkKey(event.params.key)}/records/import/commit/`,
      'importCommit'
    )
} satisfies Actions;
