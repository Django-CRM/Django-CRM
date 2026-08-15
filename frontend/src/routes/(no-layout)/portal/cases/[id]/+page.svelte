<script>
  /**
   * A conversation, not a record view. The customer wants to know what was said
   * and to say something back, so the thread is the page and the case fields are
   * a header above it.
   */
  import { enhance } from '$app/forms';
  import { resolve } from '$app/paths';
  import PortalShell from '$lib/v2/components/PortalShell.svelte';

  let { data, form } = $props();

  const OPEN_STATUSES = new Set(['New', 'Assigned', 'Pending']);

  function formatWhen(value) {
    if (!value) return '';
    return new Date(value).toLocaleString(undefined, {
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit'
    });
  }
</script>

<svelte:head>
  <title>{data.case.name}</title>
</svelte:head>

<PortalShell>
  <a class="back" href={resolve('/portal/cases')}>Back to your requests</a>

  <header class="head">
    <h1>{data.case.name}</h1>
    <span class="tag" class:open={OPEN_STATUSES.has(data.case.status)}>{data.case.status}</span>
  </header>

  {#if data.case.description}
    <p class="desc">{data.case.description}</p>
  {/if}

  <section class="thread">
    {#if data.comments.length === 0}
      <p class="empty">No replies yet. We will email you when support responds.</p>
    {:else}
      {#each data.comments as entry (entry.id)}
        <article class="msg" class:mine={entry.is_mine}>
          <div class="who">
            <strong>{entry.is_mine ? 'You' : entry.author}</strong>
            <span class="when">{formatWhen(entry.commented_on)}</span>
          </div>
          <p>{entry.comment}</p>
        </article>
      {/each}
    {/if}
  </section>

  <form method="POST" action="?/reply" use:enhance class="reply">
    <label for="comment">Add a reply</label>
    <textarea id="comment" name="comment" rows="4" placeholder="Type your message" required
    ></textarea>
    {#if form?.error}<p class="err">{form.error}</p>{/if}
    <button type="submit">Send reply</button>
  </form>
</PortalShell>

<style>
  .back {
    /* The only way back on a phone, so it gets a real tap target rather than
       the 19px a bare text link would be. Measured at 390px, not assumed. */
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    margin-bottom: 4px;
    font-size: 13px;
    color: var(--v2-slate, #6b7280);
  }
  .head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 10px;
  }
  h1 {
    margin: 0;
    font-size: 20px;
    font-weight: 600;
    /* Long summaries must wrap rather than push the tag off a 390px screen. */
    overflow-wrap: anywhere;
  }
  .tag {
    flex: none;
    padding: 3px 10px;
    border-radius: 999px;
    background: var(--v2-rule, #f3f4f6);
    font-size: 12.5px;
  }
  .tag.open {
    background: var(--v2-moss-bg, #dcfce7);
  }
  .desc {
    margin: 0 0 20px;
    color: var(--v2-slate, #4b5563);
    font-size: 14px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .thread {
    display: grid;
    gap: 12px;
    margin-bottom: 24px;
  }
  .msg {
    border: 1px solid var(--v2-rule, #e5e7eb);
    border-radius: 10px;
    padding: 12px 14px;
  }
  .msg.mine {
    background: var(--v2-paper-2, #f9fafb);
  }
  .who {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 6px;
    font-size: 12.5px;
    color: var(--v2-slate, #6b7280);
  }
  .msg p {
    margin: 0;
    font-size: 14px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .reply label {
    display: block;
    margin-bottom: 6px;
    font-size: 13px;
    font-weight: 500;
  }
  textarea {
    width: 100%;
    box-sizing: border-box;
    /* 16px stops iOS Safari zooming the viewport on focus. */
    font-size: 16px;
    padding: 11px;
    border: 1px solid var(--v2-rule, #d1d5db);
    border-radius: 8px;
  }
  button {
    margin-top: 12px;
    width: 100%;
    min-height: 46px;
    font-size: 15px;
    border: 0;
    border-radius: 8px;
    background: var(--v2-ink, #111827);
    color: #fff;
    cursor: pointer;
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
