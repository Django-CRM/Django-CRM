<script>
  /**
   * "Possible duplicates" beside a create form, while it is being typed.
   *
   * Debounced so a normal typing speed asks once per pause, and sequenced so a
   * slow early answer cannot overwrite a later one (the portal's article
   * suggestions use the same pair). The API only ever answers with records
   * the person may open. Saving is never blocked: two people can share a name,
   * and the person typing is the one who knows.
   *
   * Each hit opens in a new tab, so checking one does not throw away the form.
   */
  import { resolve } from '$app/paths';
  import { TriangleAlert } from '@lucide/svelte';

  /** @type {{ module: 'leads' | 'contacts' | 'accounts', values: Record<string, string> }} */
  let { module, values } = $props();

  /** @type {Array<{ id: string, name: string, matched_on: string }>} */
  let hits = $state([]);
  let latest = 0;

  /**
   * Whether a value is worth asking about. The API ignores weaker ones anyway;
   * this saves the round trip on every early keystroke.
   * @param {string} field
   * @param {string} value
   */
  function usable(field, value) {
    if (field === 'email') return /@.+\./.test(value);
    if (field === 'phone') return value.replace(/\D/g, '').length >= 7;
    if (field === 'website') return value.includes('.');
    return value.length >= 2;
  }

  $effect(() => {
    /** @type {Record<string, string>} */
    const criteria = {};
    for (const [field, raw] of Object.entries(values)) {
      const value = (raw ?? '').trim();
      if (value && usable(field, value)) criteria[field] = value;
    }
    const ticket = ++latest;
    if (!Object.keys(criteria).length) {
      hits = [];
      return;
    }
    const timer = setTimeout(async () => {
      try {
        // A body, not a query string: what is being typed is an email address
        // and a phone number, and a URL is written to access logs.
        const response = await fetch(`/api/duplicates/${module}`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(criteria)
        });
        if (!response.ok) return;
        const body = await response.json();
        if (ticket === latest) hits = body.duplicates ?? [];
      } catch {
        // A hint, not a gate: a failed check leaves the form as it was.
      }
    }, 400);
    return () => clearTimeout(timer);
  });
</script>

{#if hits.length}
  <div class="v2-next dup-notice" role="status" aria-live="polite">
    <TriangleAlert size={17} style="color:var(--v2-clay);flex:none" />
    <div class="v2-next-body">
      <div style="font-weight:600">
        Possible duplicate{hits.length === 1 ? '' : 's'}
      </div>
      <ul>
        {#each hits as hit (hit.id)}
          <li>
            <a href={resolve(`/${module}/${hit.id}`)} target="_blank" rel="noopener">{hit.name}</a>
            <span class="v2-sub">· same {hit.matched_on}</span>
          </li>
        {/each}
      </ul>
      <div class="v2-sub" style="margin-top:2px">
        You can still save. If it is the same one, open it instead, or merge the two afterwards.
      </div>
    </div>
  </div>
{/if}

<style>
  .dup-notice {
    background: color-mix(in srgb, var(--v2-clay) 8%, transparent);
    border-color: color-mix(in srgb, var(--v2-clay) 28%, transparent);
    margin-bottom: 18px;
  }
  ul {
    margin: 4px 0;
    padding: 0;
    list-style: none;
  }
  li {
    padding: 3px 0;
    overflow-wrap: anywhere;
  }
  li a {
    font-weight: 600;
    color: var(--v2-clay);
    /* A tap target on a phone, not a line of text. */
    display: inline-block;
    min-height: 28px;
    line-height: 28px;
  }
  @media (max-width: 768px) {
    li a {
      min-height: 44px;
      line-height: 44px;
    }
  }
</style>
