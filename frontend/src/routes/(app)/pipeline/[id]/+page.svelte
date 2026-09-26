<script>
  import { resolve } from '$app/paths';
  import { enhance } from '$app/forms';
  import PageHeader from '$lib/v2/components/PageHeader.svelte';
  import Timeline from '$lib/v2/components/Timeline.svelte';
  import Pill from '$lib/v2/components/Pill.svelte';
  import Avatar from '$lib/v2/components/Avatar.svelte';
  import { money, longDate } from '$lib/v2/format.js';
  import { AGING_TONE, AGING_LABEL, OPPORTUNITY_TYPE_LABEL } from '$lib/v2/enums.js';
  import { Check, ChevronRight } from '@lucide/svelte';

  /** @type {{ data: any, form: any }} */
  let { data, form } = $props();

  /* Raising an invoice copies the lines of a won deal. The API refuses any
     other stage kind, so the button is offered only where it can succeed; a
     deal with no lines still gets the server's own sentence below. */
  let invoicing = $state(false);

  let { deal, activity, lineItems, contacts } = $derived(data);
  /* The deal's own pipeline's open stages: the path a deal walks. Won and
     lost are not on it; they end it. */
  let openStages = $derived(
    (data.pipeline?.stages ?? []).filter((/** @type {any} */ s) => s.kind === 'open')
  );
  let stageIndex = $derived(openStages.findIndex((/** @type {any} */ s) => s.code === deal.stage));

  /**
   * The discount is per line item: `OpportunityLineItem.save()` computes
   * subtotal, then discount_amount, then total, and Opportunity has no
   * discount field at all. So the footer adds up what the lines actually
   * carry; it does not derive a deal-level discount from the difference
   * between the lines and `deal.amount`. That difference cannot exist:
   * `recalculate_amount()` sets amount to the sum of the line totals.
   */
  let subtotal = $derived(lineItems.reduce((a, li) => a + li.subtotal, 0));
  let discount = $derived(lineItems.reduce((a, li) => a + li.discount_amount, 0));
</script>

<PageHeader title={deal.name} record>
  {#snippet crumb()}
    <a href={resolve('/pipeline')}>Pipeline</a>
    <ChevronRight size={12} />
    <a href={resolve(`/accounts/${deal.account.id}`)}>{deal.account.name}</a>
  {/snippet}
  {#snippet actions()}
    <!-- "Move stage" was a second button that did nothing. Stage is edited on
         the form below, where the page can show what moving it costs, the
         aging clock resets, instead of moving it in one anonymous click. -->
    <a class="v2-btn" href={resolve(`/pipeline/${deal.id}/edit`)}>Edit</a>
    <a class="v2-btn" href="{resolve('/invoices/estimates/new')}?opportunity={deal.id}"
      >Create estimate</a
    >
    {#if deal.stage_kind === 'won'}
      <form
        method="POST"
        action="?/invoice"
        style="display:contents"
        use:enhance={() => {
          invoicing = true;
          return async ({ update }) => {
            await update();
            invoicing = false;
          };
        }}
      >
        <button class="v2-btn v2-btn-primary" type="submit" disabled={invoicing}>
          Create invoice
        </button>
      </form>
    {/if}
  {/snippet}
</PageHeader>

{#if form?.error}
  <div class="v2-pad" style="padding-top:12px;flex:none">
    <p class="deal-error" role="alert">{form.error}</p>
  </div>
{/if}

<div style="display:flex;flex:1;min-height:0;overflow:hidden">
  <div class="v2-main">
    <!-- Stage stepper. Closed stages are not on the path; they end it. -->
    <div
      class="v2-pad"
      style="padding-top:14px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;flex:none"
    >
      {#each openStages as stage, i (stage.code)}
        {#if i > 0}<ChevronRight size={12} style="color:var(--v2-slate)" />{/if}
        <span
          class="v2-pill"
          style={i <= stageIndex
            ? 'color:var(--v2-ink);background:color-mix(in srgb, var(--v2-ink) 9%, transparent)'
            : 'color:var(--v2-slate);background:var(--v2-line-soft)'}
        >
          {#if i < stageIndex}<Check size={11} />{/if}
          {stage.label}
        </span>
      {/each}
      <span style="margin-left:auto">
        <Pill tone={AGING_TONE[deal.aging_status]} dot>
          {`${AGING_LABEL[deal.aging_status]} · ${deal.days_in_current_stage} days in ${deal.stage_label}`}
        </Pill>
      </span>
    </div>

    <div class="v2-scroll">
      <div class="v2-pad" style="padding-top:14px;padding-bottom:32px">
        <!--
          The "next action" card that used to sit here was a fixture string.
          Opportunity has no such field and nothing derives one, so there is
          nothing to render. A suggestion the system invented is worse than no
          suggestion, because people act on it.
        -->
        <div class="v2-label" style="margin-bottom:12px">Activity</div>
        <Timeline events={activity} />

        {#if lineItems.length}
          <div class="v2-label" style="margin:22px 0 10px">Line items</div>
          <div class="v2-card" style="overflow:hidden">
            <table class="v2-table">
              <thead>
                <tr>
                  <th>Product</th>
                  <th class="v2-r">Qty</th>
                  <th class="v2-r">Unit price</th>
                  <th class="v2-r">Discount</th>
                  <th class="v2-r">Total</th>
                </tr>
              </thead>
              <tbody>
                {#each lineItems as li (li.id)}
                  <tr>
                    <td>{li.name}</td>
                    <td class="v2-r v2-num">{li.quantity}</td>
                    <td class="v2-r v2-num">{money(li.unit_price, deal.currency)}</td>
                    <!-- Blank, not "0", where there is no discount. A column of
                         zeroes reads as a discount that happened to be nothing. -->
                    <td class="v2-r v2-num v2-muted">
                      {li.discount_amount > 0 ? `−${money(li.discount_amount, deal.currency)}` : ''}
                    </td>
                    <td class="v2-r v2-num">{money(li.total, deal.currency)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
            <div
              style="display:flex;justify-content:flex-end;gap:24px;padding:11px 14px;border-top:1px solid var(--v2-line);font-size:13px"
            >
              {#if discount > 0}
                <span class="v2-muted">Subtotal</span>
                <span class="v2-num v2-muted">{money(subtotal, deal.currency)}</span>
                <span class="v2-muted">Discounts</span>
                <span class="v2-num v2-muted">−{money(discount, deal.currency)}</span>
              {/if}
              <span style="font-weight:650">Total</span>
              <span class="v2-num" style="font-weight:650">{money(deal.amount, deal.currency)}</span
              >
            </div>
          </div>
        {/if}
      </div>
    </div>
  </div>

  <aside class="v2-rail">
    <div class="v2-label v2-rail-head">Deal</div>
    <dl class="v2-kv">
      {#if data.pipeline}
        <dt>Pipeline</dt>
        <dd>{data.pipeline.name}</dd>
      {/if}
      <dt>Stage</dt>
      <dd>{deal.stage_label}</dd>
      <dt>Value</dt>
      <dd class="v2-num">{money(deal.amount, deal.currency)}</dd>
      <dt>Probability</dt>
      <dd class="v2-num">{deal.probability}%</dd>
      <dt>Expected close</dt>
      <dd>{longDate(deal.closed_on)}</dd>
      <dt>Type</dt>
      <dd>{OPPORTUNITY_TYPE_LABEL[deal.opportunity_type]}</dd>
      <dt>Source</dt>
      <dd style="text-transform:lowercase">{deal.lead_source || '—'}</dd>
      <dt>Owner</dt>
      <dd>{deal.assigned_to || 'Unassigned'}</dd>
      <!--
        "Last activity" used to be here, reading a `last_activity_at` the model
        does not have. Aging is measured from the last stage change, a
        narrower claim, and the one the board is actually coloured by, so that
        is what this row says now.
      -->
      <dt>Stage since</dt>
      <dd>{longDate(deal.stage_changed_at)}</dd>
    </dl>

    <div class="v2-label v2-rail-head">People</div>
    {#each contacts as c (c.id)}
      <!-- A link now that `/contacts/<uuid>` resolves. These names were
           plain text because the contacts module was still fixtures. -->
      <a
        class="v2-rail-row"
        href={resolve(`/contacts/${c.id}`)}
        style="color:inherit;text-decoration:none"
      >
        <Avatar name="{c.first_name} {c.last_name}" size={27} />
        <div style="min-width:0">
          <div style="font-size:12.5px;font-weight:550">{c.first_name} {c.last_name}</div>
          <!-- `relationship` (Champion, Blocker) was a fixture field. Contact
               has `title` and `department`, so the line says those. -->
          <div class="v2-sub" style="font-size:11px">
            {[c.title, c.department].filter(Boolean).join(' · ') || 'No title recorded'}
          </div>
        </div>
      </a>
    {:else}
      <p class="v2-sub" style="font-size:12px">
        Nobody is linked to this deal yet. Add the person who signs it.
      </p>
    {/each}

    <!--
      An "Attached" panel listing this account's invoices and tickets used to
      sit here. Both are real models, but neither is on the opportunity
      response. Filling it means two more cross-module requests per page load
      for a rail nobody asked for. Add it back deliberately if it earns them.
    -->
  </aside>
</div>

<style>
  .deal-error {
    margin: 0;
    padding: 9px 12px;
    font-size: 12.5px;
    color: var(--v2-clay);
    background: color-mix(in srgb, var(--v2-clay) 8%, transparent);
    border: 1px solid color-mix(in srgb, var(--v2-clay) 25%, transparent);
    border-radius: 7px;
  }
</style>
