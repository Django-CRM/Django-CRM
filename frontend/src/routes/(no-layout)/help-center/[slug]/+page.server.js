/**
 * An org's public help center: its published articles, searchable.
 *
 * Anonymous and indexable. Which articles exist is decided entirely by the API
 * from the slug; nothing this page sends can widen it.
 */

import { canonicalSlug, getHelpCenter, pageNumber, PAGE_SIZE } from '$lib/server/help-center.js';

/** @type {import('./$types').PageServerLoad} */
export async function load(event) {
  const slug = canonicalSlug(event.params.slug, event.url);
  const q = (event.url.searchParams.get('q') || '').trim().slice(0, 200);
  const page = pageNumber(event.url.searchParams.get('page'));
  const data = await getHelpCenter(event, slug, { q, page });
  const base = `${event.url.origin}/help-center/${slug}`;
  return {
    slug,
    canonical: page > 1 ? `${base}?page=${page}` : base,
    q,
    page,
    pages: Math.max(1, Math.ceil(data.count / PAGE_SIZE)),
    name: data.name,
    articles: data.articles,
    count: data.count
  };
}
