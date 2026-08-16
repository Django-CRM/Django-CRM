import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const {
  createEscalationPolicy,
  updateEscalationPolicy,
  deleteEscalationPolicy,
  getEscalationPolicies,
  UPDATE_FIELDS
} = await import('./escalation.js');

const cookies = /** @type {any} */ ({ get: () => 'token' });
const event = /** @type {any} */ ({ cookies });

const base = {
  priority: 'Urgent',
  first_response_action: 'notify',
  resolution_action: 'reassign',
  first_response_target_id: 'p1',
  resolution_target_id: 'p2',
  notify_team_id: 't1',
  is_active: true
};

describe('UPDATE_FIELDS', () => {
  it('excludes priority, the frozen natural key', () => {
    expect(UPDATE_FIELDS).not.toContain('priority');
  });
});

describe('createEscalationPolicy', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('includes priority on create, along with the rest of the allow-list, and drops a hostile org', async () => {
    apiRequest.mockResolvedValue({});
    await createEscalationPolicy(event, {
      ...base,
      org: 'ATTACKER-ORG',
      created_by: 'ATTACKER'
    });
    const [url, opts] = apiRequest.mock.calls[0];
    expect(url).toBe('/cases/escalation-policies/');
    expect(opts.method).toBe('POST');
    expect(opts.body.priority).toBe('Urgent');
    expect(Object.keys(opts.body).sort()).toEqual([
      'first_response_action',
      'first_response_target_id',
      'is_active',
      'notify_team_id',
      'priority',
      'resolution_action',
      'resolution_target_id'
    ]);
    expect(opts.body.org).toBeUndefined();
    expect(opts.body.created_by).toBeUndefined();
  });

  it('sends null, not an empty string, for an unselected first_response_target_id', async () => {
    apiRequest.mockResolvedValue({});
    await createEscalationPolicy(event, { ...base, first_response_target_id: '' });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body.first_response_target_id).toBeNull();
  });

  it('sends null, not an empty string, for an unselected resolution_target_id', async () => {
    apiRequest.mockResolvedValue({});
    await createEscalationPolicy(event, { ...base, resolution_target_id: '' });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body.resolution_target_id).toBeNull();
  });

  it('sends null, not an empty string, for an unselected notify_team_id', async () => {
    apiRequest.mockResolvedValue({});
    await createEscalationPolicy(event, { ...base, notify_team_id: '' });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body.notify_team_id).toBeNull();
  });

  it('coerces is_active to a boolean', async () => {
    apiRequest.mockResolvedValue({});
    await createEscalationPolicy(event, { ...base, is_active: 'true' });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body.is_active).toBe(true);
    expect(typeof body.is_active).toBe('boolean');
  });

  it('rejects a priority outside the four choices before the round trip', async () => {
    await expect(createEscalationPolicy(event, { ...base, priority: 'Critical' })).rejects.toThrow(
      /priority/i
    );
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('rejects a missing priority before the round trip', async () => {
    await expect(createEscalationPolicy(event, { ...base, priority: '' })).rejects.toThrow(
      /priority/i
    );
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('updateEscalationPolicy', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('PUTs to the detail endpoint', async () => {
    apiRequest.mockResolvedValue({});
    await updateEscalationPolicy(event, 'e1', base);
    const [url, opts] = apiRequest.mock.calls[0];
    expect(url).toBe('/cases/escalation-policies/e1/');
    expect(opts.method).toBe('PUT');
  });

  it('never sends priority, even when the caller passes one', async () => {
    apiRequest.mockResolvedValue({});
    await updateEscalationPolicy(event, 'e1', base);
    const { body } = apiRequest.mock.calls[0][1];
    expect(body.priority).toBeUndefined();
    expect(Object.keys(body)).not.toContain('priority');
  });

  it('sends only what it is given, with no org/created_by', async () => {
    apiRequest.mockResolvedValue({});
    await updateEscalationPolicy(event, 'e1', {
      ...base,
      org: 'ATTACKER-ORG',
      created_by: 'ATTACKER'
    });
    const { body } = apiRequest.mock.calls[0][1];
    expect(Object.keys(body).sort()).toEqual([
      'first_response_action',
      'first_response_target_id',
      'is_active',
      'notify_team_id',
      'resolution_action',
      'resolution_target_id'
    ]);
    expect(body.org).toBeUndefined();
    expect(body.created_by).toBeUndefined();
  });

  it('sends exactly one key for a minimal { is_active: true } body (the turn-on path)', async () => {
    apiRequest.mockResolvedValue({});
    await updateEscalationPolicy(event, 'e1', { is_active: true });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body).toEqual({ is_active: true });
  });

  it('sends exactly one key for a minimal { is_active: false } body (the turn-off path)', async () => {
    apiRequest.mockResolvedValue({});
    await updateEscalationPolicy(event, 'e1', { is_active: false });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body).toEqual({ is_active: false });
  });

  it('sends null, not an empty string, for an unselected target on update', async () => {
    apiRequest.mockResolvedValue({});
    await updateEscalationPolicy(event, 'e1', { ...base, first_response_target_id: '' });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body.first_response_target_id).toBeNull();
  });

  it('throws when the id is missing', async () => {
    await expect(updateEscalationPolicy(event, '', base)).rejects.toThrow(/which policy/i);
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('deleteEscalationPolicy', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('DELETEs the detail endpoint', async () => {
    apiRequest.mockResolvedValue({});
    await deleteEscalationPolicy(event, 'e1');
    const [url, opts] = apiRequest.mock.calls[0];
    expect(url).toBe('/cases/escalation-policies/e1/');
    expect(opts.method).toBe('DELETE');
  });

  it('throws when the id is missing', async () => {
    await expect(deleteEscalationPolicy(event, '')).rejects.toThrow(/which policy/i);
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('SLA targets on the policy', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('maps the org-configured hours onto the card', async () => {
    apiRequest.mockResolvedValue({
      policies: [
        {
          id: 'e1',
          priority: 'Urgent',
          is_active: true,
          first_response_hours: 2,
          resolution_hours: 8
        }
      ]
    });
    const { policies } = await getEscalationPolicies(event);
    expect(policies[0].first_response_hours).toBe(2);
    expect(policies[0].resolution_hours).toBe(8);
  });

  it('maps an unconfigured target to null so the card can say "default"', async () => {
    apiRequest.mockResolvedValue({
      policies: [{ id: 'e1', priority: 'Urgent', is_active: true }]
    });
    const { policies } = await getEscalationPolicies(event);
    expect(policies[0].first_response_hours).toBeNull();
    expect(policies[0].resolution_hours).toBeNull();
  });

  it('sends the hours on create', async () => {
    apiRequest.mockResolvedValue({});
    await createEscalationPolicy(event, {
      ...base,
      first_response_hours: 3,
      resolution_hours: 12
    });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body.first_response_hours).toBe(3);
    expect(body.resolution_hours).toBe(12);
  });

  it('sends a number, not the string the form posts', async () => {
    apiRequest.mockResolvedValue({});
    await createEscalationPolicy(event, { ...base, first_response_hours: '3' });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body.first_response_hours).toBe(3);
  });

  it('clears a target to null when the input is emptied, not 0 or ""', async () => {
    apiRequest.mockResolvedValue({});
    await updateEscalationPolicy(event, 'e1', { first_response_hours: '', resolution_hours: '' });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body.first_response_hours).toBeNull();
    expect(body.resolution_hours).toBeNull();
  });

  it('rejects a non-numeric target before the round trip', async () => {
    await expect(
      createEscalationPolicy(event, { ...base, first_response_hours: 'soon' })
    ).rejects.toThrow(/hours/i);
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('rejects a zero target before the round trip, matching the serializer', async () => {
    await expect(
      createEscalationPolicy(event, { ...base, first_response_hours: 0 })
    ).rejects.toThrow(/hours/i);
    expect(apiRequest).not.toHaveBeenCalled();
  });
});
