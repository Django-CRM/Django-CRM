<script lang="ts">
  import '../../../../app.css';
  import '#lib/v2/styles/v2.css';
  import imgLogo from '#lib/assets/images/logo.png';
  import { ArrowLeft, CheckCircle2, MailCheck, ShieldCheck } from '@lucide/svelte';
  import type { PageProps } from './$types';

  let { data, form }: PageProps = $props();
  let code = $state('');
  let isVerifying = $state(false);
  let isResending = $state(false);

  function onCodeInput(event: Event) {
    const target = event.currentTarget as HTMLInputElement;
    code = target.value.replace(/\D/g, '').slice(0, 6);
  }
</script>

<svelte:head>
  <title>Verify your email · Godan CRM</title>
  <meta name="description" content="Enter your one-time sign-in code to access Godan CRM." />
  <meta name="referrer" content="same-origin" />
</svelte:head>

<div class="v2-root v2-auth v2-auth-modern v2-auth-code-page">
  <div class="v2-auth-code-glow" aria-hidden="true"></div>
  <div class="v2-auth-box v2-auth-box-modern v2-auth-code-box">
    <a href="/" class="v2-auth-brand">
      <img src={imgLogo} alt="" />
      <b>Godan CRM</b>
    </a>

    <div class="v2-auth-card v2-auth-card-modern v2-auth-code-card">
      <div class="v2-auth-code-icon"><MailCheck size={25} /></div>
      <div class="v2-auth-head v2-auth-head-modern">
        <div class="v2-auth-card-kicker">ONE MORE STEP</div>
        <h1>Check your email</h1>
        <p>We sent a six-digit sign-in code to <strong>{data.maskedEmail}</strong>. Enter it below to securely access your workspace.</p>
      </div>

      {#if form?.message}
        <div class="v2-auth-note v2-auth-note-ok" role="status"><CheckCircle2 size={16} />{form.message}</div>
      {/if}
      {#if form?.error}
        <div class="v2-auth-note v2-auth-note-bad" role="alert">{form.error}</div>
      {/if}

      <form method="POST" onsubmit={() => (isVerifying = true)} class="v2-auth-form">
        <div class="v2-auth-field">
          <label for="code">Verification code</label>
          <input
            id="code"
            name="code"
            type="text"
            class="v2-input v2-auth-otp-input"
            placeholder="000000"
            inputmode="numeric"
            autocomplete="one-time-code"
            pattern="[0-9]{6}"
            maxlength="6"
            minlength="6"
            required
            autofocus
            bind:value={code}
            oninput={onCodeInput}
            aria-describedby="otp-help"
            disabled={isVerifying}
          />
          <small id="otp-help" class="v2-auth-help">The code expires in 10 minutes and can only be used once.</small>
        </div>

        <button type="submit" class="v2-btn v2-btn-primary v2-btn-block v2-auth-submit" disabled={isVerifying || code.length !== 6}>
          {#if isVerifying}
            <span class="v2-spin"></span><span>Verifying…</span>
          {:else}
            <span>Verify and sign in</span>
          {/if}
        </button>
      </form>

      <div class="v2-auth-resend">
        <span>Didn't receive the email?</span>
        <form method="POST" action="?/resend" onsubmit={() => (isResending = true)}>
          <button type="submit" disabled={isResending}>
            {#if isResending}Sending…{:else}Resend code{/if}
          </button>
        </form>
      </div>
    </div>

    <a href="/login" class="v2-auth-back"><ArrowLeft size={15} /> Back to sign in</a>
    <div class="v2-auth-security-note v2-auth-code-security">
      <ShieldCheck size={16} />
      <span>Never share your verification code. Godan support will never ask you for it.</span>
    </div>
    <p class="v2-auth-copyright">© {new Date().getFullYear()} Godan CRM. All rights reserved.</p>
  </div>
</div>
