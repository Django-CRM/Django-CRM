import { describe, expect, it } from 'vitest';

import { resolvedLink, PRODUCED_VERBS } from './notifications.js';

/**
 * `Notification.link` is a value read out of the database, and it becomes an
 * href. These cases are the reason it is parsed rather than followed.
 */
describe('resolvedLink', () => {
  const id = '4d60686d-bd3d-4e33-b114-ab36f194a6cd';

  it('normalises both CRM ticket spellings to the v2 route', () => {
    expect(resolvedLink(`/tickets/${id}`)).toBe(`/tickets/${id}`);
    expect(resolvedLink(`/cases/${id}`)).toBe(`/tickets/${id}`);
  });

  it('normalises product support to /help, including rows written as /support', () => {
    expect(resolvedLink(`/help/${id}`)).toBe(`/help/${id}`);
    expect(resolvedLink(`/support/${id}`)).toBe(`/help/${id}`);
  });

  it('tolerates a trailing slash on either shape', () => {
    expect(resolvedLink(`/tickets/${id}/`)).toBe(`/tickets/${id}`);
    expect(resolvedLink(`/support/${id}/`)).toBe(`/help/${id}`);
  });

  it.each([
    ['https://evil.example/help/1', 'an absolute URL'],
    ['//evil.example/help/1', 'a protocol-relative URL'],
    ['javascript:alert(1)', 'a javascript: URL'],
    ['/help/1/../../admin', 'a traversal attempt'],
    ['/help/', 'a help path with no id'],
    ['/settings', 'an unrelated internal path'],
    ['', 'an empty string'],
    [null, 'null'],
    [{ toString: (): string => '/help/1' }, 'a non-string that stringifies']
  ])('refuses %s (%s)', (link, _description) => {
    expect(resolvedLink(link as any)).toBe('');
  });

  it('escapes an id so it cannot break out of the path', () => {
    expect(resolvedLink('/help/a b')).toBe('/help/a%20b');
  });
});

describe('automation notifications', () => {
  const id = '4d60686d-bd3d-4e33-b114-ab36f194a6cd';

  it('knows the four automation verbs', () => {
    for (const v of ['message', 'failed', 'paused', 'limit']) {
      expect(PRODUCED_VERBS).toContain(`automation.${v}`);
    }
  });

  it('resolves the record and settings links', () => {
    for (const base of ['leads', 'contacts', 'accounts', 'pipeline', 'tasks', 'tickets']) {
      expect(resolvedLink(`/${base}/${id}`)).toBe(`/${base}/${id}`);
    }
    expect(resolvedLink(`/objects/project_x/${id}`)).toBe(`/objects/project_x/${id}`);
    expect(resolvedLink('/settings/automation')).toBe('/settings/automation');
    expect(resolvedLink(`/settings/automation/${id}`)).toBe(`/settings/automation/${id}`);
  });

  it('resolves nothing for absolute, protocol-relative, javascript: and malformed links', () => {
    for (const link of [
      'https://evil.example/leads/1',
      '//evil.example',
      'javascript:alert(1)',
      '/leads/not-a-uuid',
      `/leads/${id}/x`,
      `/objects/BAD/${id}`,
      '/objects/ok_key/9',
      '/settings/automation/x',
      '/settings/automation/',
      `/LEADS/${id}`,
      `/leads/${id}/../../x`,
      `/leads/${id}?next=//evil`,
      '/%2F%2Fevil',
      `/leads/${id}/`,
      `\\leads\\${id}`,
      `/\\evil.example`,
      ` /leads/${id}`,
      `/leads/${id} `
    ]) {
      expect(resolvedLink(link)).toBe('');
    }
  });
});

describe('retainer invoice notifications', () => {
  const invoice = '4d60686d-bd3d-4e33-b114-ab36f194a6cd';

  it('knows the verb and opens the invoice', () => {
    expect(PRODUCED_VERBS).toContain('project.retainer_invoice');
    expect(resolvedLink(`/invoices/${invoice}`)).toBe(`/invoices/${invoice}`);
    expect(resolvedLink('/invoices/1')).toBe('');
  });
});

describe('project budget notifications', () => {
  const id = '4d60686d-bd3d-4e33-b114-ab36f194a6cd';

  it('knows the verb and opens the project', () => {
    expect(PRODUCED_VERBS).toContain('project.budget');
    expect(resolvedLink(`/projects/${id}`)).toBe(`/projects/${id}`);
  });

  it('opens nothing for a project link that is not a plain id', () => {
    for (const link of ['/projects/1', `/projects/${id}/edit`, `/projects/${id}?bill=1`]) {
      expect(resolvedLink(link)).toBe('');
    }
  });
});

describe('estimate notifications', () => {
  const id = '4d60686d-bd3d-4e33-b114-ab36f194a6cd';

  it('knows both verbs', () => {
    expect(PRODUCED_VERBS).toContain('estimate.accepted');
    expect(PRODUCED_VERBS).toContain('estimate.declined');
  });

  it('opens the project, the deposit invoice or the estimates list', () => {
    expect(resolvedLink(`/projects/${id}`)).toBe(`/projects/${id}`);
    expect(resolvedLink(`/invoices/${id}`)).toBe(`/invoices/${id}`);
    expect(resolvedLink('/invoices/estimates')).toBe('/invoices/estimates');
  });

  it('opens nothing for another invoices path or an id that is not a UUID', () => {
    for (const link of [
      '/invoices',
      '/invoices/',
      '/invoices/new',
      '/invoices/1',
      '/invoices/estimates/',
      '/invoices/estimates/new',
      `/invoices/estimates/${id}`,
      `/invoices/${id}/edit`,
      `/invoices/${id}?pay=1`,
      `https://evil.example/invoices/${id}`,
      '//invoices/estimates'
    ]) {
      expect(resolvedLink(link)).toBe('');
    }
  });
});

describe('lead notifications', () => {
  const id = '4d60686d-bd3d-4e33-b114-ab36f194a6cd';

  it('knows the four lead verbs and links to the lead', () => {
    for (const v of ['assigned', 'created', 'enquired', 'waiting']) {
      expect(PRODUCED_VERBS).toContain(`lead.${v}`);
    }
    expect(resolvedLink(`/leads/${id}`)).toBe(`/leads/${id}`);
  });

  it('opens the contact for a repeat enquiry on a converted lead', () => {
    expect(resolvedLink(`/contacts/${id}`)).toBe(`/contacts/${id}`);
  });
});

describe('object match and seat notifications', () => {
  const id = '4d60686d-bd3d-4e33-b114-ab36f194a6cd';

  it('knows both verbs and links to the custom record', () => {
    expect(PRODUCED_VERBS).toContain('real_estate.match');
    expect(PRODUCED_VERBS).toContain('education.seat_open');
    expect(resolvedLink(`/objects/batch/${id}`)).toBe(`/objects/batch/${id}`);
    expect(resolvedLink('/objects/Bad Key/' + id)).toBe('');
  });
});
