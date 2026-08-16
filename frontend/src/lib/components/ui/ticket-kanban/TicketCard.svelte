<script>
  import { Building2, Timer, Megaphone } from '@lucide/svelte';

  /**
   * @typedef {Object} Ticket
   * @property {string} id
   * @property {string} name
   * @property {string} status
   * @property {string} priority
   * @property {string} [case_type]
   * @property {string} [account_name]
   * @property {boolean} [is_sla_breached]
   * @property {boolean} [is_sla_at_risk]
   * @property {boolean} [is_sla_first_response_breached]
   * @property {boolean} [is_sla_resolution_breached]
   * @property {number} [escalation_count]
   * @property {Array<{id: string, user_details?: {email?: string}, email?: string}>} [assigned_to]
   */

  /** @type {{ item: Ticket, onclick?: () => void, ondragstart?: (e: DragEvent) => void, ondragend?: () => void }} */
  let { item, onclick, ondragstart, ondragend } = $props();

  // Priority label strip colors
  const priorityLabelBg = {
    Urgent: 'bg-rose-500',
    High: 'bg-orange-500',
    Normal: 'bg-blue-500',
    Low: 'bg-gray-400'
  };

  // Computed values
  const name = $derived(item.name || 'Untitled Ticket');
  const priority = $derived(item.priority);
  const ticketType = $derived(item.case_type);
  const accountName = $derived(item.account_name);
  const isSlaBreached = $derived(
    item.is_sla_breached || item.is_sla_first_response_breached || item.is_sla_resolution_breached
  );
  // The warning band. Exclusive with breached server-side, and guarded here
  // too so a card can never wear both chips if that ever stops holding.
  const isSlaAtRisk = $derived(!isSlaBreached && Boolean(item.is_sla_at_risk));
  const escalationCount = $derived(item.escalation_count || 0);
  const isEscalated = $derived(escalationCount > 0);
  const assignees = $derived(item.assigned_to || []);
  const labelBg = $derived(priority ? priorityLabelBg[priority] : null);

  /** @param {any} assignee */
  function getAssigneeInitials(assignee) {
    const email = assignee?.user_details?.email || assignee?.email || '';
    if (!email) return '?';
    return email.charAt(0).toUpperCase();
  }

  /** @param {any} assignee */
  function getAssigneeName(assignee) {
    return assignee?.user_details?.email || assignee?.email || 'Unknown';
  }

  function getAvatarColor(email) {
    const colors = [
      'bg-violet-500',
      'bg-cyan-500',
      'bg-emerald-500',
      'bg-amber-500',
      'bg-rose-500',
      'bg-indigo-500'
    ];
    const hash = email.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0);
    return colors[hash % colors.length];
  }

  /** @param {KeyboardEvent} e */
  function handleKeydown(e) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onclick?.();
    }
  }
</script>

<div
  class="ticket-card group cursor-pointer rounded-lg border bg-white p-2.5 text-left shadow-[0_1px_0_rgba(9,30,66,0.12)] hover:border-black/10 dark:bg-white/[0.05] dark:hover:border-white/[0.1]
    {isSlaBreached
    ? 'border-l-[3px] border-rose-300 border-l-rose-500 dark:border-rose-500/40'
    : isSlaAtRisk
      ? 'border-l-[3px] border-amber-300 border-l-amber-500 dark:border-amber-500/40'
      : 'border-black/[0.04] dark:border-white/[0.06]'}"
  draggable="true"
  {onclick}
  onkeydown={handleKeydown}
  {ondragstart}
  {ondragend}
  role="button"
  tabindex="0"
>
  <!-- Priority label strip -->
  {#if labelBg}
    <div
      class="mb-1.5 inline-flex h-1.5 w-10 rounded-sm {labelBg}"
      title="{priority} priority"
    ></div>
  {/if}

  <!-- Title -->
  <h4 class="text-[14px] leading-snug font-normal text-gray-900 dark:text-gray-100">
    {name}
  </h4>

  <!-- Type + Account -->
  {#if ticketType || accountName}
    <div class="mt-1.5 flex items-center gap-2 text-[12px] text-gray-500 dark:text-gray-400">
      {#if ticketType}
        <span class="rounded bg-gray-100 px-1.5 py-px text-[11px] dark:bg-white/[0.06]">
          {ticketType}
        </span>
      {/if}
      {#if accountName}
        <div class="flex min-w-0 items-center gap-1">
          <Building2 class="h-3 w-3 shrink-0" />
          <span class="truncate">{accountName}</span>
        </div>
      {/if}
    </div>
  {/if}

  <!-- Footer: SLA / escalation + assignees -->
  {#if isSlaBreached || isSlaAtRisk || isEscalated || assignees.length > 0}
    <div class="mt-2 flex items-center justify-between gap-2">
      <div class="flex items-center gap-1.5">
        {#if isSlaBreached}
          <span
            class="inline-flex items-center gap-1 rounded bg-rose-100 px-1.5 py-0.5 text-[11px] font-semibold tracking-wide text-rose-700 uppercase dark:bg-rose-500/15 dark:text-rose-300"
            title="SLA breached"
          >
            <Timer class="h-3 w-3" />
            SLA
          </span>
        {:else if isSlaAtRisk}
          <span
            class="inline-flex items-center gap-1 rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold tracking-wide text-amber-700 uppercase dark:bg-amber-500/15 dark:text-amber-300"
            title="SLA due soon"
          >
            <Timer class="h-3 w-3" />
            Due
          </span>
        {/if}
        {#if isEscalated}
          <span
            class="inline-flex items-center gap-1 rounded bg-rose-600 px-1.5 py-0.5 text-[11px] font-semibold text-white"
            title={`Escalated ${escalationCount}x`}
          >
            <Megaphone class="h-3 w-3" />
            {escalationCount > 1 ? `Escalated ${escalationCount}x` : 'Escalated'}
          </span>
        {/if}
      </div>

      {#if assignees.length > 0}
        <div class="flex items-center -space-x-1.5">
          {#each assignees.slice(0, 3) as assignee, i (assignee.id)}
            <div
              class="relative flex h-6 w-6 items-center justify-center rounded-full {getAvatarColor(
                getAssigneeName(assignee)
              )} text-[10px] font-semibold text-white ring-2 ring-white dark:ring-[#262626]"
              style="z-index: {3 - i}"
              title={getAssigneeName(assignee)}
            >
              {getAssigneeInitials(assignee)}
            </div>
          {/each}
          {#if assignees.length > 3}
            <div
              class="relative flex h-6 w-6 items-center justify-center rounded-full bg-gray-200 text-[10px] font-semibold text-gray-700 ring-2 ring-white dark:bg-gray-700 dark:text-gray-200 dark:ring-[#262626]"
              style="z-index: 0"
            >
              +{assignees.length - 3}
            </div>
          {/if}
        </div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .ticket-card:active {
    cursor: grabbing;
  }
  .ticket-card:focus-visible {
    outline: 2px solid rgb(34 211 238);
    outline-offset: 2px;
  }
</style>
