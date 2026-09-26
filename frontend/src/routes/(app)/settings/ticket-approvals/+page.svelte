<script>
  import { resolve } from '$app/paths';
  /**
   * What gates a ticket close, and who can clear it.
   *
   * The queue at /v2/tickets/approvals answers "what is waiting on me". This
   * answers "what will be gated next time, and by whom", the same rows, a
   * different question, which is why it is a settings page and not a tab.
   *
   * Three states the model permits and the form does not warn about, all
   * flagged on the row here. See `./matching.js`, which owns the rules:
   *
   * - approver_role MANAGER with no named approvers. Profile.role is only
   *   ADMIN or USER, so the rule matches nobody and what it gates can never be
   *   closed by anyone.
   * - Two active rules with identical conditions. Only one rule gates a given
   *   ticket, and among equals the newest takes every case, so the older is
   *   dead however live it looks.
   * - Named approvers read as a narrowing and are a widening: anyone holding
   *   the role clears the rule too.
   *
   * Separation of duties, an admin clearing their own requested close, is
   * NOT a gap to warn about: `ApprovalApproveView` rejects an approval whose
   * requester is the approver, unconditionally (no admin exception), so the API
   * enforces it however a rule is configured. An earlier version of this page
   * flagged "any admin, including the requester" as a hole; the view has since
   * closed it, so the warning is gone.
   */
  import { enhance } from '$app/forms';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import SettingsCrumb from '$lib/v2/components/SettingsCrumb.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import SettingsFormPanel from '$lib/v2/components/SettingsFormPanel.svelte';
  import ConfirmAction from '$lib/v2/components/ConfirmAction.svelte';
  import { count } from '$lib/v2/format.js';
  import { ROLE_LABEL } from '$lib/v2/enums.js';
  import { missingOptions, missingOption, inactiveOptionLabel } from '$lib/v2/pickers.js';
  import {
    approverSentence,
    clearableByNobody,
    ruleMatchSentence,
    shadowedRuleIds,
    shadowedBy
  } from './matching.js';
  import { Plus, TriangleAlert, ChevronRight } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  // cases.approvals: PRIORITY_CHOICE and CASE_TYPE. Not in `$lib/v2/enums.js`
  // because there is no shared label map to derive from: value and label are
  // the same string for both, and no other v2 page reads either list.
  const MATCH_PRIORITIES = ['Low', 'Normal', 'High', 'Urgent'];
  const MATCH_CASE_TYPES = ['Question', 'Incident', 'Problem'];

  // `ROLE_LABEL` covers `Profile.role` (ADMIN/USER), not the approver-role
  // vocabulary this form offers (ADMIN/MANAGER). MANAGER is not a real
  // `Profile.role` value (see the module docstring), so it has no entry in
  // that map and would render as `undefined`. Labelled here instead.
  function approverRoleLabel(role) {
    return role === 'MANAGER' ? 'Manager' : (ROLE_LABEL[role] ?? role);
  }

  // `null` when the panel is closed, `'new'` when adding, or the rule object
  // when editing that row. One panel, two modes, so two rows can never be
  // open for edit at once.
  let editing = $state(/** @type {any} */ (null));

  function openCreate() {
    editing = 'new';
  }

  function openEdit(r) {
    editing = r;
  }

  let totals = $derived(data.totals);
  let rules = $derived(data.rules);

  // Named approvers the picker cannot offer, because the profile has been
  // deactivated since it was named. Without an option of their own the
  // multi-select submits nothing for them and an unrelated edit drops them,
  // which can widen a rule to "any admin" or, on a MANAGER rule, leave it
  // clearable by nobody.
  let missingApprovers = $derived(
    editing && editing !== 'new' ? missingOptions(data.people, editing.approvers) : []
  );

  // The same for the team. Teams have no active flag, so a stored team is
  // missing from the list only when the list failed to load. With no option to
  // match, the select falls back to "Any team" and saving clears the team.
  let missingTeam = $derived(
    editing && editing !== 'new' ? missingOption(data.teams, editing.match_team) : null
  );

  let shadowed = $derived(shadowedRuleIds(rules));
</script>

<PageHeader title="Approval rules">
  {#snippet crumb()}<SettingsCrumb />{/snippet}
  {#snippet sub()}
    <span class="v2-num">{count(totals.active)}</span> active ·
    <span class="v2-num">{count(totals.pending)}</span> approvals waiting on them right now
  {/snippet}
  {#snippet actions()}
    {#if data.can_edit && !editing}
      <button class="v2-btn v2-btn-primary" onclick={openCreate}><Plus />New rule</button>
    {/if}
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:18px;padding-bottom:32px">
    {#if editing}
      <SettingsFormPanel
        title={editing === 'new' ? 'New rule' : `Edit ${editing.name}`}
        action={editing === 'new' ? '?/create' : '?/update'}
        error={editing === 'new' ? form?.create?.error : form?.update?.error}
        submitLabel={editing === 'new' ? 'Add rule' : 'Save rule'}
        oncancel={() => (editing = null)}
        ondone={() => (editing = null)}
      >
        {#snippet fields()}
          {#if editing !== 'new'}
            <input type="hidden" name="id" value={editing.id} />
          {/if}

          <div class="v2-field">
            <label for="a-name">Name</label>
            <input
              id="a-name"
              class="v2-input"
              name="name"
              maxlength="128"
              required
              value={editing === 'new' ? '' : editing.name}
            />
          </div>

          <div class="v2-field">
            <label for="a-role">Approver role</label>
            <select id="a-role" class="v2-input" name="approver_role">
              {#each ['ADMIN', 'MANAGER'] as role (role)}
                <option
                  value={role}
                  selected={editing === 'new' ? role === 'ADMIN' : editing.approver_role === role}
                >
                  {approverRoleLabel(role)}
                </option>
              {/each}
            </select>
          </div>

          <div class="v2-field v2-sfp-wide">
            <label for="a-approvers">Named approvers</label>
            <select
              id="a-approvers"
              class="v2-input"
              name="approver_ids"
              multiple
              style="height:96px"
            >
              <!-- A named approver whose profile is no longer active is not in
                   `data.people`, so it gets an option of its own here. Without
                   one the browser submits nothing for it and saving an
                   unrelated field would drop the approver silently. -->
              {#each missingApprovers as a (a.id)}
                <option value={a.id} selected
                  >{data.options_failed ? a.email : inactiveOptionLabel(a.email)}</option
                >
              {/each}
              {#each data.people as p (p.id)}
                <option
                  value={p.id}
                  selected={editing !== 'new' && editing.approvers.some((a) => a.id === p.id)}
                >
                  {p.name}
                </option>
              {/each}
            </select>
            <p class="v2-hint">
              Named approvers are in addition to the role above. Leave this empty and anyone with
              that role can clear the approval.
            </p>
            {#if missingApprovers.length && !data.options_failed}
              <p class="v2-hint">
                {missingApprovers.length === 1
                  ? 'One approver is'
                  : `${missingApprovers.length} approvers are`}
                no longer active. They stay named until you deselect them, and they cannot clear an approval
                while their account is off.
              </p>
            {/if}
          </div>

          <div class="v2-field">
            <label for="a-priority">Priority</label>
            <select id="a-priority" class="v2-input" name="match_priority">
              <option value="" selected={editing === 'new' || !editing.match_priority}>Any</option>
              {#each MATCH_PRIORITIES as p (p)}
                <option value={p} selected={editing !== 'new' && editing.match_priority === p}>
                  {p}
                </option>
              {/each}
            </select>
          </div>

          <div class="v2-field">
            <label for="a-type">Ticket type</label>
            <select id="a-type" class="v2-input" name="match_case_type">
              <option value="" selected={editing === 'new' || !editing.match_case_type}>
                Any
              </option>
              {#each MATCH_CASE_TYPES as t (t)}
                <option value={t} selected={editing !== 'new' && editing.match_case_type === t}>
                  {t}
                </option>
              {/each}
            </select>
          </div>

          <div class="v2-field">
            <label for="a-team">Team</label>
            <select id="a-team" class="v2-input" name="match_team_id">
              <option value="" selected={editing === 'new' || !editing.match_team}>Any team</option>
              {#if missingTeam}
                <option value={missingTeam.id} selected>{missingTeam.name}</option>
              {/if}
              {#each data.teams as t (t.id)}
                <option
                  value={t.id}
                  selected={editing !== 'new' && editing.match_team?.id === t.id}
                >
                  {t.name}
                </option>
              {/each}
            </select>
            {#if data.options_failed}
              <p class="v2-hint" role="alert">
                The people and teams list did not load. {editing === 'new'
                  ? 'Reload the page to pick a team or named approvers.'
                  : 'Saving keeps the current team and approvers; reload the page to change them.'}
              </p>
            {:else if !data.teams.length}
              <p class="v2-hint">No teams in this org yet.</p>
            {/if}
          </div>

          {#if editing === 'new'}
            <div class="v2-field">
              <label for="a-active">Active</label>
              <label style="display:flex;gap:8px;align-items:center;font-weight:400">
                <input id="a-active" type="checkbox" name="is_active" value="true" checked />
                Starts gating matching ticket closes as soon as it is saved.
              </label>
            </div>
          {/if}
        {/snippet}
      </SettingsFormPanel>
    {/if}

    {#if form?.deactivate?.error}
      <p class="v2-error" style="margin-bottom:12px">{form.deactivate.error}</p>
    {/if}
    {#if form?.activate?.error}
      <p class="v2-error" style="margin-bottom:12px">{form.activate.error}</p>
    {/if}
    {#if form?.remove?.error}
      <p class="v2-error" style="margin-bottom:12px">{form.remove.error}</p>
    {/if}
    {#if form?.remove?.turned_off}
      <!-- What actually happened, not what the button said. The backend
           soft-disables a rule that has approval history rather than
           destroying it, so the row is still in the list below, off, and
           saying nothing here would read as a delete that failed silently. -->
      <div class="v2-rule-flag" style="margin-bottom:12px">
        <TriangleAlert size={14} style="color:var(--v2-clay);flex:none" />
        <span>
          That rule had approval history, so it was turned off instead of deleted. The approvals it
          already gated have to keep pointing at it. It is still in the list below, marked Off, and
          gates nothing.
        </span>
      </div>
    {/if}

    <div class="v2-label" style="margin-bottom:4px">Rules</div>
    <!-- The list reads as cumulative and is not. Worth one line above it,
         since every row below describes a gate and only one of them is ever
         the gate for a given ticket. -->
    <p class="v2-sub" style="font-size:11.5px;margin:0 0 10px">
      A ticket is gated by one rule, the most specific that matches it. The others are fallbacks for
      the tickets it misses.
    </p>
    <div style="display:flex;flex-direction:column;gap:9px">
      {#each rules as r (r.id)}
        {@const beatenBy = shadowed.has(r.id) ? shadowedBy(r, rules) : null}
        <div class="v2-card" style="padding:14px 16px;opacity:{r.is_active ? 1 : 0.62}">
          <div style="display:flex;gap:11px;align-items:flex-start">
            <div style="flex:1;min-width:0">
              <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                <b style="font-size:13.5px">{r.name}</b>
                {#if !r.is_active}<Pill tone="slate">Off</Pill>{/if}
                {#if clearableByNobody(r)}<Pill tone="rust">Nobody can clear</Pill>{/if}
                {#if beatenBy}<Pill tone="rust">Never runs</Pill>{/if}
              </div>

              <div class="v2-sub" style="font-size:12.5px;margin-top:5px;white-space:normal">
                <b style="font-weight:600;color:var(--v2-ink)">Gates</b>
                {ruleMatchSentence(r)}
                <b style="font-weight:600;color:var(--v2-ink)">→</b>
                cleared by {approverSentence(r)}
              </div>

              {#if clearableByNobody(r)}
                <div class="v2-rule-flag">
                  <TriangleAlert size={14} style="color:var(--v2-rust);flex:none" />
                  <span>
                    This organisation has admins and members. There is no manager role. With no
                    named approvers, the first ticket this gates cannot be closed by anyone. Name
                    approvers, or set it to admin.
                  </span>
                </div>
              {/if}

              {#if beatenBy}
                <div class="v2-rule-flag">
                  <TriangleAlert size={14} style="color:var(--v2-rust);flex:none" />
                  <span>
                    <b style="font-weight:600;color:var(--v2-ink)">{beatenBy.name}</b> gates exactly the
                    same tickets and was written later. One rule gates a close, the most specific match,
                    and the newest wins between equals, so this one never runs. Turn it off, delete it,
                    or narrow what it matches.
                  </span>
                </div>
              {/if}
            </div>

            <div style="flex:none;text-align:right">
              {#if r.pending_count}
                <a
                  href={resolve('/tickets/approvals')}
                  class="v2-sub"
                  style="font-size:12px;display:inline-flex;align-items:center;gap:2px"
                >
                  <span class="v2-num">{count(r.pending_count)}</span> waiting
                  <ChevronRight size={13} />
                </a>
              {/if}
            </div>

            {#if data.can_edit}
              <div style="display:flex;gap:6px;align-items:center;flex:none">
                <button class="v2-btn v2-btn-sm" type="button" onclick={() => openEdit(r)}>
                  Edit
                </button>
                {#if r.is_active}
                  <ConfirmAction
                    action="?/deactivate"
                    label="Turn off"
                    confirmLabel="Turn off"
                    explain="Stops gating new ticket closes. It stays in the list, off, until turned back on."
                    hidden={{ id: r.id }}
                  />
                {:else}
                  <form method="POST" action="?/activate" use:enhance>
                    <input type="hidden" name="id" value={r.id} />
                    <button class="v2-btn v2-btn-sm" type="submit">Turn on</button>
                  </form>
                {/if}
                <!-- Not "deleted permanently". The backend destroys a rule
                     only when it has never been used; one with any approval
                     history, in any state, is turned off instead, because the
                     approval rows have to keep pointing at it. `pending_count`
                     cannot predict which happens: it counts only the pending
                     state, and the backend's check counts every state. So the
                     line names both outcomes, and the page reports which one
                     actually happened afterwards. -->
                <ConfirmAction
                  action="?/remove"
                  label="Delete"
                  confirmLabel="Delete"
                  explain={r.pending_count > 0
                    ? `${r.pending_count} approvals are waiting on this rule. A rule that has ever gated a close is turned off rather than deleted, because the record has to be kept.`
                    : 'A rule that has never gated a close is deleted for good. One with any approval history is turned off instead, because the record has to be kept.'}
                  hidden={{ id: r.id }}
                />
              </div>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  </div>
</div>

<style>
  .v2-rule-flag {
    display: flex;
    gap: 7px;
    align-items: flex-start;
    margin-top: 9px;
    font-size: 12px;
    color: var(--v2-slate);
    line-height: 1.45;
  }
</style>
