<script>
  /**
   * Web forms: the list.
   *
   * WHAT A FORM IS, AND WHY THIS PAGE IS CAREFUL
   * A published web form is an endpoint anyone on the internet can post to,
   * and every accepted post writes a lead into this organisation. That is
   * closer to a credential than to a record, which is why creating, editing,
   * publishing and deleting are all admin-only, and why a member sees this
   * list read-only rather than not at all: knowing which forms are live is
   * ordinary operational knowledge, minting one is not.
   *
   * The controls below are hidden from a member as a courtesy, not as the
   * boundary. `is_org_admin(request.profile)` in `webforms/views.py` is the
   * boundary, and the page's actions turn its 403 into a sentence, because a
   * member can still post to them directly.
   *
   * PUBLISHED IS THE COLUMN WORTH SCANNING
   * A draft collects nothing, so the pill answers the only question anyone
   * opens this page with. Draft is `slate`, the absence of a problem, not
   * `clay`: an unfinished form is a normal state, not a fault. The one thing
   * flagged in clay is a published form with no submissions in 30 days, which
   * is the shape of an embed that was removed from the customer's site.
   *
   * COUNTS COME FROM `totals`, WHICH THE SERVER COMPUTES
   * The list is paginated. Counting the rows on screen would be right until
   * the org's eleventh form and quietly wrong afterwards, so every figure in
   * the stat strip comes from a server-side count over every form.
   */
  import { resolve } from '$app/paths';
  import { asInternalPath } from '$lib/utils/paths.js';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import SettingsCrumb from '$lib/v2/components/SettingsCrumb.svelte';
  import StatCard from '$lib/v2/components/StatCard.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import NextAction from '$lib/v2/components/NextAction.svelte';
  import EmptyState from '$lib/v2/components/EmptyState.svelte';
  import ConfirmAction from '$lib/v2/components/ConfirmAction.svelte';
  import { count, shortDate } from '$lib/v2/format.js';
  import { enhance } from '$app/forms';
  import { Plus, ShieldAlert } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  let totals = $derived(data.totals);
  let drafts = $derived(Math.max(totals.count - totals.published, 0));

  let creating = $state(false);
  let busy = $state(false);

  /** A submit handler that flips `busy` while the action runs. */
  const working = () => {
    busy = true;
    return async (/** @type {any} */ { update }) => {
      await update();
      busy = false;
    };
  };

  /**
   * The one error surface. Every action reports under its own key so a failed
   * publish cannot be mistaken for a failed delete, and this reads whichever
   * one came back.
   */
  let actionError = $derived(
    form?.create?.error ??
      form?.publish?.error ??
      form?.unpublish?.error ??
      form?.delete?.error ??
      null
  );
</script>

<PageHeader title="Web forms">
  {#snippet crumb()}<SettingsCrumb />{/snippet}
  {#snippet sub()}
    <span class="v2-num">{count(totals.published)}</span> published of
    <span class="v2-num">{count(totals.count)}</span>
  {/snippet}
  {#snippet actions()}
    {#if data.canManage}
      <button class="v2-btn v2-btn-primary" onclick={() => (creating = !creating)}>
        <Plus />New form
      </button>
    {/if}
  {/snippet}
</PageHeader>

<div class="v2-pad" style="padding-top:16px;flex:none">
  <div class="v2-stats">
    <StatCard label="Published" value={count(totals.published)} tone="ink" />
    <StatCard
      label="Drafts"
      value={count(drafts)}
      tone="slate"
      detail={drafts ? 'Collecting nothing yet' : 'None'}
    />
    <StatCard label="Leads, 30 days" value={count(totals.submissions_30d)} tone="ink" />
    <StatCard
      label="Spam blocked, 30 days"
      value={count(totals.spam_30d)}
      tone="slate"
      detail={totals.spam_30d ? 'Never reached a lead' : 'None'}
    />
  </div>
</div>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-bottom:32px">
    {#if actionError}
      <div style="margin-bottom:16px">
        <NextAction label="That did not work" text={actionError} tone="rust" />
      </div>
    {/if}

    {#if creating}
      <!-- A name and nothing else. The fields are chosen on the form's own
           page: a form with no email field cannot be published at all, so
           asking for the field list from a one-line panel would put the
           editor on two pages. This redirects straight there. -->
      <form
        method="POST"
        action="?/create"
        use:enhance={working}
        class="v2-card"
        style="padding:14px 15px;margin-bottom:18px;display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap"
      >
        <div style="flex:1;min-width:220px">
          <label class="v2-label" for="form-name" style="display:block;margin-bottom:4px">
            What is this form for?
          </label>
          <input
            id="form-name"
            name="name"
            required
            maxlength="255"
            class="v2-input"
            style="width:100%"
            placeholder="e.g. Contact us"
          />
        </div>
        <button class="v2-btn v2-btn-primary" disabled={busy}>Create and add fields</button>
        <button type="button" class="v2-btn" disabled={busy} onclick={() => (creating = false)}>
          Cancel
        </button>
      </form>
    {/if}

    {#if !data.forms.length}
      <EmptyState
        title="No web forms yet"
        body={data.canManage
          ? 'A web form is a page you embed on your own site. What people fill in becomes a lead here, with no login and no copy-pasting.'
          : 'Nobody has built a web form for this organisation yet. An admin can create one.'}
      >
        {#snippet actions()}
          {#if data.canManage}
            <button class="v2-btn v2-btn-primary" onclick={() => (creating = true)}>
              <Plus />New form
            </button>
          {/if}
        {/snippet}
      </EmptyState>
    {:else}
      <div class="v2-table-wrap">
        <table class="v2-table">
          <thead>
            <tr>
              <th>Form</th>
              <th>State</th>
              <th class="v2-r">Submissions</th>
              <th data-m="hide">Created</th>
              {#if data.canManage}<th class="v2-r">Actions</th>{/if}
            </tr>
          </thead>
          <tbody>
            {#each data.forms as f (f.id)}
              <!-- Published, embedded, and silent for the whole window. The
                   usual cause is the snippet having been taken off the page it
                   was pasted onto, which nothing else here would ever tell you. -->
              {@const quiet = f.is_published && f.submission_count === 0}
              <tr>
                <td>
                  <a
                    class="v2-row-link"
                    href={resolve(asInternalPath(`/settings/web-forms/${f.id}`))}
                  >
                    <div class="v2-table-primary">{f.name}</div>
                    <!-- The silent-form note sits here, on the descriptive
                         line, rather than under the count it describes. The
                         count cell is `.v2-num`, and mono is numerals only;
                         prose inheriting that face reads as a typo. -->
                    <div class="v2-table-secondary">
                      {f.field_count}
                      {f.field_count === 1 ? 'field' : 'fields'}
                      {#if quiet}
                        <span style="color:var(--v2-clay);font-weight:600">
                          · live but silent
                        </span>
                      {/if}
                    </div>
                  </a>
                </td>
                <td data-m="tag">
                  <Pill tone={f.is_published ? 'moss' : 'slate'}>
                    {f.is_published ? 'Published' : 'Draft'}
                  </Pill>
                </td>
                <td class="v2-r v2-num" data-m="meta" data-l="submissions">
                  {count(f.submission_count)}
                </td>
                <td data-m="hide" class="v2-muted">{shortDate(f.created_at)}</td>
                {#if data.canManage}
                  <td class="v2-r">
                    <span style="display:inline-flex;gap:7px;align-items:center;flex-wrap:wrap">
                      {#if f.is_published}
                        <!-- Two clicks. Unpublishing takes a live form off a
                             customer's site: the embed keeps rendering and every
                             submission from then on is refused. -->
                        <ConfirmAction
                          action="?/unpublish"
                          label="Unpublish"
                          confirmLabel="Unpublish it"
                          explain="Stops accepting submissions. The embed stays on the site and starts refusing people."
                          hidden={{ id: f.id }}
                        />
                      {:else}
                        <form method="POST" action="?/publish" use:enhance={working}>
                          <input type="hidden" name="id" value={f.id} />
                          <button class="v2-btn v2-btn-sm" disabled={busy}>Publish</button>
                        </form>
                      {/if}
                      <ConfirmAction
                        action="?/delete"
                        label="Delete"
                        confirmLabel="Delete permanently"
                        explain="Removes the form and its submission history. Leads already created stay."
                        hidden={{ id: f.id }}
                      />
                    </span>
                  </td>
                {/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>

      {#if data.truncated}
        <p class="v2-sub" style="font-size:12px;margin:12px 0 0">
          Showing the {data.forms.length} most recent of
          <span class="v2-num">{count(totals.count)}</span>. The rest are reachable through the API.
        </p>
      {/if}
    {/if}

    <div
      style="display:flex;gap:10px;align-items:flex-start;margin-top:20px;padding:14px 16px;border:1px solid var(--v2-line);border-radius:var(--v2-radius)"
    >
      <ShieldAlert size={16} style="color:var(--v2-clay);flex:none;margin-top:1px" />
      <div>
        <div style="font-weight:600;font-size:13px">A published form accepts posts from anyone</div>
        <p class="v2-sub" style="font-size:12px;margin:4px 0 0">
          It has to: the whole point is that a stranger can fill it in without an account. A
          honeypot field, per-form and per-address rate limits, and disposable-address rejection are
          always on, and each form can add a Cloudflare Turnstile challenge of its own. Publish only
          the forms you are embedding, and unpublish one the moment you take its snippet off your
          site.
        </p>
      </div>
    </div>
  </div>
</div>
