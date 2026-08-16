<script>
  /**
   * Built for a 390px phone first, and a stack of cards for the same reason the
   * requests list is one: a customer reads a handful of these, not a table.
   *
   * The search box is a plain GET form. It works before the page hydrates, and
   * a customer looking for an answer is exactly the person most likely to be on
   * a slow connection.
   */
  import { resolve } from '$app/paths';
  import PortalShell from '$lib/v2/components/PortalShell.svelte';

  let { data } = $props();
</script>

<svelte:head>
  <title>Help articles</title>
</svelte:head>

<PortalShell>
  <header class="head">
    <h1>Help articles</h1>
    <a class="btn" href={resolve('/portal/cases')}>Your requests</a>
  </header>

  <form method="GET" class="find">
    <label class="sr-only" for="search">Search help articles</label>
    <input id="search" name="search" value={data.search} placeholder="Search for an answer" />
    <button type="submit">Search</button>
  </form>

  {#if data.articles.length === 0}
    <p class="empty">
      {data.search
        ? `Nothing matches "${data.search}". Try a different word, or send us a request.`
        : 'There are no help articles yet.'}
    </p>
  {:else}
    <ul class="list">
      {#each data.articles as article (article.id)}
        <li>
          <a href={resolve(`/portal/articles/${article.id}`)}>{article.title}</a>
        </li>
      {/each}
    </ul>
  {/if}
</PortalShell>

<style>
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 18px;
  }
  h1 {
    margin: 0;
    font-size: 21px;
    font-weight: 600;
  }
  .btn {
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    padding: 0 14px;
    font-size: 14px;
    border: 1px solid var(--v2-rule, #d1d5db);
    border-radius: 8px;
    text-decoration: none;
    color: inherit;
    white-space: nowrap;
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
    /* 16px stops iOS Safari zooming the viewport on focus. */
    font-size: 16px;
    padding: 11px;
    border: 1px solid var(--v2-rule, #d1d5db);
    border-radius: 8px;
  }
  .find button {
    min-height: 44px;
    padding: 0 14px;
    font-size: 14px;
    border: 0;
    border-radius: 8px;
    background: var(--v2-ink, #111827);
    color: #fff;
    cursor: pointer;
  }
  .list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 10px;
  }
  .list a {
    display: block;
    padding: 14px 16px;
    border: 1px solid var(--v2-rule, #e5e7eb);
    border-radius: 10px;
    text-decoration: none;
    color: inherit;
    font-weight: 500;
  }
  .empty {
    color: var(--v2-slate, #6b7280);
    font-size: 14px;
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
