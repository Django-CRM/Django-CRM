<script>
  import { resolve } from '$app/paths';
  /**
   * A new catalogue product: a name, a list price, and where it sits.
   *
   * VALIDATION HERE IS A UX HINT, NOT A RULE. The serializer requires a name and
   * a numeric price and rejects a duplicate SKU within the org; a non-admin is
   * refused outright. curl and the mobile client reach the API without passing
   * through this page. The view is the trust boundary (see CLAUDE.md).
   */
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import { enhance } from '$app/forms';
  import { ChevronRight, Lock } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  let values = $derived(form?.values ?? {});

  /* True from submit until the response lands, so a second press during a
     slow save cannot post the product again and create two catalogue rows. */
  let saving = $state(false);
</script>

<PageHeader title="New product" record center width="62ch">
  {#snippet crumb()}
    <a href={resolve('/invoices/products')}>Products</a>
    <ChevronRight size={12} />
    <span>New</span>
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  {#if !data.can_manage}
    <div class="v2-pad" style="padding-top:24px;max-width:56ch;margin-left:auto;margin-right:auto">
      <div class="v2-next" role="note">
        <Lock size={17} style="flex:none" />
        <div class="v2-next-body">
          <div style="font-weight:600">Admins only</div>
          <div class="v2-sub" style="margin-top:2px">
            The product catalogue is shared across the org, so only an administrator can add to it.
            You can still use any product on your invoices and estimates.
          </div>
        </div>
      </div>
      <a class="v2-btn" href={resolve('/invoices/products')} style="margin-top:16px"
        >Back to products</a
      >
    </div>
  {:else}
    <form
      method="POST"
      action="?/create"
      use:enhance={({ cancel }) => {
        if (saving) return cancel();
        saving = true;
        return async ({ update }) => {
          await update();
          saving = false;
        };
      }}
      class="v2-pad"
      style="padding-top:18px;padding-bottom:36px;max-width:62ch;margin-left:auto;margin-right:auto"
    >
      {#if form?.error}
        <p style="color:var(--v2-rust);font-size:12.5px;margin:0 0 14px" role="alert">
          {form.error}
        </p>
      {/if}

      <label class="v2-field">
        <span class="v2-label">Name</span>
        <input
          class="v2-input"
          name="name"
          required
          maxlength="255"
          value={values.name ?? ''}
          placeholder="Platform licence, per seat"
        />
      </label>

      <div style="display:flex;gap:12px;flex-wrap:wrap">
        <label class="v2-field" style="flex:2;min-width:180px">
          <span class="v2-label">List price</span>
          <input
            class="v2-input"
            name="price"
            type="number"
            min="0"
            step="0.01"
            required
            value={values.price ?? ''}
            placeholder="100.00"
          />
        </label>
        <label class="v2-field" style="flex:1;min-width:130px">
          <span class="v2-label">Currency</span>
          <select class="v2-input" name="currency" value={values.currency ?? 'USD'}>
            {#each data.currencies as c (c.code)}
              <option value={c.code}>{c.label}</option>
            {/each}
          </select>
        </label>
      </div>

      <div style="display:flex;gap:12px;flex-wrap:wrap">
        <label class="v2-field" style="flex:1;min-width:160px">
          <span class="v2-label">Category</span>
          <input
            class="v2-input"
            name="category"
            maxlength="100"
            value={values.category ?? ''}
            placeholder="Licence, Module, Service…"
          />
        </label>
        <label class="v2-field" style="flex:1;min-width:160px">
          <span class="v2-label">SKU</span>
          <input
            class="v2-input"
            name="sku"
            maxlength="100"
            value={values.sku ?? ''}
            placeholder="PLAT-SEAT"
          />
        </label>
      </div>
      <p class="v2-sub" style="font-size:11.5px;margin:-6px 0 16px">
        Category groups the catalogue; leave it blank and the product sits under "Uncategorised". A
        SKU is optional but must be unique here if you set one.
      </p>

      <label class="v2-field">
        <span class="v2-label">Availability</span>
        <select
          class="v2-input"
          name="is_active"
          value={values.is_active === false ? 'false' : 'true'}
        >
          <option value="true">Sellable, appears in the line-item picker</option>
          <option value="false">Retired, kept for history, hidden from the picker</option>
        </select>
      </label>

      <label class="v2-field">
        <span class="v2-label">Description</span>
        <textarea
          class="v2-input"
          name="description"
          rows="3"
          placeholder="What it is, in the words a client would see on an invoice."
          >{values.description ?? ''}</textarea
        >
      </label>

      <div style="display:flex;gap:9px;margin-top:6px">
        <button class="v2-btn v2-btn-primary" type="submit" disabled={saving}>
          {saving ? 'Adding…' : 'Add product'}
        </button>
        <a class="v2-btn" href={resolve('/invoices/products')}>Cancel</a>
      </div>
    </form>
  {/if}
</div>
