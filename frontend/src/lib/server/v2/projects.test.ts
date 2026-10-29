import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('#lib/api-helpers.js', () => ({
  apiRequest: (...a: any[]) => apiRequest(...a)
}));

const {
  addProjectNote,
  billProjectTime,
  createProject,
  deleteProject,
  getProject,
  getProjectDraft,
  getProjectFormOptions,
  getUnbilled,
  invoiceProjectAmount,
  listProjects,
  listProjectTime,
  logProjectTime,
  projectChoice,
  projectChoices,
  startProjectTimer,
  updateProject
} = await import('#lib/server/v2/projects.js');

const event = { cookies: { get: () => 'token' } } as any;

const refusal = (status: number, body?: any) =>
  Object.assign(new Error(`HTTP ${status}`), { status, body });

const PROFILE = { id: 'p1', user_details: { email: 'ana@example.com', name: 'Ana' } };

const ITEM = {
  id: 'pr1',
  name: 'Rebuild',
  account: { id: 'a1', name: 'Acme' },
  deal: { id: 'd1', name: 'Acme rebuild' },
  status: 'active',
  billing_type: 'fixed_fee',
  currency: 'EUR',
  budget_hours: '40.00',
  budget_amount: '12000.00',
  hourly_rate: null,
  start_date: '2026-11-01',
  due_date: null,
  assigned_to: [PROFILE],
  tags: [{ id: 't1', name: 'web' }],
  burn: {
    logged_hours: '34.50',
    billable_hours: '30.00',
    billable_value: '2850.00',
    unbilled_value: '950.00',
    invoiced_amount: '3000.00',
    hours_percent: 86,
    amount_percent: null
  },
  created_at: '2026-10-20T10:00:00Z',
  updated_at: '2026-10-21T10:00:00Z'
};

beforeEach(() => {
  apiRequest.mockReset();
});

describe('listProjects', () => {
  it('reads the list and its count, turning decimals into numbers and keeping null', async () => {
    apiRequest.mockResolvedValue({ projects: [ITEM], count: 7 });
    const { results, totals } = await listProjects(
      event,
      new URLSearchParams({ status: 'active' })
    );
    expect(apiRequest.mock.calls[0][0]).toBe('/projects/?status=active&limit=50');
    expect(totals.count).toBe(7);
    expect(results[0]).toMatchObject({
      id: 'pr1',
      account: { id: 'a1', name: 'Acme' },
      deal: { id: 'd1', name: 'Acme rebuild' },
      budget_hours: 40,
      budget_amount: 12000,
      hourly_rate: null,
      assigned_to: [{ id: 'p1', name: 'Ana' }],
      tags: ['web'],
      burn: { logged_hours: 34.5, unbilled_value: 950, hours_percent: 86, amount_percent: null }
    });
  });

  it('becomes the module page when Projects is switched off', async () => {
    apiRequest.mockRejectedValue(
      refusal(404, { code: 'module_off', detail: 'Projects is switched off.', module: 'projects' })
    );
    await expect(listProjects(event)).rejects.toMatchObject({
      status: 404,
      body: { code: 'module_off', module: 'projects' }
    });
  });

  it('gives the task form an empty picker rather than failing it', async () => {
    apiRequest.mockRejectedValue(refusal(404, { detail: 'Not found.' }));
    expect(await projectChoices(event)).toEqual([]);
    apiRequest.mockResolvedValue({ projects: [ITEM] });
    expect(await projectChoices(event)).toEqual([{ id: 'pr1', name: 'Rebuild' }]);
    expect(apiRequest.mock.calls[1][0]).toBe('/projects/?limit=200');
  });
});

describe('getProject', () => {
  it('shapes the detail, its comments, files and invoices, and the three flags', async () => {
    apiRequest.mockResolvedValue({
      ...ITEM,
      description: 'Phase one',
      custom_fields: { po: 'PO-1' },
      rates: [{ profile: PROFILE, hourly_rate: '90.00' }],
      comments: [
        {
          id: 1,
          comment: 'Kickoff done',
          commented_on: '2026-10-21T09:00:00Z',
          commented_by: { id: 'p1', user_details: { email: 'ana@example.com', name: null } }
        }
      ],
      attachments: [
        { id: 'f1', file_name: 'brief.pdf', created_at: '2026-10-21T08:00:00Z' },
        { id: 'f2', file_name: '', created_at: '2026-10-22T08:00:00Z' }
      ],
      invoices: [
        {
          id: 'i1',
          invoice_number: 'INV-1',
          status: 'Draft',
          total_amount: '3000.00',
          currency: 'EUR',
          issue_date: '2026-10-22'
        }
      ],
      can_edit: true,
      can_delete: false,
      can_bill: true
    });
    const data = await getProject(event, 'pr1');
    expect(apiRequest.mock.calls[0][0]).toBe('/projects/pr1/');
    expect(data.project.rates).toEqual([{ profile: { id: 'p1', name: 'Ana' }, hourly_rate: 90 }]);
    expect(data.project.invoices[0]).toMatchObject({ invoice_number: 'INV-1', total_amount: 3000 });
    expect(data.comments).toEqual([
      {
        id: '1',
        type: 'note',
        at: '2026-10-21T09:00:00Z',
        by: 'ana@example.com',
        body: 'Kickoff done'
      }
    ]);
    expect(data.files.map((f) => [f.name, f.href])).toEqual([
      ['Attachment', '/api/attachments/f2/download'],
      ['brief.pdf', '/api/attachments/f1/download']
    ]);
    expect([data.canEdit, data.canDelete, data.canBill]).toEqual([true, false, true]);
  });

  it('answers a hidden or missing project with one 404 whose copy covers both', async () => {
    apiRequest.mockRejectedValue(refusal(404, { detail: 'Not found.' }));
    await expect(getProject(event, 'pr1')).rejects.toMatchObject({
      status: 404,
      body: { message: expect.stringContaining('do not have access') }
    });
  });

  it('shows the module page for an off module, not the missing-record copy', async () => {
    apiRequest.mockRejectedValue(
      refusal(404, { code: 'module_off', detail: 'Projects is switched off.', module: 'projects' })
    );
    await expect(getProject(event, 'pr1')).rejects.toMatchObject({
      status: 404,
      body: { message: 'Projects is switched off.', code: 'module_off' }
    });
  });

  it('reads no flag as allowed when an older answer leaves them out', async () => {
    apiRequest.mockResolvedValue({ ...ITEM });
    const data = await getProject(event, 'pr1');
    expect([data.canEdit, data.canDelete, data.canBill]).toEqual([false, false, false]);
    expect(data.project.invoices).toEqual([]);
    expect(data.project.estimate).toBeNull();
  });

  it('names the estimate that started the project', async () => {
    apiRequest.mockResolvedValue({ ...ITEM, estimate: { id: 'e1', estimate_number: 'EST-0007' } });
    const data = await getProject(event, 'pr1');
    expect(data.project.estimate).toEqual({ id: 'e1', estimate_number: 'EST-0007' });

    apiRequest.mockResolvedValue({ ...ITEM, estimate: null });
    expect((await getProject(event, 'pr1')).project.estimate).toBeNull();
  });
});

describe('writes', () => {
  it('creates, patches and deletes on the record routes', async () => {
    apiRequest.mockResolvedValue({ error: false, id: 'pr9', project: { id: 'pr9' } });
    expect(await createProject(event, { name: 'X' })).toEqual({ id: 'pr9' });
    expect(apiRequest.mock.calls[0].slice(0, 2)).toEqual([
      '/projects/',
      { method: 'POST', body: { name: 'X' } }
    ]);

    await updateProject(event, 'pr9', { status: 'on_hold' });
    expect(apiRequest.mock.calls[1].slice(0, 2)).toEqual([
      '/projects/pr9/',
      { method: 'PATCH', body: { status: 'on_hold' } }
    ]);

    await deleteProject(event, 'pr9');
    expect(apiRequest.mock.calls[2].slice(0, 2)).toEqual(['/projects/pr9/', { method: 'DELETE' }]);
  });

  it('posts a comment as JSON and a file as the project attachment field', async () => {
    apiRequest.mockResolvedValue({});
    await addProjectNote(event, 'pr1', 'Hello');
    expect(apiRequest.mock.calls[0][1]).toEqual({ method: 'POST', body: { comment: 'Hello' } });

    const file = new File(['x'], 'brief.pdf');
    await addProjectNote(event, 'pr1', '', file);
    const body = apiRequest.mock.calls[1][1].body;
    expect(body).toBeInstanceOf(FormData);
    expect(body.get('project_attachment')).toBeInstanceOf(File);
    expect(body.has('comment')).toBe(false);
  });

  it('opens the form empty when the deal is hidden or the route is missing', async () => {
    apiRequest.mockResolvedValue({ name: 'Acme rebuild', account: 'a1' });
    expect(await getProjectDraft(event, 'd1')).toEqual({ name: 'Acme rebuild', account: 'a1' });
    expect(apiRequest.mock.calls[0][0]).toBe('/projects/from-deal/d1/');

    apiRequest.mockRejectedValue(refusal(404, { detail: 'Not found.' }));
    expect(await getProjectDraft(event, 'd1')).toBeNull();
  });
});

describe('time on a project', () => {
  it('lists entries, and answers null when the read is refused', async () => {
    apiRequest.mockResolvedValue([{ id: 'e1', case: null, project: 'pr1' }]);
    expect(await listProjectTime(event, 'pr1')).toHaveLength(1);
    expect(apiRequest.mock.calls[0][0]).toBe('/projects/pr1/time-entries/');
    apiRequest.mockRejectedValue(refusal(404, { code: 'module_off' }));
    expect(await listProjectTime(event, 'pr1')).toBeNull();
  });

  it('starts a timer on the project, or on one of its tasks, billable as the project is', async () => {
    apiRequest.mockResolvedValue({});
    await startProjectTimer(event, 'pr1', { billable: true });
    await startProjectTimer(event, 'pr1', { task: 'task1', billable: false });
    expect(apiRequest.mock.calls[0].slice(0, 2)).toEqual([
      '/projects/pr1/time-entries/start/',
      { method: 'POST', body: { billable: true } }
    ]);
    expect(apiRequest.mock.calls[1][1].body).toEqual({ billable: false, task: 'task1' });
  });

  it('logs a session with its task and no currency, so the project sets it', async () => {
    apiRequest.mockResolvedValue({});
    await logProjectTime(event, 'pr1', {
      minutes: '30',
      description: 'Copy',
      billable: true,
      hourlyRate: '',
      task: 'task1'
    });
    const [path, options] = apiRequest.mock.calls[0];
    expect(path).toBe('/projects/pr1/time-entries/');
    expect(options.body).toMatchObject({ description: 'Copy', billable: true, task: 'task1' });
    expect(options.body).not.toHaveProperty('currency');
    expect(options.body).not.toHaveProperty('hourly_rate');
  });

  it('refuses a session the form should not send, without a request', async () => {
    await expect(logProjectTime(event, 'pr1', { minutes: '0', description: 'x' })).rejects.toThrow(
      /Minutes/
    );
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('billing a project', () => {
  it('reads the unbilled preview, and null when an admin-only route refuses', async () => {
    apiRequest.mockResolvedValue({
      entry_count: 3,
      hours: '4.50',
      value: '427.50',
      currency: 'EUR'
    });
    expect(await getUnbilled(event, 'pr1', '2026-10-31')).toEqual({
      entry_count: 3,
      hours: 4.5,
      value: 427.5,
      unrated_count: 0,
      currency: 'EUR'
    });
    expect(apiRequest.mock.calls[0][0]).toBe('/projects/pr1/unbilled/?up_to=2026-10-31');

    apiRequest.mockRejectedValue(refusal(403, { error: true }));
    expect(await getUnbilled(event, 'pr1', 'not-a-date')).toBeNull();
    expect(apiRequest.mock.calls[1][0]).toBe('/projects/pr1/unbilled/');
  });

  it('bills time by project id, with the date and a grouping the API takes', async () => {
    apiRequest.mockResolvedValue({ invoice_id: 'i1', invoice_number: 'INV-1' });
    expect(
      await billProjectTime(event, 'pr1', { upTo: '2026-10-31', groupBy: 'person' })
    ).toMatchObject({ invoice_id: 'i1' });
    expect(apiRequest.mock.calls[0].slice(0, 2)).toEqual([
      '/invoices/from-time-entries/',
      {
        method: 'POST',
        body: { project_id: 'pr1', group_by: 'person', up_to: '2026-10-31' }
      }
    ]);

    await billProjectTime(event, 'pr1', { upTo: '', groupBy: 'all of it' });
    expect(apiRequest.mock.calls[1][1].body).toEqual({ project_id: 'pr1', group_by: 'entry' });
  });

  it('passes a member refusal on for the page to show', async () => {
    apiRequest.mockRejectedValue(refusal(403, { error: true, message: 'Only admins can.' }));
    await expect(billProjectTime(event, 'pr1', {})).rejects.toMatchObject({ status: 403 });
  });

  it('invoices an amount on its own route', async () => {
    apiRequest.mockResolvedValue({ invoice_id: 'i2' });
    await invoiceProjectAmount(event, 'pr1', { percent: '25' });
    expect(apiRequest.mock.calls[0].slice(0, 2)).toEqual([
      '/projects/pr1/invoice-amount/',
      { method: 'POST', body: { percent: '25' } }
    ]);
  });
});

describe('getProjectFormOptions', () => {
  it('skips the deals while Pipeline is off and says when the team did not load', async () => {
    apiRequest.mockImplementation(async (path: string) => {
      if (path.startsWith('/accounts/')) {
        return { active_accounts: { open_accounts: [{ id: 'a1', name: 'Acme' }] } };
      }
      throw refusal(500);
    });
    const options = await getProjectFormOptions(event, { dealsOn: false });
    expect(options).toEqual({
      accounts: [{ id: 'a1', name: 'Acme' }],
      people: [],
      peopleFailed: true,
      deals: null
    });
    expect(apiRequest.mock.calls.some(([p]) => String(p).startsWith('/opportunities/'))).toBe(
      false
    );
  });
});

describe('the contract after review', () => {
  it('counts the unbilled entries that have no rate', async () => {
    apiRequest.mockResolvedValue({
      entry_count: 3,
      hours: '3.00',
      value: '200.00',
      unrated_count: 1,
      currency: 'EUR'
    });
    expect((await getUnbilled(event, 'pr1', '2026-10-31'))?.unrated_count).toBe(1);
  });

  it('keeps an invoiced amount the API withholds as null, not 0', async () => {
    apiRequest.mockResolvedValue({ projects: [{ ...ITEM, burn: { invoiced_amount: null } }] });
    const { results } = await listProjects(event);
    expect(results[0].burn.invoiced_amount).toBeNull();
    apiRequest.mockResolvedValue({ projects: [ITEM] });
    expect((await listProjects(event)).results[0].burn.invoiced_amount).toBe(3000);
  });

  it('reads one project as a picker option, and null when hidden or malformed', async () => {
    apiRequest.mockResolvedValue({ ...ITEM });
    const id = '4d60686d-bd3d-4e33-b114-ab36f194a6cd';
    expect(await projectChoice(event, id)).toEqual({ id: 'pr1', name: 'Rebuild' });
    expect(apiRequest.mock.calls[0][0]).toBe(`/projects/${id}/`);

    apiRequest.mockRejectedValue(refusal(404, { detail: 'Not found.' }));
    expect(await projectChoice(event, id)).toBeNull();

    apiRequest.mockClear();
    expect(await projectChoice(event, 'not-an-id')).toBeNull();
    expect(apiRequest).not.toHaveBeenCalled();
  });
});
