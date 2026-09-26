<script>
  /**
   * The front page of an org's public help center.
   *
   * Built for a 390px phone first. The search box is a plain GET form, so it
   * works before the page hydrates and on the slow connection a person looking
   * for an answer is often on. Paging is plain links for the same reason, and
   * because crawlers follow links, not buttons.
   *
   * A search result page is marked `noindex, follow`: it is a filtered copy of
   * this list, and an index full of "?q=" pages is noise. The articles it links
   * to are still followed and indexed on their own URLs.
   */
  import { resolve } from '$app/paths';
  import HelpCenterShell from '$lib/v2/components/HelpCenterShell.svelte';

  let { data } = $props();

  let title = $derived(
    data.q ? `Search: ${data.q} | ${data.name} help center` : `${data.name} help center`
  );
  let description = $derived(`Answers and guides from ${data.name}.`);

  /** The query string for page `n`, keeping the search. @param {number} n */
  function pageQuery(n) {
    const parts = [];
    if (data.q) parts.push(`q=${encodeURIComponent(data.q)}`);
    if (n > 1) parts.push(`page=${n}`);
    return parts.length ? `?${parts.join('&')}` : '';
  }
</script>

<svelte:head>
  <title>{title}</title>
  <meta name="description" content={description} />
  <link rel="canonical" href={data.canonical} />
  <meta property="og:title" content={title} />
  <meta property="og:description" content={description} />
  <meta property="og:type" content="website" />
  {#if data.q}
    <meta name="robots" content="noindex, follow" />
  {/if}
</svelte:head>

<HelpCenterShell name={data.name} slug={data.slug}>
  <h1>How can we help?</h1>

  <form method="GET" class="find" role="search">
    <label class="sr-only" for="q">Search help articles</label>
    <input
      id="q"
      name="q"
      type="search"
      value={data.q}
      maxlength="200"
      placeholder="Search for an answer"
    />
    <button type="submit">Search</button>
  </form>

  {#if data.q}
    <p class="meta">
      {data.count === 1 ? '1 article matches' : `${data.count} articles match`} "{data.q}".
      <a href={resolve(`/help-center/${data.slug}`)}>Show all</a>
    </p>
  {/if}

  {#if data.articles.length === 0}
    <p class="empty">
      {data.q
        ? 'Nothing matches that. Try a different word.'
        : 'There are no help articles here yet.'}
    </p>
  {:else}
    <ul class="list">
      {#each data.articles as article (article.id)}
        <li>
          <a href={resolve(`/help-center/${data.slug}/articles/${article.id}`)}>
            <span class="t">{article.title}</span>
            {#if article.snippet}
              <span class="s">{article.snippet}</span>
            {/if}
          </a>
        </li>
      {/each}
    </ul>
  {/if}

  {#if data.pages > 1}
    <nav class="pager" aria-label="Pages">
      {#if data.page > 1}
        <a href="{resolve(`/help-center/${data.slug}`)}{pageQuery(data.page - 1)}" rel="prev"
          >Previous</a
        >
      {:else}
        <span></span>
      {/if}
      <span class="where">Page {data.page} of {data.pages}</span>
      {#if data.page < data.pages}
        <a href="{resolve(`/help-center/${data.slug}`)}{pageQuery(data.page + 1)}" rel="next"
          >Next</a
        >
      {:else}
        <span></span>
      {/if}
    </nav>
  {/if}
</HelpCenterShell>

<style>
  h1 {
    margin: 0 0 16px;
    font-size: 22px;
    font-weight: 600;
    line-height: 1.3;
  }
  .find {
    display: flex;
    gap: 8px;
    margin-bottom: 20px;
  }
  input {
    flex: 1;
    min-width: 0;
    box-sizing: border-box;
    min-height: 44px;
    /* 16px stops iOS Safari zooming the viewport on focus. */
    font-size: 16px;
    padding: 10px 11px;
    border: 1px solid var(--v2-rule, #d1d5db);
    border-radius: 8px;
    background: #fff;
    color: inherit;
  }
  .find button {
    min-height: 44px;
    padding: 0 16px;
    font-size: 14px;
    border: 0;
    border-radius: 8px;
    background: var(--v2-ink, #111827);
    color: #fff;
    cursor: pointer;
  }
  .meta {
    margin: -8px 0 16px;
    font-size: 13px;
    color: var(--v2-slate, #6b7280);
    overflow-wrap: anywhere;
  }
  .meta a {
    display: inline-block;
    padding: 12px 4px;
    margin: -12px 0;
    color: inherit;
  }
  .list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 10px;
  }
  .list a {
    display: grid;
    gap: 4px;
    padding: 14px 16px;
    border: 1px solid var(--v2-rule, #e5e7eb);
    border-radius: 10px;
    text-decoration: none;
    color: inherit;
    overflow-wrap: anywhere;
  }
  .t {
    font-weight: 500;
  }
  .s {
    font-size: 13px;
    line-height: 1.45;
    color: var(--v2-slate, #6b7280);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .empty {
    color: var(--v2-slate, #6b7280);
    font-size: 14px;
  }
  .pager {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: center;
    gap: 8px;
    margin-top: 24px;
  }
  .pager a {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 44px;
    padding: 0 14px;
    border: 1px solid var(--v2-rule, #d1d5db);
    border-radius: 8px;
    font-size: 14px;
    text-decoration: none;
    color: inherit;
  }
  .pager a[rel='prev'] {
    justify-self: start;
  }
  .pager a[rel='next'] {
    justify-self: end;
  }
  .where {
    font-size: 13px;
    color: var(--v2-slate, #6b7280);
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
</style>
