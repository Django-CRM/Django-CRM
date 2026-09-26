/**
 * Tasks: the seventh v2 module wired to the real API.
 *
 * Same rules as the six before it: this file lives under `$lib/server` because
 * SvelteKit refuses to bundle that directory into client code and the access
 * token is an httpOnly cookie. The org is never a parameter; it is a claim
 * inside the JWT and the backend reads it from there.
 *
 * WHY THIS MODULE, AND WHY NOW
 * The contact page already renders a **Tasks** rail from real rows. It has
 * since contacts were wired, and every one of those rows was a dead end,
 * because there was no task page to link to. That is a quieter failure than
 * the ticket rail that pointed at a 404 last session: nothing was broken,
 * there was simply nowhere to go. It also turned out to be the module whose
 * three red tests nobody had read.
 *
 * WHAT CHANGED WHEN THE FIXTURES CAME OFF
 * - **A task cannot be attached to an invoice.** The mock had one, `related:
 *   { kind: 'invoice', … }` linking to `/invoices/INV-2025-0142`. `Task`
 *   has four optional parents: account, opportunity, case, lead. There is no
 *   invoice FK and no endpoint that would populate one, so that kind is gone.
 * - **"Done this week" is not computable.** It was the fourth stat card.
 *   `Task` has no `completed_at` column, only `BoardTask` does, so nothing
 *   records *when* a task was finished, and a card counting it would have been
 *   inventing the number. In its place: **no due date**, which is real, and is
 *   the count that matters most, because a task with no due date is never
 *   overdue and never due this week and so never appears in front of anyone.
 * - `assigned_to` is a list of resolved display names in the mock. It is M2M
 *   on the model and stays a list here; `toRow` keeps ids alongside names, so
 *   an edit can preserve co-assignees instead of dropping them.
 * - `related` is derived here, once, from the four nullable columns, the way
 *   the mock promised the API layer would. Nothing in the UI branches on which
 *   column was populated.
 *
 * WHAT THE FIXTURE GOT RIGHT
 * That the row itself is the work. Every other list in this app is a set of
 * links to somewhere you do the thing; a task list is where you do it. The
 * per-row tick is a real PATCH now, and the mock's own note asked for exactly
 * that: "what it must never become is a tick that looks saved and is not."
 */
import { error } from '@sveltejs/kit';
import { apiRequest } from '$lib/api-helpers.js';
import { attachmentHref } from '$lib/server/v2/files.js';

/** `Task.STATUS_CHOICES`. Note `Task` priority is Low/Medium/High. A Case's is not. */
export const TASK_STATUSES = ['New', 'In Progress', 'Completed'];
export const TASK_PRIORITIES = ['Low', 'Medium', 'High'];

/** See the note on FILTER_FIELDS in tickets.js. */
export const FILTER_FIELDS = [
  'assigned_to',
  'priority',
  'status',
  'due_date__gte',
  'due_date__lte'
];

/** The four parent columns, and where each one's record lives in v2. */
const PARENTS = [
  { field: 'account', kind: 'account', href: (/** @type {string} */ id) => `/accounts/${id}` },
  {
    field: 'opportunity',
    kind: 'deal',
    href: (/** @type {string} */ id) => `/pipeline/${id}`
  },
  { field: 'case', kind: 'ticket', href: (/** @type {string} */ id) => `/tickets/${id}` },
  { field: 'lead', kind: 'lead', href: (/** @type {string} */ id) => `/leads/${id}` }
];

/**
 * The one parent a task has, as a single thing.
 *
 * The model allows exactly one and now enforces it on every path, so the first
 * match is the answer rather than a guess. All four destinations are wired
 * modules, which is the only reason this can be a link at all.
 *
 * @param {any} row
 */
function toRelated(row) {
  for (const parent of PARENTS) {
    const value = row[parent.field];
    if (value && value.id) {
      return {
        kind: parent.kind,
        id: value.id,
        name: value.name || '(unnamed)',
        href: parent.href(value.id)
      };
    }
  }
  return null;
}

/**
 * A profile as the pickers and avatars want it.
 *
 * @param {any} profile
 */
function toPerson(profile) {
  const user = profile?.user_details ?? profile?.user ?? {};
  return {
    id: profile?.id ?? '',
    name: user.name || user.email || profile?.user__email || '',
    email: user.email || profile?.user__email || ''
  };
}

/**
 * The subset of a Django task the v2 pages read.
 *
 * @param {any} row
 */
function toRow(row) {
  const assigned = (row.assigned_to ?? []).map(toPerson);
  return {
    id: row.id,
    title: row.title ?? '',
    status: row.status,
    priority: row.priority,
    due_date: row.due_date ?? null,
    description: row.description ?? '',
    related: toRelated(row),
    assigned_to: assigned,
    // The list rows only ever render names; keeping both shapes here saves
    // every template from reaching through an object to get one.
    assigned_names: assigned.map((person) => person.name).filter(Boolean),
    assigned_ids: assigned.map((person) => person.id),
    tags: (row.tags ?? []).map((/** @type {any} */ tag) => tag.name ?? tag),
    created_at: row.created_at,
    // Derived once rather than in four templates. "Completed" is the only
    // status that means finished, and a finished task is never late.
    is_done: row.status === 'Completed'
  };
}

/**
 * The task list, newest first.
 *
 * `totals` is counted by the API over the whole filtered queryset and is the
 * *requester's*: an admin's numbers cover the org, a member's cover the tasks
 * they made or were handed. That is the same rule the rows follow, which is
 * the point: a header that counted more than the list can show is how the old
 * queue came to disagree with itself.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {URLSearchParams} [params]
 */
export async function listTasks({ cookies }, params) {
  const query = new URLSearchParams(params ?? undefined);
  if (!query.has('limit')) query.set('limit', '50');
  // The account and contact catalogues are for the *form*. A list page that
  // pulls them costs 120 KB it never renders.
  if (!query.has('slim')) query.set('slim', 'true');

  const response = await apiRequest(`/tasks/?${query}`, {}, { cookies });
  const rows = (response.tasks ?? []).map(toRow);
  const totals = response.totals ?? {};

  return {
    results: rows,
    totals: {
      count: totals.count ?? rows.length,
      open: totals.open ?? 0,
      overdue: totals.overdue ?? 0,
      due_today: totals.due_today ?? 0,
      due_this_week: totals.due_this_week ?? 0,
      no_due_date: totals.no_due_date ?? 0,
      unassigned: totals.unassigned ?? 0,
      shown: rows.length,
      matched: response.tasks_count ?? rows.length
    },
    owners: (response.users ?? []).map(toPerson)
  };
}

/**
 * Everything a task form needs to render its pickers.
 *
 * One call, because the list endpoint already computes all of it, the
 * catalogues, the assignable profiles and the two choice lists. Dropping
 * `slim` is the whole difference.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 */
export async function getTaskFormOptions({ cookies }) {
  const response = await apiRequest('/tasks/?limit=1', {}, { cookies });
  return {
    owners: (response.users ?? []).map(toPerson),
    accounts: (response.accounts_list ?? []).map((/** @type {any} */ row) => ({
      id: row.id,
      name: row.name ?? ''
    })),
    // `ContactPickerSerializer`: the id and the name, no email.
    contacts: (response.contacts_list ?? []).map((/** @type {any} */ row) => ({
      id: row.id,
      name: [row.first_name, row.last_name].filter(Boolean).join(' ')
    }))
  };
}

/**
 * One task, with its comments.
 *
 * A 404 here covers three different things on purpose, no such id, a
 * malformed id, and a task belonging to another org, because distinguishing
 * them out loud would confirm which ids exist.
 *
 * The 403 carries no message: `routes/v2/+error.svelte` writes its own copy
 * for that status and ignores whatever is passed, deliberately, so that the
 * page never says anything about the record. Passing a sentence that is thrown
 * away would only mislead whoever reads this next.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function getTask({ cookies }, id) {
  /** @type {any} */
  let response;
  try {
    response = await apiRequest(`/tasks/${id}/`, {}, { cookies });
  } catch (/** @type {any} */ err) {
    if (err?.status === 404) {
      error(404, 'That task does not exist, or it belongs to another team.');
    }
    if (err?.status === 403) error(403);
    throw err;
  }

  const task = toRow(response.task_obj ?? {});
  return {
    task,
    // `ContactLinkSerializer`: id, first_name, last_name and email.
    contacts: (response.task_obj?.contacts ?? []).map((/** @type {any} */ row) => ({
      id: row.id,
      name: [row.first_name, row.last_name].filter(Boolean).join(' ') || row.email || '',
      email: row.email ?? ''
    })),
    activity: buildTaskActivity(response),
    // Who this task could be handed to. The API narrows it to admins for a
    // non-admin, which is the same list the v1 detail page used.
    owners: (response.users ?? []).map(toPerson)
  };
}

/**
 * The task's real events, newest first: comments, files, and creation.
 *
 * Three event KINDS, all backed by rows that exist. A comment is a `Comment`,
 * a file is an `Attachments`, and the task always has its own creation. The old
 * shape read `response.comments` alone, so every file uploaded against a task
 * was dropped from the page it should have appeared on (`TaskDetailView.get`
 * returns `attachments` right beside `comments`). Nothing records status
 * changes on a `Task`, there is no history table and no `completed_at`, so
 * those are not invented here, the same discipline as `leads.js`.
 *
 * A comment's author comes from the nested `commented_by`; an attachment's
 * `created_by` is a bare user id, so a file is left unattributed rather than
 * printing an id.
 *
 * @param {any} response
 */
function buildTaskActivity(response) {
  /** @type {Array<{id:string,type:'comment'|'file'|'status',at:string,by:string|null,body:string,href?:string|null}>} */
  const events = (response.comments ?? []).map((/** @type {any} */ c) => ({
    id: `comment-${c.id}`,
    type: 'comment',
    at: c.commented_on,
    by: toPerson(c.commented_by).name || null,
    body: c.comment ?? ''
  }));

  for (const a of response.attachments ?? []) {
    events.push({
      id: `file-${a.id}`,
      type: 'file',
      at: a.created_at,
      by: null,
      body: a.file_name || 'Attachment',
      href: attachmentHref(a.id)
    });
  }

  const task = response.task_obj;
  if (task?.created_at) {
    events.push({
      id: `created-${task.id}`,
      type: 'status',
      at: task.created_at,
      by: null,
      body: 'Task created'
    });
  }

  return events.sort(
    (/** @type {any} */ a, /** @type {any} */ b) =>
      new Date(b.at).getTime() - new Date(a.at).getTime()
  );
}

/**
 * Comment on a task, optionally with a file.
 *
 * `TaskDetailView.post` creates a `Comment` scoped to the task's org and stamps
 * `commented_by` from `request.profile`; the only field it reads from the body
 * is `comment`. It is gated by `assert_task_access`, the same gate as reading
 * the task, so this composer appears only to someone who could already open the
 * page, and the comment is attributed server-side, never by the client.
 *
 * The attachment save is a SEPARATE top-level block in the view (not nested
 * inside the comment check), so a file may ride with a comment or arrive on its
 * own. The field name is `task_attachment`.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {string} comment
 * @param {File|null} [file]
 */
export async function addTaskNote({ cookies }, id, comment, file = null) {
  if (file) {
    const form = new FormData();
    if (comment) form.set('comment', comment);
    form.set('task_attachment', file);
    return await apiRequest(`/tasks/${id}/`, { method: 'POST', body: form }, { cookies });
  }
  return await apiRequest(`/tasks/${id}/`, { method: 'POST', body: { comment } }, { cookies });
}

/** Scalar fields the task forms own. Everything else is server-derived. */
export const EDITABLE_FIELDS = [
  'title',
  'status',
  'priority',
  'due_date',
  'description',
  'account',
  'opportunity',
  'case',
  'lead',
  'assigned_to'
];

/**
 * Turn form values into a request body.
 *
 * Absent stays absent, which is what makes PATCH safe: a field the form did
 * not send is a field the save leaves alone. It matters twice on this model;
 * `assigned_to` is an M2M the API clears and rewrites whenever the key is
 * present, and the four parent columns are checked against each other, so
 * sending an untouched one back can trip the "one parent entity" rule.
 *
 * @param {Record<string, any>} values
 */
function toBody(values) {
  /** @type {Record<string, any>} */
  const body = {};
  for (const field of EDITABLE_FIELDS) {
    if (!(field in values)) continue;
    body[field] = values[field];
  }
  return body;
}

/**
 * Save an edit.
 *
 * PATCH, not PUT, seven for seven, and here for two reasons at once. A PUT
 * clears `contacts`, `teams`, `assigned_to` and `tags` whenever the key is
 * missing, so a form that edits the title would silently unassign everybody;
 * and it would send all four parent columns on every save, which the
 * one-parent rule reads as an attempt to add a second one.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {Record<string, any>} values
 */
export async function updateTask({ cookies }, id, values) {
  return await apiRequest(`/tasks/${id}/`, { method: 'PATCH', body: toBody(values) }, { cookies });
}

/**
 * Write a new task.
 *
 * No `org` and no `created_by` in the body: `TaskListView.post` sets both from
 * `request.profile`, and `created_by` is now `read_only` on the serializer
 * because it decides who may delete the task.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {Record<string, any>} values
 */
export async function createTask({ cookies }, values) {
  return await apiRequest('/tasks/', { method: 'POST', body: toBody(values) }, { cookies });
}

/**
 * Tick a task off, or put it back.
 *
 * The narrowest possible PATCH, one field, because this fires from a list
 * row where nothing else on screen is being edited.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {boolean} done
 */
export async function setTaskDone({ cookies }, id, done) {
  return await apiRequest(
    `/tasks/${id}/`,
    { method: 'PATCH', body: { status: done ? 'Completed' : 'New' } },
    { cookies }
  );
}

/**
 * Delete a task.
 *
 * Admins and the creator only: an assignee gets a 403, which the page shows
 * rather than hiding the button and pretending the rule is about the UI.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function deleteTask({ cookies }, id) {
  return await apiRequest(`/tasks/${id}/`, { method: 'DELETE' }, { cookies });
}
