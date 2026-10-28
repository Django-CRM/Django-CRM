<script lang="ts">
  import { resolve } from '$app/paths';
  /**
   * The organisation itself (read view).
   *
   * Most of this is the company profile, and the thing worth saying about it
   * is where it ends up: these fields are printed on every invoice and
   * estimate a customer receives. v1 presents them as a generic settings form,
   * so a wrong VAT number looks like a wrong preference rather than a wrong
   * document. The page groups them under that heading instead.
   *
   * Two behaviour switches sit at the bottom. They are not preferences either
   *: one stops customer surveys org-wide, the other changes what a close
   * button offers to do, so each says what turning it off actually causes.
   * They are real `Org` columns and, until this page was wired, had no way to
   * be edited at all.
   *
   * The org API key is NOT here, in any form. It is a credential that
   * authenticates as the org's first admin, so the API never serialises it onto
   * this payload. Rotating it belongs behind an explicit, audited action
   * (`OrgApiKeyView`), not on a page reachable by browsing.
   *
   * Editing is admin-only. `can_edit` (from the JWT role claim) decides whether
   * the "Edit details" link shows; the edit route and the backend PATCH are what
   * enforce it. A member sees this page read-only. Industry apps and sample
   * data are in Settings › Apps.
   */
  import PageHeader from '#lib/v2/components/PageHeader.svelte';
  import SettingsCrumb from '#lib/v2/components/SettingsCrumb.svelte';
  import Pill from '#lib/v2/components/Pill.svelte';
  import { count, shortDate } from '#lib/v2/format.js';
  import { exampleNumber, NUMBER_DATE_PARTS } from '#lib/v2/document-numbers.js';
  import { todayIn } from '#lib/v2/dates.js';
  import { FileText, ShieldAlert, Pencil } from '@lucide/svelte';
  import { isOn } from '#lib/v2/modules.js';
  import { reminderLabel } from '#lib/v2/lead-alerts.js';
  import type { PageProps } from './$types';

  let { data }: PageProps = $props();

  let org = $derived(data.org);
  let invoicesOn = $derived(isOn(data.modules, 'invoices'));
  let ticketsOn = $derived(isOn(data.modules, 'tickets'));
  let leadsOn = $derived(isOn(data.modules, 'leads'));
  let today = $derived(todayIn(org.timezone || 'UTC'));
  /** "Year, count restarts each year" for the stored date part. */
  let numbering = $derived(
    NUMBER_DATE_PARTS.find((part) => part.value === org.number_date_part)?.label ?? ''
  );

  let address = $derived(
    [org.address_line, org.city, org.state, org.postcode, org.country].filter(Boolean).join(', ')
  );
</script>

<PageHeader title="Organization">
  {#snippet crumb()}<SettingsCrumb />{/snippet}
  {#snippet sub()}
    <span class="v2-num">{count(org.member_count)}</span> members · created
    {shortDate(org.created_at)}
  {/snippet}
  {#snippet actions()}
    {#if data.can_edit}
      <a class="v2-btn v2-btn-primary" href={resolve('settings/organization/edit')}>
        <Pencil size={13} />Edit details
      </a>
    {/if}
  {/snippet}
</PageHeader>

<div class="v2-scroll">
  <div class="v2-pad" style="padding-top:18px;padding-bottom:32px">
    <div class="v2-split">
      <div>
        <div class="v2-label" style="margin-bottom:10px">What customers see</div>
        <div class="v2-card" style="padding:16px 18px;margin-bottom:12px">
          <dl class="v2-kv">
            <dt>Legal name</dt>
            <dd>{org.company_name || '—'}</dd>
            <dt>Trading as</dt>
            <dd>{org.name}</dd>
            <dt>Address</dt>
            <dd>{address || '—'}</dd>
            <dt>Tax ID</dt>
            <dd class="v2-num" style="font-size:12px">{org.tax_id || '—'}</dd>
            <dt>Email</dt>
            <dd>{org.email || '—'}</dd>
            <dt>Phone</dt>
            <dd class="v2-num" style="font-size:12px">{org.phone || '—'}</dd>
            <dt>Website</dt>
            <dd>{org.website || '—'}</dd>
            <dt>Logo</dt>
            <dd>{org.logo_url ? 'Set' : 'Not set'}</dd>
          </dl>
        </div>

        {#if invoicesOn}
          <div class="v2-card" style="padding:14px 16px">
            <div style="display:flex;gap:10px;align-items:flex-start">
              <FileText size={16} style="color:var(--v2-slate);flex:none;margin-top:2px" />
              <p class="v2-sub" style="font-size:12.5px;margin:0;line-height:1.5">
                These fields are printed on every invoice and estimate. Changing one changes
                documents from that moment on; PDFs already sent keep what they were sent with. How
                they are laid out is set in
                <a href={resolve('invoices/templates')} style="color:inherit">invoice templates</a>.
              </p>
            </div>
          </div>
        {/if}
      </div>

      <div>
        <div class="v2-label" style="margin-bottom:10px">Defaults</div>
        <div class="v2-card" style="overflow:hidden;margin-bottom:20px">
          <div class="v2-setting">
            <div class="v2-setting-body">
              <b>Currency</b>
              <span class="v2-sub" style="font-size:11.5px">
                {invoicesOn
                  ? 'Applied to new invoices and estimates. Existing ones keep theirs.'
                  : 'The default for new records that carry an amount. Existing ones keep theirs.'}
              </span>
            </div>
            <span class="v2-num" style="font-size:13px">{org.default_currency || '—'}</span>
          </div>
          <div class="v2-setting">
            <div class="v2-setting-body">
              <b>Country</b>
              <span class="v2-sub" style="font-size:11.5px">Default for new addresses.</span>
            </div>
            <span style="font-size:13px">{org.default_country || '—'}</span>
          </div>
          <div class="v2-setting">
            <div class="v2-setting-body">
              <b>Time zone</b>
              <span class="v2-sub" style="font-size:11.5px">
                When a day starts here, so "due today" and "overdue" mean what your team expects.
              </span>
            </div>
            <span style="font-size:13px">{(org.timezone || 'UTC').replace(/_/g, ' ')}</span>
          </div>
        </div>

        {#if invoicesOn}
          <div class="v2-label" style="margin-bottom:10px">Invoice numbers</div>
          <div class="v2-card" style="overflow:hidden;margin-bottom:20px">
            <div class="v2-setting">
              <div class="v2-setting-body">
                <b>New invoices</b>
                <span class="v2-sub" style="font-size:11.5px">
                  {numbering}. Numbers already issued keep theirs.
                </span>
              </div>
              <span class="v2-num" style="font-size:13px"
                >{exampleNumber(org, 'invoice', today)}</span
              >
            </div>
            <div class="v2-setting">
              <div class="v2-setting-body">
                <b>New estimates</b>
                <span class="v2-sub" style="font-size:11.5px">Same format, own prefix.</span>
              </div>
              <span class="v2-num" style="font-size:13px"
                >{exampleNumber(org, 'estimate', today)}</span
              >
            </div>
          </div>
        {/if}

        {#if leadsOn}
          <div class="v2-label" style="margin-bottom:10px">Leads</div>
          <div class="v2-card" style="overflow:hidden;margin-bottom:20px">
            <div class="v2-setting">
              <div class="v2-setting-body">
                <b>Waiting reminder</b>
                <span class="v2-sub" style="font-size:11.5px">
                  Reminds the assignee, or the admins when nobody is on it, once when a lead waits
                  longer than this for a reply.
                </span>
              </div>
              <Pill tone={org.lead_reminder_minutes ? 'moss' : 'slate'}>
                {reminderLabel(org.lead_reminder_minutes)}
              </Pill>
            </div>
          </div>
        {/if}

        {#if ticketsOn}
          <div class="v2-label" style="margin-bottom:10px">Behaviour</div>
          <div class="v2-card" style="overflow:hidden">
            <div class="v2-setting">
              <div class="v2-setting-body">
                <b>Satisfaction surveys</b>
                <!-- Org-level kill switch: the post-close signal short-circuits
                     before any email is sent. Off is silent, everywhere. -->
                <span class="v2-sub" style="font-size:11.5px">
                  Off stops every survey org-wide, with no per-team exception and no notice on the
                  ticket.
                </span>
              </div>
              <Pill tone={org.csat_enabled ? 'moss' : 'slate'}>
                {org.csat_enabled ? 'Sending' : 'Off'}
              </Pill>
            </div>
            <div class="v2-setting">
              <div class="v2-setting-body">
                <b>Close child tickets with the parent</b>
                <!-- Only the DEFAULT state of the prompt; the person closing the
                     ticket still confirms. This used to claim the prompt without
                     saying where it is, and there is no such prompt on the web:
                     `close-with-children` has no caller in `frontend/src`, so
                     closing a parent here leaves its children open. The phone is
                     the client that asks, and the one this setting reaches. -->
                <span class="v2-sub" style="font-size:11.5px">
                  Sets how the close prompt starts on the mobile app, which offers to close a
                  parent's open children with it. On the web there is no such prompt yet: closing a
                  parent leaves its children open.
                </span>
              </div>
              <Pill tone={org.auto_close_children_on_parent_close ? 'clay' : 'slate'}>
                {org.auto_close_children_on_parent_close ? 'Offered on' : 'Offered off'}
              </Pill>
            </div>
          </div>
        {/if}

        <div class="v2-card" style="padding:14px 16px;margin-top:20px">
          <div style="display:flex;gap:10px;align-items:flex-start">
            <ShieldAlert size={16} style="color:var(--v2-slate);flex:none;margin-top:2px" />
            <div>
              <div style="font-weight:600;font-size:13px">The organisation API key is not here</div>
              <p class="v2-sub" style="font-size:12.5px;margin:5px 0 0;line-height:1.5">
                It authenticates as the whole organisation, so it is never rendered on a page you
                can reach by browsing. For per-person programmatic access, use
                <a href={resolve('settings/api-tokens')} style="color:inherit">API tokens</a>, which
                can be revoked one at a time.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
