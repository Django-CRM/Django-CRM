<script>
  import { resolve } from '$app/paths';
  /**
   * A new invoice template: name, the two brand colours, an optional logo, and
   * the boilerplate text (notes, terms, footer) new invoices start with.
   *
   * SCOPE, ON PURPOSE. No `template_html` / `template_css` input anywhere on
   * this page, and no `{@html}` anywhere in this app. Both fields are org-
   * authored markup that WeasyPrint renders into a PDF server-side. A new
   * template starts from the built-in layout, and replacing that whole
   * document is a deliberate follow-up on an existing template rather than
   * part of naming a new one, so the edit page owns those two fields. See
   * `templates.js` for the full reasoning, including the older reason this
   * page gave, which the editor route made obsolete.
   *
   * VALIDATION HERE IS A UX HINT, NOT A RULE. `POST /api/invoices/templates/`
   * is admin-gated (`_forbid_non_admin_template`) and enforces that
   * regardless of what this page shows; curl and the mobile client reach the
   * API without passing through here. The two colour inputs use
   * `type="color"` so the browser can only ever submit a valid six-digit hex
   * value, and `_validate_hex_color` in the serializer refuses anything else,
   * which is the check that counts.
   */
  import { enhance } from '$app/forms';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import { ChevronRight, Lock } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  let values = $derived(form?.values ?? {});

  /* True from submit until the response lands, so a second press during a
     slow save cannot post the template again and create two templates. */
  let saving = $state(false);
</script>

<PageHeader title="New template" record center width="62ch">
  {#snippet crumb()}
    <a href={resolve('/invoices/templates')}>Templates</a>
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
            Invoice templates are shared config for the whole org, every invoice's look, so only an
            administrator can create one. You can still see how existing templates look on the
            templates page.
          </div>
        </div>
      </div>
      <a class="v2-btn" href={resolve('/invoices/templates')} style="margin-top:16px"
        >Back to templates</a
      >
    </div>
  {:else}
    <form
      method="POST"
      action="?/create"
      enctype="multipart/form-data"
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
          maxlength="100"
          value={values.name ?? ''}
          placeholder="Standard invoice"
        />
      </label>

      <div class="color-row">
        <label class="color-field">
          <span class="v2-label">Primary colour</span>
          <input
            class="color-swatch"
            type="color"
            name="primary_color"
            value={values.primary_color || '#3B82F6'}
          />
        </label>
        <label class="color-field">
          <span class="v2-label">Secondary colour</span>
          <input
            class="color-swatch"
            type="color"
            name="secondary_color"
            value={values.secondary_color || '#1E40AF'}
          />
        </label>
      </div>
      <p class="v2-sub" style="font-size:11.5px;margin:-6px 0 16px">
        Picked, not typed, so the value sent is always a valid six digit hex.
      </p>

      <label class="v2-field">
        <span class="v2-label">Logo <span class="opt">(optional)</span></span>
        <input class="v2-input" type="file" name="logo" accept="image/*" />
      </label>

      <label class="v2-field">
        <span class="v2-label">Default notes <span class="opt">(optional)</span></span>
        <textarea
          class="v2-input"
          name="default_notes"
          rows="3"
          placeholder="Thanks for your business.">{values.default_notes ?? ''}</textarea
        >
      </label>

      <label class="v2-field">
        <span class="v2-label">Default terms <span class="opt">(optional)</span></span>
        <textarea
          class="v2-input"
          name="default_terms"
          rows="3"
          placeholder="Payment due within 30 days.">{values.default_terms ?? ''}</textarea
        >
      </label>

      <label class="v2-field">
        <span class="v2-label">Footer text <span class="opt">(optional)</span></span>
        <textarea class="v2-input" name="footer_text" rows="2">{values.footer_text ?? ''}</textarea>
      </label>

      <label class="flag">
        <input type="checkbox" name="is_default" checked={values.is_default === true} />
        <span>
          <strong>Make this the default template</strong>
          <span class="v2-sub">
            Only one template can be the default at a time. Turning this on replaces whichever
            template holds it now, new invoices will print with this one instead.
          </span>
        </span>
      </label>

      <div style="display:flex;gap:9px;margin-top:6px">
        <button class="v2-btn v2-btn-primary" type="submit" disabled={saving}>
          {saving ? 'Creating…' : 'Create template'}
        </button>
        <a class="v2-btn" href={resolve('/invoices/templates')}>Cancel</a>
      </div>
    </form>
  {/if}
</div>

<style>
  .opt {
    text-transform: none;
    font-weight: 500;
    letter-spacing: 0;
    color: var(--v2-slate);
  }
  .color-row {
    display: flex;
    gap: 16px;
  }
  .color-field {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .color-swatch {
    width: 56px;
    height: 38px;
    padding: 3px;
    border: 1px solid var(--v2-line);
    border-radius: 8px;
    background: var(--v2-card);
    cursor: pointer;
  }
  .flag {
    display: flex;
    gap: 9px;
    align-items: flex-start;
    font-size: 13px;
    margin: 4px 0 18px;
  }
  .flag span {
    display: block;
  }
  .flag .v2-sub {
    display: block;
    font-size: 11.5px;
    margin-top: 2px;
  }
</style>
