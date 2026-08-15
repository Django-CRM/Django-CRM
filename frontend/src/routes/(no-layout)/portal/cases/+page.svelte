<script>
  /**
   * Built for a 390px phone first. The list is a stack of cards rather than a
   * table, because a customer has a handful of requests and a table of six
   * columns is a desktop answer to a phone question.
   */
  import { enhance } from '$app/forms';
  import { resolve } from '$app/paths';
  import PortalShell from '$lib/v2/components/PortalShell.svelte';

  let { data, form } = $props();

  let composing = $state(false);

  const FILTERS = [
    { value: '', label: 'All' },
    { value: 'New', label: 'New' },
    { value: 'Pending', label: 'Pending' },
    { value: 'Closed', label: 'Closed' }
  ];

  const OPEN_STATUSES = new Set(['New', 'Assigned', 'Pending']);

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
  <title>Your support requests</title>
</svelte:head>

<PortalShell>
  <header class="head">
    <h1>Your requests</h1>
    <button type="button" onclick={() => (composing = !composing)}>
      {composing ? 'Cancel' : 'New request'}
    </button>
  </header>

  {#if composing}
    <form method="POST" action="?/create" use:enhance class="compose">
      <label for="name">What do you need help with?</label>
      <input id="name" name="name" required placeholder="Short summary" />

      <label for="description">Any detail that would help</label>
      <textarea id="description" name="description" rows="4"></textarea>

      <label for="priority">How urgent is it?</label>
      <select id="priority" name="priority">
        <option value="Low">Low</option>
        <option value="Normal" selected>Normal</option>
        <option value="High">High</option>
      </select>

      {#if form?.error}<p class="err">{form.error}</p>{/if}
      <button type="submit" class="primary">Send request</button>
    </form>
  {/if}

  <nav class="filters">
    {#each FILTERS as filter (filter.value)}
      <a
        href={resolve(filter.value ? `/portal/cases?status=${filter.value}` : '/portal/cases')}
        class:on={data.status === filter.value}
      >
        {filter.label}
      </a>
    {/each}
  </nav>

  {#if data.cases.length === 0}
    <p class="empty">
      {data.status
        ? `You have no ${data.status.toLowerCase()} requests.`
        : 'You have not sent us any requests yet.'}
    </p>
  {:else}
    <ul class="list">
      {#each data.cases as item (item.id)}
        <li>
          <a href={resolve(`/portal/cases/${item.id}`)}>
            <span class="name">{item.name}</span>
            <span class="meta">
              <span class="tag" class:open={OPEN_STATUSES.has(item.status)}>{item.status}</span>
              <span class="when">{formatDate(item.created_at)}</span>
            </span>
          </a>
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
  button {
    min-height: 44px;
    padding: 0 14px;
    font-size: 14px;
    border: 1px solid var(--v2-rule, #d1d5db);
    border-radius: 8px;
    background: none;
    cursor: pointer;
  }
  button.primary {
    width: 100%;
    margin-top: 14px;
    border: 0;
    background: var(--v2-ink, #111827);
    color: #fff;
  }
  .compose {
    border: 1px solid var(--v2-rule, #e5e7eb);
    border-radius: 10px;
    padding: 18px 16px;
    margin-bottom: 20px;
  }
  .compose label {
    display: block;
    margin: 12px 0 6px;
    font-size: 13px;
    font-weight: 500;
  }
  .compose label:first-child {
    margin-top: 0;
  }
  input,
  textarea,
  select {
    width: 100%;
    box-sizing: border-box;
    /* 16px stops iOS Safari zooming the viewport on focus. */
    font-size: 16px;
    padding: 11px;
    border: 1px solid var(--v2-rule, #d1d5db);
    border-radius: 8px;
  }
  .filters {
    display: flex;
    gap: 8px;
    margin-bottom: 16px;
    /* Four short filters fit at 390px; scrolling is the fallback, not the plan. */
    overflow-x: auto;
  }
  .filters a {
    /* inline-flex plus min-height rather than padding alone: padding put these
       at 39px, which measured under the 44px floor at 390px. */
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    padding: 0 16px;
    border-radius: 999px;
    border: 1px solid var(--v2-rule, #e5e7eb);
    font-size: 13px;
    text-decoration: none;
    color: inherit;
    white-space: nowrap;
  }
  .filters a.on {
    background: var(--v2-ink, #111827);
    color: #fff;
    border-color: var(--v2-ink, #111827);
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
  }
  .name {
    display: block;
    font-weight: 500;
    margin-bottom: 8px;
  }
  .meta {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 12.5px;
    color: var(--v2-slate, #6b7280);
  }
  .tag {
    padding: 2px 9px;
    border-radius: 999px;
    background: var(--v2-rule, #f3f4f6);
  }
  .tag.open {
    background: var(--v2-moss-bg, #dcfce7);
  }
  .empty {
    color: var(--v2-slate, #6b7280);
    font-size: 14px;
  }
  .err {
    margin: 10px 0 0;
    color: var(--v2-rust, #b91c1c);
    font-size: 13px;
  }
</style>
