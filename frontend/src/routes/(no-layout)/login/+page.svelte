<script lang="ts">
  import '../../../app.css';
  import '#lib/v2/styles/v2.css';
  import { enhance, type SubmitFunction } from '$app/forms';
  import imgGoogle from '#lib/assets/images/google.svg';
  import imgLogo from '#lib/assets/images/logo.png';
  import { ArrowRight, Eye, EyeOff, LockKeyhole, ShieldCheck, Users, ChartNoAxesCombined, Headset } from '@lucide/svelte';
  import type { PageProps } from './$types';

  let { data, form }: PageProps = $props();
  let email = $state('');
  let password = $state('');
  let showPassword = $state(false);
  let isLoading = $state(false);
  let isGoogleLoading = $state(false);
  let loginError = $state('');

  function handleGoogleLogin() {
    isGoogleLoading = true;
  }

  const handlePasswordLogin: SubmitFunction = () => {
    isLoading = true;
    loginError = '';
    return async ({ result }) => {
      isLoading = false;
      if (result?.type === 'failure') {
        loginError = String(result.data?.error || 'We could not sign you in. Please try again.');
      } else if (result?.type === 'error') {
        loginError = 'Something went wrong. Please try again.';
      }
    };
  };
</script>

<svelte:head>
  <title>Sign in · Godan CRM</title>
  <meta name="description" content="Securely sign in to your Godan CRM workspace." />
  <meta name="theme-color" content="#101c2e" />
</svelte:head>

<div class="v2-root v2-auth v2-auth-modern">
  <section class="v2-auth-showcase" aria-label="Godan CRM">
    <a href="/" class="v2-auth-showcase-brand">
      <img src={imgLogo} alt="" />
      <span>Godan <b>CRM</b></span>
    </a>

    <div class="v2-auth-showcase-content">
      <div class="v2-auth-eyebrow"><span></span> YOUR BUSINESS, IN SYNC</div>
      <h1>Build stronger<br />customer relationships.</h1>
      <p class="v2-auth-showcase-copy">
        One clear view of your customers, sales pipeline, and team — so your business can move forward with confidence.
      </p>

      <div class="v2-auth-feature-list">
        <div><span class="v2-auth-feature-icon"><Users size={18} /></span><span><b>Customer relationships</b><small>Keep every conversation and contact connected.</small></span></div>
        <div><span class="v2-auth-feature-icon"><ChartNoAxesCombined size={18} /></span><span><b>Sales visibility</b><small>See opportunities, progress, and what needs attention.</small></span></div>
        <div><span class="v2-auth-feature-icon"><Headset size={18} /></span><span><b>Team collaboration</b><small>Give everyone a shared view of the work.</small></span></div>
      </div>
    </div>

    <div class="v2-auth-showcase-bottom">
      <ShieldCheck size={16} />
      <span>Secure access to your business workspace</span>
    </div>
  </section>

  <section class="v2-auth-panel">
    <div class="v2-auth-box v2-auth-box-modern">
      <a href="/" class="v2-auth-brand v2-auth-brand-mobile">
        <img src={imgLogo} alt="" />
        <b>Godan CRM</b>
      </a>

      <div class="v2-auth-card v2-auth-card-modern">
        <div class="v2-auth-head v2-auth-head-modern">
          <div class="v2-auth-card-kicker">WELCOME BACK</div>
          <h2>Sign in to your account</h2>
          <p>Enter your work email and password to continue.</p>
        </div>

        {#if data.error}
          <div class="v2-auth-note v2-auth-note-bad" role="alert">{data.error}</div>
        {/if}

        <form method="POST" use:enhance={handlePasswordLogin} class="v2-auth-form">
          <div class="v2-auth-field">
            <label for="email">Work email</label>
            <input
              id="email"
              name="email"
              type="email"
              class="v2-input"
              placeholder="you@company.com"
              autocomplete="username"
              autocapitalize="none"
              spellcheck="false"
              required
              bind:value={email}
              disabled={isLoading}
            />
          </div>

          <div class="v2-auth-field">
            <div class="v2-auth-label-row">
              <label for="password">Password</label>
              <a href="/login/forgot-password" class="v2-auth-forgot-link">Forgot password?</a>
            </div>
            <div class="v2-auth-password-wrap">
              <LockKeyhole size={16} class="v2-auth-input-icon" />
              <input
                id="password"
                name="password"
                type={showPassword ? 'text' : 'password'}
                class="v2-input"
                placeholder="Enter your password"
                autocomplete="current-password"
                required
                bind:value={password}
                disabled={isLoading}
              />
              <button
                type="button"
                class="v2-auth-password-toggle"
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                aria-pressed={showPassword}
                onclick={() => (showPassword = !showPassword)}
                disabled={isLoading}
              >
                {#if showPassword}<EyeOff size={17} />{:else}<Eye size={17} />{/if}
              </button>
            </div>
          </div>

          {#if loginError || form?.error}
            <div class="v2-auth-note v2-auth-note-bad" role="alert">{loginError || form?.error}</div>
          {/if}

          <button type="submit" class="v2-btn v2-btn-primary v2-btn-block v2-auth-submit" disabled={isLoading}>
            {#if isLoading}
              <span class="v2-spin"></span><span>Verifying credentials…</span>
            {:else}
              <span>Continue</span><ArrowRight size={17} />
            {/if}
          </button>
        </form>

        <div class="v2-auth-divider">or continue with</div>

        {#if data.google_url}
          <a
            href={data.google_url}
            rel="external"
            onclick={handleGoogleLogin}
            class="v2-btn v2-btn-block v2-auth-google"
            aria-busy={isGoogleLoading}
          >
            {#if isGoogleLoading}
              <span class="v2-spin"></span><span>Connecting to Google…</span>
            {:else}
              <img src={imgGoogle} alt="" class="v2-auth-gicon" />
              <span>Continue with Google</span>
            {/if}
          </a>
        {/if}

        <div class="v2-auth-security-note">
          <ShieldCheck size={16} />
          <span>For your security, we'll email you a one-time verification code after checking your password.</span>
        </div>
      </div>

      <p class="v2-auth-copyright">© {new Date().getFullYear()} Godan CRM. All rights reserved.</p>
    </div>
  </section>
</div>
