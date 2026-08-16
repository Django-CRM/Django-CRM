<script>
  import { resolve } from '$app/paths';
  /**
   * Editing an article is editing its words. The two decisions about it,
   * approving it, and releasing it to customers, are buttons
   * on the article page, because they are things somebody does once and not
   * fields somebody fills in.
   *
   * The status select is here because moving a draft along to "reviewed" is
   * part of finishing the writing. Approving is not, and is only offered to
   * an admin.
   */
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import { SOLUTION_STATUS, SOLUTION_STATUS_LABEL } from '$lib/v2/enums.js';
  import { enhance } from '$app/forms';
  import { untrack } from 'svelte';
  import { ChevronLeft } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  // `untrack` so a revalidation of the page data cannot overwrite what
  // somebody has typed. The initial value is read once, on purpose.
  let title = $state(untrack(() => form?.values?.title ?? data.form.title));
  let description = $state(untrack(() => form?.values?.description ?? data.form.description));
  let status = $state(untrack(() => form?.values?.status ?? data.form.status));
  // An array rather than a Set: a knowledge base has a handful of tags, so
  // `includes` costs nothing, and Svelte's reactivity has no special case to
  // fall foul of. Seeded from the failed save first so a rejected submit comes
  // back with the same chips lit, then from the article's own tags.
  let picked = $state(untrack(() => (form?.values?.tags ?? data.form.tags ?? []).map(String)));
  let saving = $state(false);

  /** @param {string} id */
  function toggleTag(id) {
    picked = picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id];
  }

  let statuses = $derived(
    data.canRelease ? SOLUTION_STATUS : SOLUTION_STATUS.filter((s) => s !== 'approved')
  );

  let ready = $derived(title.trim().length > 3 && description.trim().length > 20);

  /** Published articles cannot be moved back down the workflow. See the
      serializer. Saying so beside the select beats a 400 after the save. */
  let locked = $derived(data.article.is_published);
</script>

<PageHeader title="Edit article" record center width="760px">
  {#snippet crumb()}
    <a href={resolve(`/solutions/${data.article.id}`)}
      ><ChevronLeft size={13} />{data.article.title}</a
    >
  {/snippet}
  {#snippet sub()}
    {data.article.author || 'Unknown author'} · used on
    <span class="v2-num">{data.article.use_count}</span> tickets
  {/snippet}
  {#snippet actions()}
    <a class="v2-btn" href={resolve(`/solutions/${data.article.id}`)}>Cancel</a>
    <button
      class="v2-btn v2-btn-primary"
      type="submit"
      form="article-form"
      disabled={!ready || saving}
    >
      {saving ? 'Saving…' : 'Save changes'}
    </button>
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  <form
    id="article-form"
    method="POST"
    action="?/save"
    use:enhance={() => {
      saving = true;
      return async ({ update }) => {
        saving = false;
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

      {#if data.article.is_published}
        <p class="notice">
          This article is published, so anything saved here is what customers read in the portal and
          what agents are offered on tickets.
        </p>
      {/if}

      <div class="v2-card" style="padding:18px 20px">
        <label class="f">
          <span>Title</span>
          <input name="title" bind:value={title} />
          <em>Say the symptom the way a customer would report it, not the fix.</em>
        </label>

        <label class="f" style="margin-top:18px">
          <span>Answer</span>
          <textarea name="description" rows="12" bind:value={description}></textarea>
          <em>This is pasted into replies as-is.</em>
        </label>
      </div>

      {#if data.tags.length > 0}
        <div class="v2-card" style="padding:18px 20px;margin-top:14px">
          <div class="v2-label" style="margin-bottom:4px">Tags</div>
          <!-- Internal filing only. The portal uses these to pick related
               articles and never prints the names, which read like "At Risk"
               and "VIP" because the vocabulary is shared with deals. -->
          <p class="lead">
            Customers never see these names, but articles sharing one are shown to each other in the
            portal.
          </p>
          <div class="tags">
            {#each data.tags as tag (tag.id)}
              <label class="tag">
                <input
                  type="checkbox"
                  name="tags"
                  value={tag.id}
                  checked={picked.includes(String(tag.id))}
                  onchange={() => toggleTag(String(tag.id))}
                />
                <span>{tag.name}</span>
              </label>
            {/each}
          </div>
        </div>
      {/if}

      <div class="v2-card" style="padding:18px 20px;margin-top:14px">
        <label class="f" style="max-width:240px">
          <span>Review status</span>
          <select name="status" bind:value={status} disabled={locked}>
            {#each statuses as s (s)}
              <option value={s}>{SOLUTION_STATUS_LABEL[s]}</option>
            {/each}
          </select>
          <em>
            {#if locked}
              A published article cannot move back down the workflow. Unpublish it first.
            {:else if !data.canRelease}
              Approving is an admin's call. It is what lets an article be published.
            {:else}
              Approving does not publish it. That is a separate button on the article.
            {/if}
          </em>
        </label>
        <!-- What the status was when this page loaded, so the action can tell
             "somebody chose this" from "the select simply had a value". -->
        <input type="hidden" name="status_original" value={data.form.status} />
      </div>
    </div>
  </form>
</div>

<style>
  .lead {
    margin: 0;
    font-size: 12.5px;
    color: var(--v2-slate);
  }
  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 12px;
  }
  .tag {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    min-height: 44px;
    padding: 0 13px;
    border: 1px solid var(--v2-rule);
    border-radius: 999px;
    font-size: 13px;
    cursor: pointer;
  }
  .tag:has(input:checked) {
    border-color: var(--v2-ink);
    background: var(--v2-ink);
    color: var(--v2-paper);
  }
  .tag input {
    /* The chip itself is the affordance; the box would be a second one.
       Sized explicitly: these forms set `input { width: 100% }`, and an
       absolutely positioned 100%-wide checkbox pushed 272px of horizontal
       overflow onto a 390px screen. Kept focusable rather than
       `display: none` so the chips still work from a keyboard. */
    position: absolute;
    width: 1px;
    height: 1px;
    opacity: 0;
    pointer-events: none;
  }
  .tag:focus-within {
    outline: 2px solid var(--v2-ink);
    outline-offset: 2px;
  }
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
  select:disabled {
    color: var(--v2-slate);
    background: var(--v2-hover);
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
  .notice {
    margin: 0 0 14px;
    padding: 10px 12px;
    font-size: 12.5px;
    line-height: 1.5;
    color: var(--v2-clay);
    background: color-mix(in srgb, var(--v2-clay) 8%, transparent);
    border: 1px solid color-mix(in srgb, var(--v2-clay) 26%, transparent);
    border-radius: 6px;
  }
</style>
