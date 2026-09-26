<script>
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import FilterBar from '$lib/v2/components/FilterBar.svelte';
  import Avatar from '$lib/v2/components/Avatar.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import EmptyState from '$lib/v2/components/EmptyState.svelte';
  import { count, relativeDays } from '$lib/v2/format.js';
  import ContactImportDrawer from '$lib/components/contacts/ContactImportDrawer.svelte';
  import { Download, Users, PhoneOff, Plus, Upload } from '@lucide/svelte';

  /** @type {{ data: any }} */
  let { data } = $props();

  let contacts = $derived(data.contacts);
  let totals = $derived(data.totals);
  let importOpen = $state(false);
</script>

<PageHeader title="Contacts">
  {#snippet sub()}
    <span class="v2-num">{count(totals.count)}</span> people
    {#if !data.includeInactive && totals.inactive}
      · <span class="v2-num">{count(totals.inactive)}</span> inactive hidden
    {/if}
    {#if totals.do_not_call}
      · <span class="v2-num">{count(totals.do_not_call)}</span> do not call
    {/if}
  {/snippet}
  {#snippet actions()}
    {#if data.includeInactive}
      <a class="v2-btn" href={resolve('/contacts')}>Hide inactive</a>
    {:else}
      <a class="v2-btn" href={resolve('/contacts?inactive=1')}>Show inactive</a>
    {/if}
    <button class="v2-btn" onclick={() => (importOpen = true)}><Upload />Import</button>
    <!-- The page's own query string: the export rebuilds the same API query
         from it, so the file holds every row this list would page through. -->
    <a
      class="v2-btn"
      href="{resolve('/api/contacts/export')}?{page.url.searchParams}"
      data-sveltekit-reload
    >
      <Download />Export
    </a>
    <a class="v2-btn v2-btn-primary" href={resolve('/contacts/new')}><Plus />New contact</a>
  {/snippet}
</PageHeader>

<FilterBar
  page="contacts"
  url={page.url}
  people={data.people}
  tags={data.tags}
  meId={data.meId}
  meta="Most recently added first"
/>

<div class="v2-scroll">
  {#if contacts.length === 0}
    <EmptyState
      title="No contacts yet"
      body="A contact is a person at an account. Convert a lead, or add one directly and attach them to the account they work for."
    >
      {#snippet icon()}<Users size={21} />{/snippet}
      {#snippet actions()}
        <a class="v2-btn v2-btn-primary" href={resolve('/contacts/new')}>New contact</a>
        <a class="v2-btn" href={resolve('/leads')}>Go to leads</a>
      {/snippet}
    </EmptyState>
  {:else}
    <div class="v2-table-wrap">
      <table class="v2-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Account</th>
            <th>Reachable on</th>
            <th>Email</th>
            <th data-m="hide">Owner</th>
            <th class="v2-r">Updated</th>
          </tr>
        </thead>
        <tbody>
          {#each contacts as c (c.id)}
            <tr>
              <td>
                <a
                  class="v2-row-link"
                  href={resolve(`/contacts/${c.id}`)}
                  style="display:flex;align-items:center;gap:9px"
                >
                  <Avatar name={c.name} size={26} />
                  <span>
                    <span class="v2-table-primary">{c.name}</span>
                    <span class="v2-table-secondary" style="display:block">
                      {c.title || 'No title recorded'}
                    </span>
                  </span>
                </a>
              </td>
              <td>
                <!--
                  The linked account, not the typed-in company name.
                  `organization` is free text and routinely names a different
                  company from the account this person is attached to, so it
                  appears only where there is no link to show, and says so.
                -->
                {#if c.account}
                  <a href={resolve(`/accounts/${c.account.id}`)} style="color:inherit"
                    >{c.account.name}</a
                  >
                  {#if c.other_accounts.length}
                    <span class="v2-sub" style="font-size:11px">+{c.other_accounts.length}</span>
                  {/if}
                {:else if c.organization}
                  <span class="v2-muted" title="Typed in, not linked to an account">
                    {c.organization}
                  </span>
                {:else}
                  <span class="v2-muted">—</span>
                {/if}
              </td>
              <td>
                <!--
                  Two different facts, so two different marks. "Do not call" is
                  a rule about how you may contact this person; inactive is a
                  fact about whether they still work there.
                -->
                <span style="display:inline-flex;gap:6px;align-items:center">
                  {#if c.do_not_call}
                    <Pill tone="rust"><PhoneOff size={11} />Do not call</Pill>
                  {:else if c.phone}
                    <span class="v2-num" style="font-size:12px">{c.phone}</span>
                  {:else}
                    <span class="v2-muted">No phone</span>
                  {/if}
                  {#if !c.is_active}
                    <Pill tone="slate">Inactive</Pill>
                  {/if}
                </span>
              </td>
              <td>
                {#if c.email}
                  <a href="mailto:{c.email}" style="color:inherit">{c.email}</a>
                {:else}
                  <span class="v2-muted">No email</span>
                {/if}
              </td>
              <td data-m="hide">{c.owner ?? 'Unassigned'}</td>
              <td class="v2-r v2-muted">
                <!--
                  When the record was last edited, which is all the CRM knows.
                  The mock sorted and coloured this column by `last_activity_at`,
                  when somebody last spoke to this person. Nothing stores that.
                -->
                {c.updated_at ? relativeDays(c.updated_at) : '—'}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <p class="v2-sub v2-pad" style="font-size:12px;padding-bottom:24px">
      Showing <span class="v2-num">{contacts.length}</span> of
      <span class="v2-num">{count(totals.count)}</span>
    </p>
  {/if}
</div>

<ContactImportDrawer bind:open={importOpen} />
