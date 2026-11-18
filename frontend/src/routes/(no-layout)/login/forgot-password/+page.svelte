<script lang="ts">
 import '../../../../app.css';import '#lib/v2/styles/v2.css';import imgLogo from '#lib/assets/images/logo.png';import {ArrowLeft,Mail,KeyRound,ShieldCheck} from '@lucide/svelte';import type {PageProps} from './$types';let {form}:PageProps=$props();let email=$state('');
</script>
<svelte:head><title>Recover your account · Godan CRM</title></svelte:head>
<div class="v2-root v2-auth v2-auth-modern v2-auth-code-page"><div class="v2-auth-code-glow"></div><div class="v2-auth-box v2-auth-box-modern v2-auth-code-box"><a href="/" class="v2-auth-brand"><img src={imgLogo} alt=""/><b>Godan CRM</b></a><div class="v2-auth-card v2-auth-card-modern v2-auth-code-card">
<div class="v2-auth-head v2-auth-head-modern"><div class="v2-auth-card-kicker">ACCOUNT RECOVERY</div><h1>Get back to business.</h1><p>Choose how you want to regain access. Start with your account email.</p></div>
{#if form?.error}<div class="v2-auth-note v2-auth-note-bad" role="alert">{form.error}</div>{/if}{#if form?.message}<div class="v2-auth-note v2-auth-note-ok" role="status">{form.message}</div>{/if}
<div class="v2-auth-field"><label for="email">Work email</label><input id="email" class="v2-input" type="email" bind:value={email} placeholder="you@company.com" autocomplete="email" required/></div>
<div class="v2-auth-recovery-options"><form method="POST" action="?/loginOtp"><input type="hidden" name="email" value={email}/><button class="v2-btn v2-btn-primary v2-btn-block" disabled={!email}><Mail size={17}/> Email login OTP <span>Sign in directly after verification</span></button></form>
<form method="POST" action="?/reset"><input type="hidden" name="email" value={email}/><button class="v2-btn v2-btn-block v2-auth-google" disabled={!email}><KeyRound size={17}/> Password reset <span>Verify email, then set a new password</span></button></form></div>
<div class="v2-auth-security-note"><ShieldCheck size={16}/><span>Codes expire in 10 minutes, work once, and are protected by rate limits.</span></div></div><a href="/login" class="v2-auth-back"><ArrowLeft size={15}/> Back to sign in</a></div></div>
