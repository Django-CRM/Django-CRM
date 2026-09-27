<script>
  import { resolve } from '$app/paths';
  /**
   * Your own account.
   *
   * The fields that are NOT editable here are the interesting ones. Role is
   * shown and cannot be changed from this page. The API refuses to let anyone
   * change their own role (ProfileSelfUpdateSerializer names only name and
   * phone), and an input that always fails is worse than no input. Same for the
   * organisation: which org you are in decides which rows you can see at all,
   * and it comes from the JWT, not from a form.
   *
   * Two things you CAN do: edit your name and phone (PATCH /profile/), and
   * switch org, a real action that re-issues the token rather than editing a
   * field, so it goes through its own action and the copy says so.
   */
  import { enhance } from '$app/forms';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import Avatar from '$lib/v2/components/Avatar.svelte';
  import { relativeDays, shortDate, count } from '$lib/v2/format.js';
  import { ROLE_LABEL, ROLE_TONE } from '$lib/v2/enums.js';
  import { KeyRound, Lock, ArrowLeftRight, CalendarDays } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  let p = $derived(data.profile);
  let name = $derived(`${p.user_details.first_name} ${p.user_details.last_name}`.trim());

  // Editing name + phone. The backend stores one `name` on User, so the form
  // offers a single full-name field rather than the split the header renders.
  let editing = $state(false);
  let editName = $state('');
  let editPhone = $state('');

  function openEdit() {
    editName = name;
    editPhone = p.phone || '';
    editing = true;
  }

  const onEdit = (/** @type {any} */ { formData }) => {
    // Only send the field the person actually changed. The PATCH treats an
    // absent field as "leave it alone", so an untouched phone is not
    // re-validated, which matters because some seeded numbers carry an
    // extension the validator rejects, and re-sending one would block a plain
    // name change. Same rule the leads form uses for its owner select.
    if ((formData.get('name') ?? '') === name) formData.delete('name');
    if ((formData.get('phone') ?? '') === (p.phone || '')) formData.delete('phone');

    return async (/** @type {any} */ { result, update }) => {
      if (result.type === 'success') {
        editing = false;
        await update(); // reloads the profile with the saved values
      } else {
        await update({ reset: false }); // keep what they typed, show the message
      }
    };
  };

  // `form` is shared by both actions; the switch action tags its failures.
  let editError = $derived(form?.scope === 'switch' ? '' : (form?.message ?? ''));
  let switchError = $derived(form?.scope === 'switch' ? (form?.message ?? '') : '');
</script>

<PageHeader title={name} record>
  {#snippet sub()}
    {ROLE_LABEL[p.role]} · {data.org.name} · joined {shortDate(p.joined_at)}
  {/snippet}
  {#snippet actions()}
    {#if !editing}
      <button class="v2-btn v2-btn-primary" onclick={openEdit}>Edit details</button>
    {/if}
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:18px;padding-bottom:32px">
    <div class="v2-split">
      <div>
        <div class="v2-label" style="margin-bottom:10px">You</div>

        {#if editing}
          <form
            class="v2-card"
            method="POST"
            action="?/edit"
            use:enhance={onEdit}
            style="padding:17px 18px;margin-bottom:20px"
          >
            <div class="v2-field">
              <label for="f-name">Full name</label>
              <input
                id="f-name"
                name="name"
                class="v2-input"
                bind:value={editName}
                maxlength="255"
              />
            </div>
            <div class="v2-field" style="margin-top:12px">
              <label for="f-phone">Phone</label>
              <input
                id="f-phone"
                name="phone"
                class="v2-input"
                bind:value={editPhone}
                placeholder="+44 20 7946 0100"
              />
              <p class="v2-hint">Digits and separators only. Leave blank to remove it.</p>
            </div>
            {#if editError}
              <p class="v2-error" style="margin-top:10px">{editError}</p>
            {/if}
            <div style="display:flex;gap:8px;margin-top:16px">
              <button class="v2-btn v2-btn-primary" type="submit">Save</button>
              <button class="v2-btn" type="button" onclick={() => (editing = false)}>Cancel</button>
            </div>
          </form>
        {:else}
          <div class="v2-card" style="padding:17px 18px;margin-bottom:20px">
            <div style="display:flex;gap:13px;align-items:center;margin-bottom:16px">
              <Avatar {name} size={46} />
              <div style="min-width:0">
                <div style="font-weight:640;font-size:15px">{name}</div>
                <div class="v2-sub" style="font-size:12.5px">{p.user_details.email}</div>
              </div>
            </div>
            <dl class="v2-kv">
              <dt>Phone</dt>
              <dd class="v2-num" style="font-size:12px">{p.phone || '—'}</dd>
              <dt>Teams</dt>
              <dd>{p.teams.join(', ') || '—'}</dd>
              <dt>Joined</dt>
              <dd>{shortDate(p.joined_at)}</dd>
              <dt>Last signed in</dt>
              <dd>{relativeDays(p.last_login)}</dd>
            </dl>
          </div>
        {/if}

        <div class="v2-label" style="margin-bottom:10px">Organisations</div>
        <div class="v2-card" style="overflow:hidden">
          {#each p.orgs as o (o.id)}
            <div class="v2-setting">
              <div class="v2-setting-body">
                <b>{o.name}</b>
                <span class="v2-sub" style="font-size:11.5px">
                  You are {ROLE_LABEL[o.role] === 'Admin' ? 'an admin' : 'a member'} here
                </span>
              </div>
              {#if o.is_current}
                <Pill tone="ink" dot>Current</Pill>
              {:else}
                <!-- Switching org re-issues the token; it does not edit a field
                     on this page. The action swaps the cookies and reloads. -->
                <form method="POST" action="?/switchOrg" use:enhance class="v2-inline-form">
                  <input type="hidden" name="org_id" value={o.id} />
                  <button class="v2-btn v2-btn-sm" type="submit">
                    <ArrowLeftRight size={12} />Switch
                  </button>
                </form>
              {/if}
            </div>
          {/each}
        </div>
        {#if switchError}
          <p class="v2-error" style="margin-top:9px">{switchError}</p>
        {/if}
        <p class="v2-sub" style="font-size:11.5px;margin-top:11px">
          Switching organisation signs you in again with a new token. Which org you are in decides
          which records exist for you at all, so it is not a filter you can toggle.
        </p>
      </div>

      <div>
        <div class="v2-label" style="margin-bottom:10px">Access</div>
        <div class="v2-card" style="overflow:hidden;margin-bottom:20px">
          <div class="v2-setting">
            <div class="v2-setting-body">
              <b>Role</b>
              <!-- Displayed, never editable from here. -->
              <span class="v2-sub" style="font-size:11.5px">
                Set by an admin. You cannot change your own role.
              </span>
            </div>
            <Lock size={14} style="color:var(--v2-slate);flex:none" />
            <Pill tone={ROLE_TONE[p.role]}>{ROLE_LABEL[p.role]}</Pill>
          </div>
          <!-- /profile/tokens, not /settings/api-tokens. The settings page is
               the org-wide oversight list and 403s a member, so this count used
               to lead most of the people who clicked it to "Admins only". -->
          <a class="v2-setting" href={resolve('/profile/tokens')}>
            <div class="v2-setting-body">
              <b>API tokens</b>
              <span class="v2-sub" style="font-size:11.5px">
                Each one signs in as you, with your role.
              </span>
            </div>
            <KeyRound size={14} style="color:var(--v2-slate);flex:none" />
            <span class="v2-num" style="font-size:13px;font-weight:600">
              {count(p.active_token_count)}
            </span>
          </a>
          <a class="v2-setting" href={resolve('/profile/calendar-feed')}>
            <div class="v2-setting-body">
              <b>Calendar feed</b>
              <span class="v2-sub" style="font-size:11.5px">
                Your open tasks in Google Calendar, Outlook or Apple Calendar.
              </span>
            </div>
            <CalendarDays size={14} style="color:var(--v2-slate);flex:none" />
          </a>
          <div class="v2-setting">
            <div class="v2-setting-body">
              <b>Sign-in method</b>
              <!-- It used to say "Google, on <email>", which is false for
                   anyone who signed in with an emailed code. Nothing in the
                   payload says which was used, so this states what holds for
                   both rather than guessing. -->
              <span class="v2-sub" style="font-size:11.5px">
                {p.user_details.email}, by Google or an emailed code. There is no password to
                change.
              </span>
            </div>
          </div>
        </div>

        <div class="v2-label" style="margin-bottom:10px">Where your work shows up</div>
        <div class="v2-card" style="overflow:hidden">
          <a class="v2-setting" href={resolve('/goals')}>
            <div class="v2-setting-body">
              <b>Goals</b>
              <span class="v2-sub" style="font-size:11.5px">Your quota and how it is pacing</span>
            </div>
          </a>
          <a class="v2-setting" href={resolve('/timesheet')}>
            <div class="v2-setting-body">
              <b>Timesheet</b>
              <span class="v2-sub" style="font-size:11.5px">Hours you have logged this week</span>
            </div>
          </a>
          <a class="v2-setting" href={resolve('/tasks')}>
            <div class="v2-setting-body">
              <b>Tasks</b>
              <span class="v2-sub" style="font-size:11.5px">What is assigned to you</span>
            </div>
          </a>
        </div>
      </div>
    </div>
  </div>
</div>

<style>
  /* The Switch button sits in a form so it can POST; keep it laid out exactly
     as the bare button was (the row uses flex; the form must not add a box). */
  .v2-inline-form {
    display: contents;
  }
</style>
