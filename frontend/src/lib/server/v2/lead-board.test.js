import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { getLeadBoard, moveLead, UNSTAGED } = await import('$lib/server/v2/lead-board.js');
const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

const PIPELINES = {
  pipelines: [
    { id: 'p-default', name: 'Inbound' },
    { id: 'p-pack', name: 'Admissions' }
  ]
};

const KANBAN = {
  mode: 'pipeline',
  columns: [
    {
      id: 's-2',
      name: 'Admitted',
      order: 2,
      color: '#10B981',
      wip_limit: null,
      lead_count: 0,
      leads: []
    },
    {
      id: 's-1',
      name: 'New enquiry',
      order: 1,
      color: 'red;background:url(x)',
      wip_limit: 5,
      lead_count: 150,
      leads: [
        {
          id: 'l-1',
          full_name: 'Asha Rao',
          company_name: 'Rao & Co',
          rating: 'HOT',
          is_follow_up_overdue: true,
          assigned_to: [{ user_details: { email: 'owner@example.com' } }]
        }
      ]
    }
  ],
  unstaged: { lead_count: 1, leads: [{ id: 'l-2', full_name: 'Ben', assigned_to: [] }] }
};

describe('getLeadBoard', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('returns an empty board, without asking for a kanban, when the org has no pipelines', async () => {
    apiRequest.mockResolvedValueOnce({ pipelines: [] });
    const board = await getLeadBoard(event, null);
    expect(board).toEqual({ pipelines: [], pipeline: null, lanes: [] });
    expect(apiRequest).toHaveBeenCalledOnce();
  });

  it('opens the requested pipeline when the API listed it', async () => {
    apiRequest.mockResolvedValueOnce(PIPELINES).mockResolvedValueOnce(KANBAN);
    const board = await getLeadBoard(event, 'p-pack');
    expect(board.pipeline).toEqual({ id: 'p-pack', name: 'Admissions' });
    expect(apiRequest.mock.calls[1][0]).toBe('/leads/kanban/?pipeline_id=p-pack');
  });

  it('never forwards an id the API did not list, falling back to the first pipeline', async () => {
    apiRequest.mockResolvedValueOnce(PIPELINES).mockResolvedValueOnce(KANBAN);
    const board = await getLeadBoard(event, '../../admin&pipeline_id=x');
    expect(board.pipeline.id).toBe('p-default');
    expect(apiRequest.mock.calls[1][0]).toBe('/leads/kanban/?pipeline_id=p-default');
  });

  it('puts "No stage" first, then the stages in their order', async () => {
    apiRequest.mockResolvedValueOnce(PIPELINES).mockResolvedValueOnce(KANBAN);
    const { lanes } = await getLeadBoard(event, 'p-pack');
    expect(lanes.map((/** @type {any} */ l) => l.id)).toEqual([UNSTAGED, 's-1', 's-2']);
    expect(lanes[0]).toMatchObject({ name: 'No stage', unstaged: true, count: 1 });
    expect(lanes[1].unstaged).toBe(false);
  });

  it('shapes cards and marks a lane capped below its true count', async () => {
    apiRequest.mockResolvedValueOnce(PIPELINES).mockResolvedValueOnce(KANBAN);
    const { lanes } = await getLeadBoard(event, 'p-pack');
    expect(lanes[1]).toMatchObject({ count: 150, truncated: true, wipLimit: 5 });
    expect(lanes[1].rows[0]).toEqual({
      id: 'l-1',
      name: 'Asha Rao',
      company: 'Rao & Co',
      rating: 'HOT',
      owner: 'owner@example.com',
      overdue: true
    });
    expect(lanes[2].truncated).toBe(false);
  });

  it('draws a stage colour that is not a plain hex value grey, since it lands in a style attribute', async () => {
    apiRequest.mockResolvedValueOnce(PIPELINES).mockResolvedValueOnce(KANBAN);
    const { lanes } = await getLeadBoard(event, 'p-pack');
    expect(lanes[1].color).toBe('#6B7280');
    expect(lanes[2].color).toBe('#10B981');
  });

  it('copes with a response that has no unstaged group', async () => {
    apiRequest
      .mockResolvedValueOnce(PIPELINES)
      .mockResolvedValueOnce({ ...KANBAN, unstaged: undefined });
    const { lanes } = await getLeadBoard(event, 'p-pack');
    expect(lanes[0]).toMatchObject({ unstaged: true, count: 0, rows: [] });
  });
});

describe('moveLead', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('PATCHes the move endpoint with the stage and only the neighbours it has', async () => {
    apiRequest.mockResolvedValue({ error: false });
    await moveLead(event, 'l-1', { stageId: 's-2', aboveId: 'l-9' });
    const [endpoint, options] = apiRequest.mock.calls[0];
    expect(endpoint).toBe('/leads/l-1/move/');
    expect(options.method).toBe('PATCH');
    expect(options.body).toEqual({ stage_id: 's-2', above_lead_id: 'l-9' });
  });
});
