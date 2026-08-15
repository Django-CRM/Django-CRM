<script>
  import { resolve } from '$app/paths';
  /**
   * New knowledge-base article.
   *
   * ── TWO SWITCHES, NOT ONE ────────────────────────────────────────────────
   * `cases.Solution` carries `status` (draft → reviewed → approved) AND a
   * separate `is_published` boolean, and they answer different questions:
   * status is "has a human checked this", published is "does the ticket reply
   * panel suggest it". The list page exists partly to surface the row where
   * they disagree, approved and still not suggested.
   *
   * Publishing does not reach a customer. `is_published` gates the agent
   * suggester in `cases/kb_views.py:85` and nothing else; the customer-facing
   * site it was meant to gate was cut. See `cases/kb_access.py:26-28`.
   *
   * ── WHERE THE MOCK WAS WRONG ─────────────────────────────────────────────
   * This form used to offer both switches freely and told the writer that a
   * published draft was "allowed, and sometimes right, but it is a choice,
   * not an accident". It was neither: `Solution.publish()` has always refused
   * it, and the serializer now refuses it on every path in. The old page could
   * say that because it never submitted anything.
   *
   * What is true is narrower and more useful, so the form says that instead:
   * publishing needs an approval, approving is somebody else's job, and a
   * writer's article starts as a draft. The publish switch only appears for
   * an admin. For anyone else it would be a control that answers 403.
   *
   * ── WHAT THIS FORM DOES NOT SET ──────────────────────────────────────────
   * `org` and `created_by` are server-derived; `case_count` is an aggregate
   * over the Case M2M and belongs to the database. None of the three appear
   * here, and the API rejects all three from the request body.
   */
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import { SOLUTION_STATUS, SOLUTION_STATUS_LABEL } from '$lib/v2/enums.js';
  import { enhance } from '$app/forms';
  import { untrack } from 'svelte';
  import { ChevronLeft, Eye, EyeOff } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  // `untrack` so a revalidation cannot overwrite what somebody has typed.
  // These are read once, on purpose. `update({ reset: false })` below is what
  // keeps a rejected save's words in the boxes.
  let title = $state(untrack(() => form?.values?.title ?? ''));
  let description = $state(untrack(() => form?.values?.description ?? ''));
  let status = $state(untrack(() => form?.values?.status ?? 'draft'));
  let isPublished = $state(untrack(() => Boolean(form?.values?.is_published)));
  let saving = $state(false);

  /**
   * Statuses this writer may actually set. `approved` is an admin act. See
   * `cases/kb_access.py`, so for everybody else it is not in the list rather
   * than in the list and rejected.
   */
  let statuses = $derived(
    data.canRelease ? SOLUTION_STATUS : SOLUTION_STATUS.filter((s) => s !== 'approved')
  );

  let ready = $derived(title.trim().length > 3 && description.trim().length > 20);

  /**
   * The combination, in words. Three reachable states, and each one puts the
   * article somewhere different, which is why the two flags cannot be
   * collapsed into a single "publish" toggle.
   */
  let consequence = $derived.by(() => {
    if (isPublished && status === 'approved')
      return { tone: 'moss', text: 'Suggested on tickets, and someone has checked it.' };
    if (isPublished)
      return {
        tone: 'clay',
        text: 'This will be refused: an article has to be approved before it can be published. Set the status to Approved, or leave it internal for now.'
      };
    if (status === 'approved')
      return {
        tone: 'clay',
        text: 'Checked, but not suggested anywhere yet. Publishing is what puts it in the ticket reply panel.'
      };
    return {
      tone: 'slate',
      text: 'Not suggested anywhere. Agents can still search for it and attach it to a ticket.'
    };
  });
</script>

<PageHeader title="New article" center width="760px">
  {#snippet crumb()}
    <a href={resolve('/solutions')}><ChevronLeft size={13} />Knowledge base</a>
  {/snippet}
  {#snippet sub()}
    Write the answer once, attach it to every ticket that asks
  {/snippet}
  {#snippet actions()}
    <a class="v2-btn" href={resolve('/solutions')}>Cancel</a>
    <button
      class="v2-btn v2-btn-primary"
      type="submit"
      form="article-form"
      disabled={!ready || saving}
    >
      {saving ? 'Saving…' : 'Save article'}
    </button>
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  <form
    id="article-form"
    method="POST"
    action="?/create"
    use:enhance={() => {
      saving = true;
      return async ({ update }) => {
        saving = false;
        // `reset: false` so a rejected save comes back with the words still
        // in the textarea. Retyping an article because the title was three
        // characters long is not a thing to ask of anybody.
        await update({ reset: false });
      };
    }}
  >
    <div
      class="v2-pad"
      style="padding-top:18px;padding-bottom:32px;max-width:760px;margin-left:auto;margin-right:auto"
    >
      {#if form?.error}
        <p style="color:var(--v2-rust);font-size:12.5px;margin:0 0 14px">{form.error}</p>
      {/if}

      <div class="v2-card" style="padding:18px 20px">
        <label class="f">
          <span>Title</span>
          <input
            name="title"
            bind:value={title}
            placeholder="Fixing an SSO login loop after an identity provider change"
          />
          <!-- The title is what an agent scans in a list of forty under time
               pressure, so the guidance is about scanning, not SEO. -->
          <em>Say the symptom the way a customer would report it, not the fix.</em>
        </label>

        <label class="f" style="margin-top:18px">
          <span>Answer</span>
          <textarea
            name="description"
            rows="9"
            bind:value={description}
            placeholder="What is happening, why, and the steps that resolve it."></textarea>
          <em>
            {#if description.trim().length && description.trim().length <= 20}
              A few more words: this is the text an agent will paste to a customer.
            {:else}
              This is pasted into replies as-is, so write it to be read by the person with the
              problem.
            {/if}
          </em>
        </label>
      </div>

      <div class="v2-card" style="padding:18px 20px;margin-top:14px">
        <div class="v2-label" style="margin-bottom:4px">Review and visibility</div>
        <p class="lead">
          These are two separate facts about the article and this form keeps them separate.
        </p>

        <div class="switches">
          <label class="f" style="max-width:220px">
            <span>Review status</span>
            <select name="status" bind:value={status}>
              {#each statuses as s (s)}
                <option value={s}>{SOLUTION_STATUS_LABEL[s]}</option>
              {/each}
            </select>
            {#if !data.canRelease}
              <em>Approving is an admin's call. It is what lets an article be published.</em>
            {/if}
          </label>

          {#if data.canRelease}
            <div class="pub">
              <span class="pub-label">Ticket suggestions</span>
              <div class="seg">
                <button type="button" class:on={!isPublished} onclick={() => (isPublished = false)}>
                  <EyeOff size={13} />Internal
                </button>
                <button type="button" class:on={isPublished} onclick={() => (isPublished = true)}>
                  <Eye size={13} />Published
                </button>
              </div>
              <!-- A segmented control cannot post a value on its own. The
                   checkbox is what the form actually submits. -->
              <input type="checkbox" name="is_published" checked={isPublished} hidden />
            </div>
          {/if}
        </div>

        <p class="consequence" data-tone={consequence.tone}>{consequence.text}</p>
      </div>
    </div>
  </form>
</div>

<style>
  .f {
    display: block;
  }
  .f > span {
    display: block;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--v2-slate);
    margin-bottom: 5px;
  }
  .f em {
    display: block;
    margin-top: 5px;
    font-style: normal;
    font-size: 11.5px;
    color: var(--v2-slate);
    line-height: 1.5;
  }
  input,
  select,
  textarea {
    width: 100%;
    padding: 8px 10px;
    font: inherit;
    font-size: 13.5px;
    color: var(--v2-ink);
    background: var(--v2-card);
    border: 1px solid var(--v2-line);
    border-radius: 6px;
  }
  textarea {
    resize: vertical;
    line-height: 1.6;
  }
  input:focus,
  select:focus,
  textarea:focus {
    outline: 2px solid var(--v2-ember);
    outline-offset: -1px;
  }

  .lead {
    margin: 0 0 14px;
    font-size: 12.5px;
    color: var(--v2-slate);
    line-height: 1.5;
  }
  .switches {
    display: flex;
    gap: 26px;
    flex-wrap: wrap;
    align-items: flex-start;
  }
  .pub-label {
    display: block;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--v2-slate);
    margin-bottom: 5px;
  }
  .seg {
    display: flex;
    border: 1px solid var(--v2-line);
    border-radius: 6px;
    overflow: hidden;
  }
  .seg button {
    display: flex;
    align-items: center;
    gap: 5px;
    padding: 7px 12px;
    font: inherit;
    font-size: 12.5px;
    color: var(--v2-slate);
    background: var(--v2-card);
    border: 0;
    cursor: pointer;
  }
  .seg button + button {
    border-left: 1px solid var(--v2-line);
  }
  .seg button.on {
    color: var(--v2-ink);
    background: var(--v2-hover);
    font-weight: 600;
  }

  .consequence {
    margin: 16px 0 0;
    padding-top: 13px;
    border-top: 1px solid var(--v2-line);
    font-size: 12.5px;
    line-height: 1.55;
    color: var(--v2-slate);
  }
  .consequence[data-tone='clay'] {
    color: var(--v2-clay);
  }
  .consequence[data-tone='moss'] {
    color: var(--v2-moss);
  }
</style>
