import { fail } from '@sveltejs/kit';
import { listTasks, setTaskDone, FILTER_FIELDS } from '$lib/server/v2/tasks.js';
import { readFilters, buildFilterQuery } from '$lib/server/v2/filter-params.js';
import { getOrgPeopleAndTeams, resolveMe } from '$lib/server/v2/org-people.js';
import { isOrgAdmin } from '$lib/admin.js';

/**
 * The task queue.
 *
 * `open` is the default view because a task list is a to-do list: the finished
 * ones are history, and history belongs behind a filter rather than at the top
 * of the thing you work from. `?all=1` shows everything, which is the URL the
 * "Show completed" control points at.
 *
 * @type {import('./$types').PageServerLoad}
 */
export async function load(event) {
  const { url, locals } = event;
  const showAll = url.searchParams.get('all') === '1';

  const filters = readFilters(url, 'tasks');
  const params = buildFilterQuery(FILTER_FIELDS, filters);
  const search = url.searchParams.get('q');
  if (search) params.set('search', search);

  const [{ results, totals, owners }, orgPeople] = await Promise.all([
    listTasks(event, params),
    getOrgPeopleAndTeams(event.cookies)
  ]);

  // `all=1` and an explicit `?status=` both mean the viewer asked for something
  // other than the to-do list, so the open-only strip stands down for either.
  // Without this, `status=Completed` fetches the completed tasks and then
  // drops all of them, and the page reads as "you have none".
  const explicitStatus = Boolean(filters.status);

  return {
    // Filtered here rather than with `?status=` alone, because the totals the
    // header reads have to cover both; "3 of 11 open" needs the 11.
    tasks: showAll || explicitStatus ? results : results.filter((task) => !task.is_done),
    totals,
    owners,
    showAll,
    people: orgPeople.people,
    meId: resolveMe(orgPeople.people, /** @type {any} */ (locals).user?.email),
    canDelete: isOrgAdmin(/** @type {any} */ (locals).profile)
  };
}

/** @type {import('./$types').Actions} */
export const actions = {
  /**
   * Tick a row off, or put it back.
   *
   * The mock did this in local state with a note saying it must never look
   * saved without being saved. This is that PATCH: the row reverts on failure
   * because the page reloads from the API either way.
   */
  toggle: async (event) => {
    const form = await event.request.formData();
    const id = form.get('id')?.toString();
    const done = form.get('done')?.toString() === 'true';
    if (!id) return fail(400, { error: 'Which task?' });

    try {
      await setTaskDone(event, id, done);
    } catch (/** @type {any} */ err) {
      // 403 is the interesting one: the list can show a task you may read
      // through a shared parent but not edit.
      return fail(err?.status === 403 ? 403 : 400, {
        error:
          err?.status === 403
            ? 'That task is not yours to change.'
            : (err?.body?.errors ?? 'That did not save. Try again.')
      });
    }
    return { done };
  }
};
