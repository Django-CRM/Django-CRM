import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { getTicketBoard, moveTicket } = await import('$lib/server/v2/ticket-board.js');
const event = /** @type {any} */ ({ cookies: { get: () => 'token' } });

const PIPELINES = { pipelines: [{ id: 'p-1', name: 'Onboarding', is_default: true }] };

/** @param {string} id @param {number} order @param {Record<string, any>} [extra] */
function statusColumn(id, order, extra = {}) {
  return {
    id,
    name: id,
    order,
    color: '#3B82F6',
    is_status_column: true,
    wip_limit: null,
    case_count: 0,
    cases: [],
    ...extra
  };
}

const STATUS_BOARD = {
  mode: 'status',
  pipeline: null,
  total_cases: 3,
  columns: [
    statusColumn('Closed', 4),
    statusColumn('New', 1, {
      case_count: 150,
      cases: [
        {
          id: 'c-1',
          name: 'Printer on fire',
          priority: 'Urgent',
          account_name: 'Acme',
          assigned_to: [{ id: 'pr-1', user_details: { email: 'agent@example.com' } }],
          is_sla_breached: true,
          is_sla_at_risk: true
        },
        { id: 'c-2', name: '', priority: 'Low', account_name: null, is_sla_at_risk: true }
      ]
    }),
    statusColumn('Duplicate', 6),
    statusColumn('Pending', 3, { color: 'red;background:url(x)' })
  ]
};

const PIPELINE_BOARD = {
  mode: 'pipeline',
  pipeline: { id: 'p-1', name: 'Onboarding' },
  total_cases: 0,
  columns: [
    {
      id: 's-1',
      name: 'Triage',
      order: 1,
      color: '#10B981',
      is_status_column: false,
      maps_to_status: null,
      wip_limit: 5,
      case_count: 0,
      cases: []
    }
  ]
};

describe('getTicketBoard', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('opens in status mode by default, asking the kanban without a pipeline', async () => {
    apiRequest.mockResolvedValueOnce(PIPELINES).mockResolvedValueOnce(STATUS_BOARD);
    const board = await getTicketBoard(event, null);
    expect(apiRequest.mock.calls[0][0]).toBe('/cases/pipelines/');
    expect(apiRequest.mock.calls[1][0]).toBe('/cases/kanban/');
    expect(board).toMatchObject({ mode: 'status', pipeline: null, total: 3 });
    expect(board.pipelines).toEqual([{ id: 'p-1', name: 'Onboarding' }]);
  });

  it('switches to pipeline mode for a pipeline the API listed', async () => {
    apiRequest.mockResolvedValueOnce(PIPELINES).mockResolvedValueOnce(PIPELINE_BOARD);
    const board = await getTicketBoard(event, 'p-1');
    expect(apiRequest.mock.calls[1][0]).toBe('/cases/kanban/?pipeline_id=p-1');
    expect(board.mode).toBe('pipeline');
    expect(board.pipeline).toEqual({ id: 'p-1', name: 'Onboarding' });
    expect(board.lanes[0]).toMatchObject({ id: 's-1', name: 'Triage', wipLimit: 5 });
  });

  it('never forwards an unknown pipeline id, falling back to status mode', async () => {
    apiRequest.mockResolvedValueOnce(PIPELINES).mockResolvedValueOnce(STATUS_BOARD);
    const board = await getTicketBoard(event, '../../admin&pipeline_id=x');
    expect(apiRequest.mock.calls[1][0]).toBe('/cases/kanban/');
    expect(board.mode).toBe('status');
    expect(board.pipeline).toBeNull();
  });

  it('drops the Duplicate column and orders the rest', async () => {
    apiRequest.mockResolvedValueOnce(PIPELINES).mockResolvedValueOnce(STATUS_BOARD);
    const { lanes } = await getTicketBoard(event, null);
    expect(lanes.map((/** @type {any} */ l) => l.id)).toEqual(['New', 'Pending', 'Closed']);
  });

  it('draws a colour that is not a plain hex value grey, since it lands in a style attribute', async () => {
    apiRequest.mockResolvedValueOnce(PIPELINES).mockResolvedValueOnce(STATUS_BOARD);
    const { lanes } = await getTicketBoard(event, null);
    expect(lanes[0].color).toBe('#3B82F6');
    expect(lanes[1].color).toBe('#6B7280');
  });

  it('shapes cards and marks a lane capped below its true count', async () => {
    apiRequest.mockResolvedValueOnce(PIPELINES).mockResolvedValueOnce(STATUS_BOARD);
    const { lanes } = await getTicketBoard(event, null);
    expect(lanes[0]).toMatchObject({ count: 150, truncated: true, wipLimit: null });
    expect(lanes[0].rows).toEqual([
      {
        id: 'c-1',
        name: 'Printer on fire',
        account: 'Acme',
        owner: 'agent@example.com',
        priority: 'Urgent',
        slaBreached: true,
        slaAtRisk: false
      },
      {
        id: 'c-2',
        name: 'Untitled ticket',
        account: '',
        owner: '',
        priority: 'Low',
        slaBreached: false,
        slaAtRisk: true
      }
    ]);
    expect(lanes[1].truncated).toBe(false);
  });
});

describe('moveTicket', () => {
  beforeEach(() => {
    apiRequest.mockReset();
    apiRequest.mockResolvedValue({ error: false });
  });

  it('sends the column as a status in status mode, with only the neighbours it has', async () => {
    await moveTicket(event, 'c-1', { mode: 'status', laneId: 'Closed', aboveId: 'c-9' });
    const [endpoint, options] = apiRequest.mock.calls[0];
    expect(endpoint).toBe('/cases/c-1/move/');
    expect(options.method).toBe('PATCH');
    expect(options.body).toEqual({ status: 'Closed', above_case_id: 'c-9' });
  });

  it('sends the column as a stage_id in pipeline mode', async () => {
    await moveTicket(event, 'c-1', { mode: 'pipeline', laneId: 's-2', belowId: 'c-3' });
    expect(apiRequest.mock.calls[0][1].body).toEqual({ stage_id: 's-2', below_case_id: 'c-3' });
  });
});
