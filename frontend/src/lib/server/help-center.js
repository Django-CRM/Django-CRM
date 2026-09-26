/**
 * Server-side calls to `/api/public/help/`, an org's public help center.
 *
 * Anonymous on purpose, and deliberately NOT built on `apiRequest`: that helper
 * attaches the visitor's `jwt_access` cookie, and a staff member browsing their
 * own org's public page must see exactly what a stranger sees. The API ignores
 * credentials there anyway; not sending one keeps the two facts in agreement.
 *
 * Every miss (no such address, a help center switched off, an unpublished or
 * foreign article) arrives from the API as one identical 404 and leaves here as
 * one identical SvelteKit 404, so a page cannot be used to probe which it was.
 */

import { error, redirect } from '@sveltejs/kit';
import { env } from '$env/dynamic/public';

const API_BASE_URL = `${env.PUBLIC_DJANGO_API_URL}/api/public/help`;

/** Articles per list page. Matches the API's default page size. */
export const PAGE_SIZE = 20;

/** The API's `max_limit`, used when the sitemap walks every article. */
const SITEMAP_PAGE_SIZE = 100;

/**
 * A sitemap file holds at most 50,000 URLs. Stopping well short of that also
 * bounds how many API calls one anonymous request can cause.
 */
const SITEMAP_MAX_PAGES = 50;

// The backend's slug rule (`validate_help_center_slug`), repeated here only so
// an address that could never exist is answered without a round trip. The API
// is still the authority: a well-formed slug nobody owns is its 404, not ours.
const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const notFound = () => error(404, 'Not found');

/**
 * The slug a request should be served under, or a 404/redirect.
 *
 * Slugs are stored lowercase and the API matches exactly, so `/help-center/Acme`
 * is sent to `/help-center/acme` with a permanent redirect rather than served
 * twice under two addresses.
 *
 * @param {string} slug
 * @param {URL} url
 */
export function canonicalSlug(slug, url) {
  const lower = slug.toLowerCase();
  if (lower.length < 3 || lower.length > 50 || !SLUG_RE.test(lower)) throw notFound();
  if (lower !== slug) {
    const rest = url.pathname.slice(`/help-center/${slug}`.length);
    throw redirect(301, `/help-center/${lower}${rest}${url.search}`);
  }
  return lower;
}

/**
 * The visitor's address, forwarded so the API's per-visitor throttle buckets
 * the visitor and not this server. Informational only: the API never makes an
 * authorization decision on it.
 *
 * @param {{ request: Request, getClientAddress: () => string }} event
 */
function forwardedFor(event) {
  const upstream = event.request.headers.get('x-forwarded-for');
  if (upstream) return upstream;
  try {
    return event.getClientAddress();
  } catch {
    return '';
  }
}

/**
 * @param {{ request: Request, getClientAddress: () => string }} event
 * @param {string} path
 */
async function call(event, path) {
  const ip = forwardedFor(event);
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { Accept: 'application/json', ...(ip ? { 'X-Forwarded-For': ip } : {}) }
  });
  if (response.status === 404) throw notFound();
  if (response.status === 429) throw error(429, 'Too many requests. Try again shortly.');
  if (!response.ok) throw new Error(`help center request failed with ${response.status}`);
  return response.json();
}

/**
 * One page of the article list, optionally narrowed by a search.
 *
 * @param {{ request: Request, getClientAddress: () => string }} event
 * @param {string} slug already canonical
 * @param {{ q?: string, page?: number }} [options]
 */
export async function getHelpCenter(event, slug, { q = '', page = 1 } = {}) {
  const params = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String((page - 1) * PAGE_SIZE)
  });
  if (q) params.set('q', q);
  const data = await call(event, `/${encodeURIComponent(slug)}/?${params}`);
  return {
    name: data.help_center?.name ?? '',
    articles: data.articles ?? [],
    count: data.articles_count ?? 0
  };
}

/**
 * One article. A malformed id is answered here with the same 404 the API gives
 * for a missing one.
 *
 * @param {{ request: Request, getClientAddress: () => string }} event
 * @param {string} slug already canonical
 * @param {string} id
 */
export async function getHelpArticle(event, slug, id) {
  if (!UUID_RE.test(id)) throw notFound();
  const data = await call(event, `/${encodeURIComponent(slug)}/articles/${id}/`);
  return {
    name: data.help_center?.name ?? '',
    article: data.article,
    related: data.related ?? []
  };
}

/**
 * Every article, for the sitemap. Walks the API a page at a time, since the
 * API caps a page at 100 for an anonymous caller.
 *
 * @param {{ request: Request, getClientAddress: () => string }} event
 * @param {string} slug already canonical
 * @returns {Promise<{ id: string, updated_at?: string }[]>}
 */
export async function listAllArticles(event, slug) {
  /** @type {{ id: string, updated_at?: string }[]} */
  const all = [];
  for (let pageIndex = 0; pageIndex < SITEMAP_MAX_PAGES; pageIndex += 1) {
    const params = new URLSearchParams({
      limit: String(SITEMAP_PAGE_SIZE),
      offset: String(pageIndex * SITEMAP_PAGE_SIZE)
    });
    const data = await call(event, `/${encodeURIComponent(slug)}/?${params}`);
    const articles = data.articles ?? [];
    all.push(...articles.map((/** @type {any} */ a) => ({ id: a.id, updated_at: a.updated_at })));
    if (all.length >= (data.articles_count ?? 0) || articles.length === 0) break;
  }
  return all;
}

/**
 * A page number from the query string, or 1. Anything that is not a positive
 * whole number is treated as the first page rather than as an error.
 *
 * @param {string | null} raw
 */
export function pageNumber(raw) {
  const n = Number(raw);
  return Number.isInteger(n) && n >= 1 && n <= 10000 ? n : 1;
}

/** @param {string} value */
function xmlEscape(value) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

/**
 * The sitemap document: the index page and every article.
 *
 * @param {string} origin e.g. https://app.example.com
 * @param {string} slug
 * @param {{ id: string, updated_at?: string }[]} articles
 */
export function sitemapXml(origin, slug, articles) {
  const base = `${origin}/help-center/${slug}`;
  const entries = [`  <url><loc>${xmlEscape(base)}</loc></url>`];
  for (const article of articles) {
    const lastmod = article.updated_at
      ? `<lastmod>${xmlEscape(article.updated_at.slice(0, 10))}</lastmod>`
      : '';
    entries.push(
      `  <url><loc>${xmlEscape(`${base}/articles/${article.id}`)}</loc>${lastmod}</url>`
    );
  }
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...entries,
    '</urlset>',
    ''
  ].join('\n');
}
