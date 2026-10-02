<script>
  import { resolve } from '$app/paths';
  import '../../../../app.css';
  import '$lib/v2/styles/v2.css';
  import imgLogo from '$lib/assets/images/logo.png';

  let { data, form } = $props();

  const error = $derived(form?.error ?? data.error);
  let submitting = $state(false);
</script>

<svelte:head>
  <title>Sign in · BottleCRM</title>
  <meta name="referrer" content="no-referrer" />
</svelte:head>

<div class="v2-root v2-auth">
  <div class="v2-auth-box">
    <a href={resolve('/')} class="v2-auth-brand">
      <img src={imgLogo} alt="" />
      <b>BottleCRM</b>
    </a>

    <div class="v2-auth-card" style="text-align:center">
      {#if error}
        <div class="v2-auth-head" style="margin-bottom:16px">
          <h1>Link expired or invalid</h1>
          <p>{error}</p>
        </div>
        <a href={resolve('/login')} class="v2-btn v2-btn-block">Back to sign in</a>
      {:else}
        <div class="v2-auth-head" style="margin-bottom:16px">
          <h1>Sign in to BottleCRM</h1>
          <p>Press the button to finish signing in.</p>
        </div>
        <!-- A plain submit, never an automatic one: see +page.server.js. No
             `action` attribute, so it posts back to this URL, token included. -->
        <form method="POST" onsubmit={() => (submitting = true)}>
          <button type="submit" class="v2-btn v2-btn-primary v2-btn-block" disabled={submitting}>
            {#if submitting}
              <span class="v2-spin"></span>
              <span>Signing in…</span>
            {:else}
              <span>Continue to BottleCRM</span>
            {/if}
          </button>
        </form>
      {/if}
    </div>
  </div>
</div>
