<script>
  /**
   * "Possible duplicates" on a lead, contact or account page, shown only when
   * there are some. A warning that is always on is one people learn to scroll
   * past.
   *
   * Merge leads to the side-by-side page, where the person picks which record
   * to keep. It is offered when the caller could merge in at least one
   * direction: keeping one record means deleting the other, and only an admin
   * or a record's creator may delete it. The API enforces that whatever this
   * shows; `can_delete` only spares somebody a refusal.
   */
  import { resolve } from '$app/paths';

  /** @type {{
   *   module: 'leads' | 'contacts' | 'accounts',
   *   id: string,
   *   canDelete: boolean,
   *   duplicates: Array<{ id: string, name: string, matched_on: string, can_delete: boolean }>
   * }} */
  let { module, id, canDelete, duplicates } = $props();

  // Said once under the list, and only when no row can be merged: repeated
  // beside every row it reads as a wall of refusals. The phone does the same.
  let noneMergeable = $derived(!canDelete && duplicates.every((d) => !d.can_delete));
</script>

{#if duplicates.length}
  <section class="dup" aria-labelledby="dup-title">
    <div id="dup-title" class="v2-label" style="color:var(--v2-clay);margin-bottom:4px">
      Possible duplicate{duplicates.length === 1 ? '' : 's'}
    </div>
    {#each duplicates as d (d.id)}
      <div class="row">
        <div style="flex:1;min-width:0">
          <a href={resolve(`/${module}/${d.id}`)}>{d.name}</a>
          <span class="v2-sub"> · same {d.matched_on}</span>
        </div>
        {#if canDelete || d.can_delete}
          <a class="v2-btn v2-btn-sm merge" href={resolve(`/${module}/${id}/merge?with=${d.id}`)}>
            Compare
          </a>
        {/if}
      </div>
    {/each}
    {#if noneMergeable}
      <p class="v2-sub" style="font-size:11.5px;margin:4px 0 0">
        Only an admin or whoever created one of them can merge these.
      </p>
    {/if}
  </section>
{/if}

<style>
  .dup {
    border: 1px solid var(--v2-line);
    border-left: 3px solid var(--v2-clay);
    border-radius: var(--v2-radius);
    background: var(--v2-card);
    padding: 11px 14px;
    margin-bottom: 18px;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    padding: 6px 0;
    font-size: 12.5px;
    line-height: 1.55;
    overflow-wrap: anywhere;
  }
  .row + .row {
    border-top: 1px solid var(--v2-line-soft);
  }
  .row a:not(.v2-btn) {
    color: var(--v2-clay);
    font-weight: 600;
  }
  @media (max-width: 768px) {
    .merge,
    .row a:not(.v2-btn) {
      min-height: 44px;
      display: inline-flex;
      align-items: center;
    }
  }
</style>
