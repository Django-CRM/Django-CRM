<script lang="ts">
  /** A key shown once, with a copy button. The page that gets it does not keep it. */
  import { Check, Copy } from '@lucide/svelte';
  import { KEY_NOTICE } from '#lib/v2/connected-apps.js';

  let { title, secret }: { title: string; secret: string } = $props();
  let copied = $state(false);
  let card = $state<HTMLElement | null>(null);

  // A long page at phone width would leave the new key off-screen.
  $effect(() => {
    if (card) {
      card.scrollIntoView({ block: 'center' });
      card.focus({ preventScroll: true });
    }
  });

  async function copyKey() {
    try {
      await navigator.clipboard.writeText(secret);
      copied = true;
      setTimeout(() => (copied = false), 1600);
    } catch {
      // Clipboard blocked: the key is on screen to select by hand.
    }
  }
</script>

<div
  bind:this={card}
  tabindex="-1"
  class="v2-card"
  style="outline:none;padding:15px 16px;margin-bottom:18px;border-color:color-mix(in srgb, var(--v2-moss) 40%, var(--v2-line))"
>
  <div style="font-weight:650;font-size:13px">{title}</div>
  <p class="v2-sub" style="font-size:12px;margin:4px 0 10px">{KEY_NOTICE}</p>
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <code
      class="v2-num"
      style="flex:1;min-width:0;background:var(--v2-bg-sunk);border:1px solid var(--v2-line);border-radius:var(--v2-radius);padding:9px 11px;font-size:12.5px;word-break:break-all"
    >
      {secret}
    </code>
    <button type="button" class="v2-btn v2-btn-sm" onclick={copyKey}>
      {#if copied}<Check size={13} />Copied{:else}<Copy size={13} />Copy{/if}
    </button>
  </div>
</div>
