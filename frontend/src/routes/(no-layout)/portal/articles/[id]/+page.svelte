<script>
  /**
   * The article body is rendered as text, not as HTML.
   *
   * `white-space: pre-wrap` keeps the author's line breaks without handing the
   * page a markup parser. Article bodies are written by agents, but "written by
   * staff" is not the same as "safe to inject", and this is the one page in the
   * product where an org's own text is rendered to somebody outside the org.
   * If rich text arrives later it needs a sanitiser, not `{@html}`.
   */
  import { resolve } from '$app/paths';
  import PortalShell from '$lib/v2/components/PortalShell.svelte';

  let { data } = $props();

  function formatDate(value) {
    if (!value) return '';
    return new Date(value).toLocaleDateString(undefined, {
      day: 'numeric',
      month: 'short',
      year: 'numeric'
    });
  }
</script>

<svelte:head>
  <title>{data.article.title}</title>
</svelte:head>

<PortalShell>
  <a class="back" href={resolve('/portal/articles')}>Back to help articles</a>

  <article>
    <h1>{data.article.title}</h1>
    {#if data.article.updated_at}
      <p class="when">Updated {formatDate(data.article.updated_at)}</p>
    {/if}
    <div class="body">{data.article.description}</div>
  </article>

  {#if data.related.length > 0}
    <nav class="related" aria-label="Related articles">
      <h2>Related articles</h2>
      <ul>
        {#each data.related as item (item.id)}
          <li><a href={resolve(`/portal/articles/${item.id}`)}>{item.title}</a></li>
        {/each}
      </ul>
    </nav>
  {/if}

  <!-- The link is its own block rather than a word inside the sentence. As
       inline prose it measured 17px tall, well under a thumb, and it is the
       action this whole page exists to avoid needing. -->
  <div class="ask">
    <p>Still stuck?</p>
    <a href={resolve('/portal/cases')}>Send us a request</a>
  </div>
</PortalShell>

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
    font-size: 21px;
    font-weight: 600;
    line-height: 1.3;
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
    /* Long support answers carry URLs and error strings that would otherwise
       push a 390px screen sideways. */
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
  }
  .ask {
    margin-top: 32px;
    padding-top: 20px;
    border-top: 1px solid var(--v2-rule, #e5e7eb);
  }
  .ask p {
    margin: 0 0 10px;
    font-size: 14px;
    color: var(--v2-slate, #6b7280);
  }
  .ask a {
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    padding: 0 16px;
    border: 1px solid var(--v2-rule, #d1d5db);
    border-radius: 8px;
    font-size: 14px;
    text-decoration: none;
    color: inherit;
  }
  /* Related already drew the rule that separates the body from the footer;
     a second one right under it reads as an empty section. */
  .related + .ask {
    margin-top: 20px;
    padding-top: 0;
    border-top: 0;
  }
</style>
