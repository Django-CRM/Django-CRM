<script>
  /**
   * One public help article.
   *
   * The body is rendered as text, not as HTML, exactly as the signed-in portal
   * renders it. `white-space: pre-wrap` keeps the author's line breaks without
   * handing the page a markup parser. This page is anonymous and indexed, so
   * an org's stored text reaches strangers here; if rich text ever arrives it
   * needs a sanitiser, never `{@html}`.
   */
  import { resolve } from '$app/paths';
  import HelpCenterShell from '$lib/v2/components/HelpCenterShell.svelte';

  let { data } = $props();

  let title = $derived(`${data.article.title} | ${data.name} help center`);

  /** A one-line summary for search results: the body's opening, whitespace folded. */
  let description = $derived.by(() => {
    const text = String(data.article.description ?? '')
      .replace(/\s+/g, ' ')
      .trim();
    return text.length > 160 ? `${text.slice(0, 157).trimEnd()}...` : text;
  });

  /** @param {string} value */
  function formatDate(value) {
    if (!value) return '';
    return new Date(value).toLocaleDateString('en', {
      day: 'numeric',
      month: 'short',
      year: 'numeric'
    });
  }
</script>

<svelte:head>
  <title>{title}</title>
  {#if description}
    <meta name="description" content={description} />
    <meta property="og:description" content={description} />
  {/if}
  <link rel="canonical" href={data.canonical} />
  <meta property="og:title" content={data.article.title} />
  <meta property="og:type" content="article" />
</svelte:head>

<HelpCenterShell name={data.name} slug={data.slug}>
  <a class="back" href={resolve(`/help-center/${data.slug}`)}>All help articles</a>

  <article>
    <h1>{data.article.title}</h1>
    {#if data.article.updated_at}
      <p class="when">
        Updated <time datetime={data.article.updated_at}>{formatDate(data.article.updated_at)}</time
        >
      </p>
    {/if}
    <div class="body">{data.article.description}</div>
  </article>

  {#if data.related.length > 0}
    <nav class="related" aria-label="Related articles">
      <h2>Related articles</h2>
      <ul>
        {#each data.related as item (item.id)}
          <li>
            <a href={resolve(`/help-center/${data.slug}/articles/${item.id}`)}>{item.title}</a>
          </li>
        {/each}
      </ul>
    </nav>
  {/if}
</HelpCenterShell>

<style>
  .back {
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    font-size: 14px;
    color: var(--v2-slate, #6b7280);
    text-decoration: none;
  }
  h1 {
    margin: 8px 0 6px;
    font-size: 22px;
    font-weight: 600;
    line-height: 1.3;
    overflow-wrap: anywhere;
  }
  .when {
    margin: 0 0 20px;
    font-size: 12.5px;
    color: var(--v2-slate, #6b7280);
  }
  .body {
    /* Keeps the author's paragraphs without parsing their text as markup. */
    white-space: pre-wrap;
    line-height: 1.6;
    /* URLs and error strings would otherwise push a 390px screen sideways. */
    overflow-wrap: anywhere;
  }
  .related {
    margin-top: 32px;
    padding-top: 20px;
    border-top: 1px solid var(--v2-rule, #e5e7eb);
  }
  .related h2 {
    margin: 0 0 10px;
    font-size: 13px;
    font-weight: 500;
    color: var(--v2-slate, #6b7280);
  }
  .related ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 8px;
  }
  .related a {
    display: block;
    padding: 12px 14px;
    border: 1px solid var(--v2-rule, #e5e7eb);
    border-radius: 10px;
    text-decoration: none;
    color: inherit;
    font-size: 14px;
    overflow-wrap: anywhere;
  }
</style>
