/**
 * Knowledge base: the sixth v2 module wired to the real API.
 *
 * Same rules as the five before it: this file lives under `$lib/server`
 * because SvelteKit refuses to bundle that directory into client code and the
 * access token is an httpOnly cookie. The org is never a parameter; it is a
 * claim inside the JWT and the backend reads it from there.
 *
 * WHY THIS MODULE, AND WHY NOW
 * The ticket page shipped last with a rail of **linked articles**, each one an
 * `<a href="/solutions/<uuid>">` built from a real `Case.solutions` row.
 * Pointing at a page whose fixtures were keyed `'SOL-01'`. Every one of those
 * links was a 404 waiting for the first org that filed an article against a
 * ticket. The link crawl did not catch it because no seeded case has a linked
 * solution and no seeded org has an article at all: the knowledge base has
 * never been seeded, which is a good part of why nobody had looked at it.
 *
 * WHAT CHANGED WHEN THE FIXTURES CAME OFF
 * - `use_count` is `case_count`. The number of tickets the article is filed
 *   against. Same idea, and it is real; the mock just named it something the
 *   API does not have.
 * - `author` is new on the wire. `created_by` serialized as a bare `User`
 *   UUID, so the Author column in the mock was showing a name the endpoint
 *   could not have supplied.
 * - **"Related articles" was gone, and is back with a defined relation.** The
 *   mock listed three articles under that heading with no explanation of what
 *   related meant, and nothing computed it. Articles now carry tags, so the
 *   relation is "filed under the same tag", and the portal article page shows
 *   it. This agent page still lists the **tickets the article is filed
 *   against** instead, which is the relation an agent is actually chasing.
 * - The mock's new-article form said an article could be published while still
 *   a draft; "that is allowed, and sometimes right". It is not: the model's
 *   `publish()` refuses it and the serializer now refuses it on every path.
 *   The form says the rule instead of contradicting it.
 *
 * WHAT THE FIXTURE GOT RIGHT
 * That `status` and `is_published` are two different facts and collapsing them
 * into one switch loses the state worth surfacing. Approved but nobody has
 * released it. That row is the one stat card on the list page.
 */
import { error } from '@sveltejs/kit';
import { apiRequest } from '$lib/api-helpers.js';

/** `Solution.STATUS_CHOICES`, in workflow order. */
export const SOLUTION_STATUSES = ['draft', 'reviewed', 'approved'];

/**
 * Every filter param `listArticles` will forward. The descriptor in
 * `$lib/v2/filters.js` must not name a key absent from this list; a test in
 * `filters.test.js` enforces that. `visibility` is not here: it is a view
 * param the page's own load translates to `is_published` before the query
 * ever reaches this module, the same way contacts translates `inactive`.
 */
export const FILTER_FIELDS = ['status', 'tags'];

/**
 * The subset of a Django solution the v2 pages read.
 *
 * @param {any} row
 */
function toRow(row) {
  return {
    id: row.id,
    title: row.title ?? '',
    description: row.description ?? '',
    status: row.status,
    is_published: Boolean(row.is_published),
    // Tickets this article is filed against. The API's own count, and it is
    // the total. See `getArticle` for why that can exceed the rows shown.
    use_count: row.case_count ?? 0,
    author: row.author ?? '',
    // Agent-side only. The portal serializers do not carry tags at all, on
    // purpose: the vocabulary is shared with deals and reads like "At Risk".
    tags: (row.tags ?? []).map((/** @type {any} */ t) => ({ id: t.id, name: t.name })),
    created_at: row.created_at,
    updated_at: row.updated_at ?? row.created_at,
    // Derived here rather than in three templates: approved and unreleased is
    // the state the whole list page exists to surface.
    awaiting_release: row.status === 'approved' && !row.is_published
  };
}

/**
 * The knowledge base, most-recently-edited first.
 *
 * `totals` counts the whole base rather than the filtered page. The four
 * numbers are a partition ("12 articles, 3 of them drafts"), so recomputing
 * them inside a `?status=draft` filter would leave three of the cards reading
 * zero and saying nothing.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {URLSearchParams} [params]
 */
export async function listArticles({ cookies }, params) {
  const query = new URLSearchParams(params ?? undefined);
  if (!query.has('limit')) query.set('limit', '50');

  const response = await apiRequest(`/cases/solutions/?${query}`, {}, { cookies });
  const rows = (response.results ?? []).map(toRow);
  const totals = response.totals ?? {};

  return {
    results: rows,
    totals: {
      count: totals.count ?? rows.length,
      published: totals.published ?? 0,
      draft: totals.draft ?? 0,
      reviewed: totals.reviewed ?? 0,
      approved_unpublished: totals.approved_unpublished ?? 0,
      // How many came back on this page, which is not the same number once a
      // filter is on.
      shown: rows.length,
      matched: response.count ?? rows.length
    }
  };
}

/**
 * One article and the tickets it is filed against.
 *
 * `linked_cases` is filtered by the API to the tickets *this reader* may open,
 * while `case_count` stays the article's real usage. So the two can disagree,
 * on purpose, and the page says so rather than quietly showing a smaller
 * number to some people than to others.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 */
export async function getArticle({ cookies }, id) {
  /** @type {any} */
  let response;
  try {
    response = await apiRequest(`/cases/solutions/${id}/`, {}, { cookies });
  } catch (/** @type {any} */ err) {
    // On the status, not on the wording. Another org's article is a 404 and
    // not a 403, which would confirm the id exists.
    if (err?.status === 404) {
      error(404, 'That article does not exist, or it belongs to another team.');
    }
    throw err;
  }

  const article = toRow(response);
  const tickets = (response.linked_cases ?? []).map((/** @type {any} */ row) => ({
    id: row.id,
    name: row.name ?? '',
    status: row.status,
    priority: row.priority,
    opened_at: row.created_at
  }));

  return {
    article,
    tickets,
    // The gap between "filed against 4 tickets" and "here are 2 of them".
    hidden_ticket_count: Math.max(0, article.use_count - tickets.length)
  };
}

/**
 * Fields the article forms own. Everything else is server-derived.
 *
 * `tags` is the one non-scalar. It carries tag ids, and the API resolves them
 * against the caller's own org before attaching any, so an id from elsewhere
 * is dropped rather than linked. Sending it at all is optional: the API treats
 * an absent `tags` as "leave them alone" and only an explicit `[]` as "clear".
 */
export const EDITABLE_FIELDS = ['title', 'description', 'status', 'is_published', 'tags'];

/**
 * Turn form values into a request body.
 *
 * Absent stays absent. A field the form did not send is a field the save
 * leaves alone, which is what makes PATCH safe here, and it matters more than
 * usual on this model, because `status` and `is_published` are gated
 * separately from the article's text.
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
 * PATCH, not PUT. Six for six. Here the reason is the gate rather than a
 * relation: `SolutionDetailView` asks whether the request *moves* `status` or
 * `is_published` before demanding an admin, and a PUT sends both fields on
 * every save. An author fixing a typo on their own approved article would be
 * told they are not allowed to approve it.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {Record<string, any>} values
 */
export async function updateArticle({ cookies }, id, values) {
  return await apiRequest(
    `/cases/solutions/${id}/`,
    { method: 'PATCH', body: toBody(values) },
    { cookies }
  );
}

/**
 * Write a new article.
 *
 * No `org` and no `created_by` in the body: `SolutionListView.post` sets both
 * from `request.profile`.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {Record<string, any>} values
 */
export async function createArticle({ cookies }, values) {
  return await apiRequest(
    '/cases/solutions/',
    { method: 'POST', body: toBody(values) },
    { cookies }
  );
}

/**
 * Release an article to customers, or take it back.
 *
 * `is_published` gates two surfaces: the agent suggester in `cases/kb_views.py`
 * and, for an article that is also approved, the customer portal
 * (`PortalBaseView._published_articles`). Unpublishing withdraws both.
 *
 * Both are admin-only and the API says so with a 403. The UI hides the
 * buttons from everybody else, but that is a courtesy and not the control.
 *
 * @param {{ cookies: import('@sveltejs/kit').Cookies }} event
 * @param {string} id
 * @param {boolean} published
 */
export async function setPublished({ cookies }, id, published) {
  const verb = published ? 'publish' : 'unpublish';
  return await apiRequest(
    `/cases/solutions/${id}/${verb}/`,
    { method: 'POST', body: {} },
    { cookies }
  );
}
