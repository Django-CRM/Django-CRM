import { describe, it, expect } from 'vitest';
import { hubGroups } from './settings-hub.js';

const row = (key: string, on: boolean = true) => ({
  key,
  enabled: on,
  available: on
});

function data(more: Record<string, any> = {}): any {
  return {
    org: { name: 'Acme', company_name: 'Acme Ltd' },
    is_organization_admin: true,
    modules: null,
    calendar: { name: 'Standard', days: [{ open: '09:00', close: '17:00' }] },
    peopleTotals: { count: 2, admins: 1, tokens_on_deactivated: 0 },
    tokenTotals: { live: 1, orphaned: 0, unused_90d: 0 },
    routingTotals: { active: 1, unrouted_last_30d: 0 },
    escalationTotals: { active: 1, count: 2, breaches_unhandled_30d: 0 },
    mailboxTotals: { active: 1, count: 1, silently_dropping: 0 },
    reopen: { is_enabled: true, reopen_window_days: 14 },
    approvalRules: [],
    approvalTotals: { active: 0 },
    macroTotals: { org: 3, with_unknown_placeholders: 0 },
    tagTotals: { active: 4 },
    fieldTotals: { active: 1, models_extended: 1, required_with_gaps: 0 },
    webFormTotals: { published: 1, submissions_30d: 2 },
    helpCenter: { help_center_enabled: false, help_center_slug: '' },
    automation: null,
    customObjects: null,
    ...more
  };
}

const titles = (d: any) => hubGroups(d).flatMap((g) => g.items.map((i) => i.title));
const item = (d: any, title: string) =>
  hubGroups(d)
    .flatMap((g) => g.items)
    .find((i) => i.title === title);

const NULL_TICKETS = {
  calendar: null,
  routingTotals: null,
  escalationTotals: null,
  mailboxTotals: null,
  reopen: null,
  approvalRules: null,
  approvalTotals: null,
  macroTotals: null
};

describe('hubGroups', () => {
  it('leaves out the tickets group and Macros when tickets is off', () => {
    const d = data({ modules: [row('tickets', false)], ...NULL_TICKETS });
    expect(hubGroups(d).map((g) => g.label)).not.toContain('How tickets are handled');
    expect(titles(d)).not.toContain('Macros');
    expect(titles(d)).not.toContain('Ticket routing');
  });

  it('names in its copy only the modules that are on', () => {
    const all = data();
    expect(item(all, 'Organization')?.body).toContain('every invoice and estimate');
    expect(item(all, 'Tags')?.body).toBe(
      'Labels shared across accounts, leads, deals and tickets.'
    );
    expect(item(all, 'Web forms')?.body).toContain('becomes a lead or a ticket.');

    const off = data({
      modules: [row('invoices', false), row('tickets', false), row('deals', false)],
      ...NULL_TICKETS
    });
    expect(item(off, 'Organization')?.body).not.toContain('invoice');
    expect(item(off, 'Tags')?.body).toBe('Labels shared across accounts and leads.');
    expect(item(off, 'Web forms')?.body).toContain('becomes a lead.');

    const ticketsOnly = data({ modules: [row('leads', false)] });
    expect(item(ticketsOnly, 'Web forms')?.body).toContain('becomes a ticket.');
    const neither = data({
      modules: [row('leads', false), row('tickets', false)],
      ...NULL_TICKETS
    });
    expect(item(neither, 'Web forms')?.body).toBe('Forms you embed on your own site.');
  });

  it('leaves out the Help center when knowledge base is off', () => {
    const d = data({ modules: [row('knowledge_base', false)], helpCenter: null });
    expect(titles(d)).not.toContain('Help center');
    expect(titles(data())).toContain('Help center');
  });

  it('still builds the ticket rows, without values, when their totals are null', () => {
    const d = data({ modules: [row('tickets')], ...NULL_TICKETS });
    expect(titles(d)).toEqual(
      expect.arrayContaining(['Ticket routing', 'Inbound email', 'Macros'])
    );
    expect(item(d, 'Ticket routing')).toMatchObject({ value: null, warn: false });
    expect(item(d, 'Business hours')?.value).toBeNull();
  });

  it('shows admins an Apps row after Organization, and members none', () => {
    const allOn = data({ modules: [row('tasks'), row('goals')] });
    expect(item(allOn, 'Apps')).toMatchObject({ href: '/settings/apps', value: 'All modules on' });
    const g = hubGroups(allOn)[0].items.map((i) => i.title);
    expect(g.indexOf('Apps')).toBe(g.indexOf('Organization') + 1);
    const two = data({ modules: [row('tasks', false), row('goals', false), row('leads')] });
    expect(item(two, 'Apps')?.value).toBe('2 modules off');
    expect(titles(data({ is_organization_admin: false }))).not.toContain('Apps');
  });

  it('puts Products after Invoice templates only while products are on', () => {
    const on = titles(data());
    expect(on.indexOf('Products')).toBe(on.indexOf('Invoice templates') + 1);
    const none = titles(data({ modules: [row('deals', false), row('invoices', false)] }));
    expect(none).not.toContain('Products');
    expect(none).not.toContain('Invoice templates');
    expect(titles(data({ modules: [row('invoices', false)] }))).toContain('Products');
  });
});

describe('the lead rows', () => {
  it('lists lead capture and sources while Leads is on', () => {
    const d = data({
      leadSources: { active: 14, count: 15 },
      leadIntakes: { count: 2, active: 1 }
    });
    expect(item(d, 'Lead capture')?.value).toBe('1 of 2 webhooks on');
    expect(item(d, 'Lead sources')?.value).toBe('14 in use');
  });

  it('keeps lead capture without numbers for a member, and hides sources on an older API', () => {
    const d = data({ is_organization_admin: false, leadSources: false, leadIntakes: null });
    expect(item(d, 'Lead capture')?.value).toBeNull();
    expect(titles(d)).not.toContain('Lead sources');
  });

  it('leaves the group out while Leads is off, and keeps the sources deals use', () => {
    const d = data({
      modules: [row('leads', false)],
      leadSources: { active: 14, count: 15 },
      leadIntakes: null
    });
    expect(hubGroups(d).map((g) => g.label)).not.toContain('How leads arrive');
    expect(item(d, 'Lead sources')?.value).toBe('14 in use');
  });

  it('files lead sources with the shared lists, after the lead pipelines', () => {
    const d = data({ leadSources: { active: 3, count: 3 } });
    const shared = hubGroups(d).find((g) => g.label === 'Shared words and fields');
    const names = (shared?.items ?? []).map((i) => i.title);
    expect(names.indexOf('Lead sources')).toBe(names.indexOf('Lead pipelines') + 1);
  });

  it('lists inbound email with the lead rows while Tickets is off', () => {
    const d = data({ modules: [row('tickets', false)], ...NULL_TICKETS, mailboxLeads: true });
    d.mailboxTotals = { active: 1, count: 2, silently_dropping: 0 };
    const leads = hubGroups(d).find((g) => g.label === 'How leads arrive');
    const inbound = leads?.items.find((i) => i.title === 'Inbound email');
    expect(inbound?.body).toBe('The addresses that turn email into leads.');
    expect(inbound?.value).toBe('1 of 2 active');
    expect(titles(d).filter((t) => t === 'Inbound email')).toHaveLength(1);

    const noLeadAddresses = data({ modules: [row('tickets', false)], ...NULL_TICKETS });
    expect(titles(noLeadAddresses)).not.toContain('Inbound email');
  });

  it('says inbound email makes leads only where it can', () => {
    expect(item(data({ mailboxLeads: true }), 'Inbound email')?.body).toContain('or leads');
    expect(item(data({ mailboxLeads: false }), 'Inbound email')?.body).not.toContain('leads');
  });
});

describe('the billing row', () => {
  const billing = (more: Record<string, any> = {}) => ({
    plan: { key: 'a', name: 'Plan A' },
    canManage: false,
    state: null,
    notice: null,
    plans: null,
    ...more
  });

  it('is absent wherever the server offers no billing', () => {
    expect(titles(data({ billing: null }))).not.toContain('Billing');
    expect(titles(data({ is_organization_admin: false, billing: null }))).not.toContain('Billing');
    expect(titles(data())).not.toContain('Billing');
  });

  it('shows a member the plan name and nothing to act on', () => {
    const row = item(data({ is_organization_admin: false, billing: billing() }), 'Billing');
    expect(row).toEqual({
      href: '/settings/billing',
      title: 'Billing',
      body: "This organization's plan.",
      value: 'Plan A',
      warn: false
    });
  });

  it('warns an admin while the server has a notice, after Apps', () => {
    const d = data({ billing: billing({ notice: { sentence: 'The last payment failed.' } }) });
    expect(item(d, 'Billing')?.warn).toBe(true);
    expect(item(d, 'Billing')?.body).toContain('how it is paid for');
    const names = titles(d);
    expect(names.indexOf('Billing')).toBe(names.indexOf('Apps') + 1);
  });
});

describe('the connected apps row', () => {
  const objects = { objects: [], link_targets: [] };

  it('is listed for an admin where custom objects are offered', () => {
    const row = item(data({ customObjects: objects }), 'Connected apps');
    expect(row?.href).toBe('/settings/connected-apps');
  });

  it('is absent when custom objects are null, and for a member', () => {
    expect(titles(data({ customObjects: null }))).not.toContain('Connected apps');
    expect(titles(data({ is_organization_admin: false, customObjects: objects }))).not.toContain(
      'Connected apps'
    );
  });
});
