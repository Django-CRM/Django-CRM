/**
 * `/help-center/<slug>/sitemap.xml`: the index page and every published
 * article, for search engines. A help center that is off or unknown is a 404
 * here too.
 */

import { canonicalSlug, listAllArticles, sitemapXml } from '$lib/server/help-center.js';

/** @type {import('./$types').RequestHandler} */
export async function GET(event) {
  const slug = canonicalSlug(event.params.slug, event.url);
  const articles = await listAllArticles(event, slug);
  return new Response(sitemapXml(event.url.origin, slug, articles), {
    headers: {
      'Content-Type': 'application/xml; charset=utf-8',
      'Cache-Control': 'public, max-age=3600'
    }
  });
}
