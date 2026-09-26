<script>
  /**
   * A signing secret, shown in the one response that carries it (a create or
   * a rotate) and gone on the next load. The list and detail responses only
   * ever carry `secret_hint`.
   *
   * @type {{ title: string, secret: string }}
   */
  import { Copy, Check } from '@lucide/svelte';

  let { title, secret } = $props();
  let copied = $state(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(secret);
      copied = true;
      setTimeout(() => (copied = false), 1600);
    } catch {
      // Clipboard blocked (no https or no permission). The value is on screen
      // to select by hand.
    }
  }
</script>

<div
  class="v2-card"
  style="padding:15px 16px;margin-bottom:18px;border-color:color-mix(in srgb, var(--v2-moss) 40%, var(--v2-line))"
>
  <div style="font-weight:650;font-size:13px">{title}</div>
  <p class="v2-sub" style="font-size:12px;margin:4px 0 10px">
    This is the only time the signing secret is shown. Store it where your receiver can read it; use
    Rotate to replace it if it is lost.
  </p>
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <code
      class="v2-num"
      style="flex:1;min-width:200px;background:var(--v2-bg-sunk);border:1px solid var(--v2-line);border-radius:var(--v2-radius);padding:9px 11px;font-size:12.5px;word-break:break-all"
    >
      {secret}
    </code>
    <button class="v2-btn" type="button" style="min-height:44px" onclick={copy}>
      {#if copied}<Check size={13} />Copied{:else}<Copy size={13} />Copy{/if}
    </button>
  </div>
</div>
