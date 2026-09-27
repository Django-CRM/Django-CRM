import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiRequest = vi.fn();
vi.mock('$lib/api-helpers.js', () => ({ apiRequest: (...a) => apiRequest(...a) }));

const { createMailbox, updateMailbox, deleteMailbox, getMailboxes, topicArnEdit } =
  await import('./inbound-email.js');

const ARN = 'arn:aws:sns:us-east-1:123456789012:inbound';

const cookies = /** @type {any} */ ({ get: () => 'token' });
const event = /** @type {any} */ ({ cookies });

const base = {
  address: 'Support@Example.io',
  provider: 'ses',
  default_priority: 'Normal',
  default_case_type: 'Question',
  default_assignee_id: 'p1',
  is_active: true
};

describe('createMailbox', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('sends only allow-listed keys on create and drops a hostile org', async () => {
    apiRequest.mockResolvedValue({});
    await createMailbox(event, {
      ...base,
      org: 'ATTACKER-ORG',
      created_by: 'ATTACKER'
    });
    const [url, opts] = apiRequest.mock.calls[0];
    expect(url).toBe('/cases/mailboxes/');
    expect(opts.method).toBe('POST');
    expect(Object.keys(opts.body).sort()).toEqual([
      'address',
      'default_assignee_id',
      'default_case_type',
      'default_priority',
      'is_active',
      'provider'
    ]);
    expect(opts.body.org).toBeUndefined();
    expect(opts.body.created_by).toBeUndefined();
  });

  it('never sends a webhook_secret, even when handed one', async () => {
    apiRequest.mockResolvedValue({});
    await createMailbox(event, { ...base, webhook_secret: 'ATTACKER-CHOSEN' });
    const [, opts] = apiRequest.mock.calls[0];
    expect(opts.body.webhook_secret).toBeUndefined();
  });

  it('sends an admin-entered topic_arn, trimmed', async () => {
    apiRequest.mockResolvedValue({});
    await createMailbox(event, { ...base, topic_arn: `  ${ARN} ` });
    const [, opts] = apiRequest.mock.calls[0];
    expect(opts.body.topic_arn).toBe(ARN);
  });

  it('trims and lowercases the address before sending', async () => {
    apiRequest.mockResolvedValue({});
    await createMailbox(event, { ...base, address: '  Support@Example.IO  ' });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body.address).toBe('support@example.io');
  });

  it('sends null, not an empty string, for an unselected default_case_type', async () => {
    apiRequest.mockResolvedValue({});
    await createMailbox(event, { ...base, default_case_type: '' });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body.default_case_type).toBeNull();
  });

  it('sends null, not an empty string, for an unselected default_assignee_id', async () => {
    apiRequest.mockResolvedValue({});
    await createMailbox(event, { ...base, default_assignee_id: '' });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body.default_assignee_id).toBeNull();
  });

  it('coerces is_active to a boolean', async () => {
    apiRequest.mockResolvedValue({});
    await createMailbox(event, { ...base, is_active: 'true' });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body.is_active).toBe(true);
    expect(typeof body.is_active).toBe('boolean');
  });

  it('refuses a mailbox with no address before making a request', async () => {
    await expect(createMailbox(event, { ...base, address: '' })).rejects.toThrow(
      /needs an address/i
    );
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('refuses a mailbox whose address is only whitespace before making a request', async () => {
    await expect(createMailbox(event, { ...base, address: '   ' })).rejects.toThrow(
      /needs an address/i
    );
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('updateMailbox', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('PUTs to the detail endpoint', async () => {
    apiRequest.mockResolvedValue({});
    await updateMailbox(event, 'm1', base);
    const [url, opts] = apiRequest.mock.calls[0];
    expect(url).toBe('/cases/mailboxes/m1/');
    expect(opts.method).toBe('PUT');
  });

  it('sends only what it is given, with no org/created_by', async () => {
    apiRequest.mockResolvedValue({});
    await updateMailbox(event, 'm1', {
      ...base,
      org: 'ATTACKER-ORG',
      created_by: 'ATTACKER'
    });
    const { body } = apiRequest.mock.calls[0][1];
    expect(Object.keys(body).sort()).toEqual([
      'address',
      'default_assignee_id',
      'default_case_type',
      'default_priority',
      'is_active',
      'provider'
    ]);
    expect(body.org).toBeUndefined();
    expect(body.created_by).toBeUndefined();
  });

  it('never sends a webhook_secret, even when handed one', async () => {
    apiRequest.mockResolvedValue({});
    await updateMailbox(event, 'm1', { ...base, webhook_secret: 'ATTACKER-CHOSEN' });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body.webhook_secret).toBeUndefined();
  });

  it('sends a changed topic_arn, and an empty one clears it', async () => {
    apiRequest.mockResolvedValue({});
    await updateMailbox(event, 'm1', { topic_arn: ARN });
    await updateMailbox(event, 'm1', { topic_arn: '' });
    expect(apiRequest.mock.calls[0][1].body).toEqual({ topic_arn: ARN });
    expect(apiRequest.mock.calls[1][1].body).toEqual({ topic_arn: '' });
  });

  it('leaves topic_arn out when the form did not change it', async () => {
    apiRequest.mockResolvedValue({});
    await updateMailbox(event, 'm1', { ...base, topic_arn: undefined });
    expect('topic_arn' in apiRequest.mock.calls[0][1].body).toBe(false);
  });

  it('sends exactly one key for a minimal { is_active: true } body (the turn-on path)', async () => {
    apiRequest.mockResolvedValue({});
    await updateMailbox(event, 'm1', { is_active: true });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body).toEqual({ is_active: true });
  });

  it('sends exactly one key for a minimal { is_active: false } body (the turn-off path)', async () => {
    apiRequest.mockResolvedValue({});
    await updateMailbox(event, 'm1', { is_active: false });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body).toEqual({ is_active: false });
  });

  it('does not require an address, since a turn-on/off submits none', async () => {
    apiRequest.mockResolvedValue({});
    await expect(updateMailbox(event, 'm1', { is_active: true })).resolves.toBeDefined();
  });

  it('trims and lowercases an address when one is submitted', async () => {
    apiRequest.mockResolvedValue({});
    await updateMailbox(event, 'm1', { address: '  New@Example.IO  ' });
    const { body } = apiRequest.mock.calls[0][1];
    expect(body.address).toBe('new@example.io');
  });

  it('throws when the id is missing', async () => {
    await expect(updateMailbox(event, '', base)).rejects.toThrow(/which mailbox/i);
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('deleteMailbox', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('DELETEs the detail endpoint', async () => {
    apiRequest.mockResolvedValue({});
    await deleteMailbox(event, 'm1');
    const [url, opts] = apiRequest.mock.calls[0];
    expect(url).toBe('/cases/mailboxes/m1/');
    expect(opts.method).toBe('DELETE');
  });

  it('throws when the id is missing', async () => {
    await expect(deleteMailbox(event, '')).rejects.toThrow(/which mailbox/i);
    expect(apiRequest).not.toHaveBeenCalled();
  });
});

describe('topicArnEdit', () => {
  it('is undefined when the field was not on the form', () => {
    expect(topicArnEdit(null, '')).toBeUndefined();
    expect(topicArnEdit(undefined, ARN)).toBeUndefined();
  });

  it('is undefined when the value is what the form was prefilled with', () => {
    // A stale form must not undo a pin the webhook set after the page loaded.
    expect(topicArnEdit('', '')).toBeUndefined();
    expect(topicArnEdit(` ${ARN} `, ARN)).toBeUndefined();
  });

  it('is the new value, trimmed, when the admin changed it', () => {
    expect(topicArnEdit(` ${ARN}`, '')).toBe(ARN);
    expect(topicArnEdit('arn:aws:sns:eu-west-1:123456789012:other', ARN)).toBe(
      'arn:aws:sns:eu-west-1:123456789012:other'
    );
  });

  it('is the empty string when the admin cleared it', () => {
    expect(topicArnEdit('  ', ARN)).toBe('');
  });
});

describe('getMailboxes', () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  const row = { id: 'm1', address: 'a@b.io', provider: 'ses', is_active: true };

  it('carries the ARN the backend sent an admin', async () => {
    apiRequest.mockResolvedValue({
      mailboxes: [{ ...row, has_topic_arn: true, topic_arn: ARN, webhook_secret: 'x' }]
    });
    const { mailboxes } = await getMailboxes(event);
    expect(mailboxes[0].topic_arn).toBe(ARN);
    expect(mailboxes[0].has_topic_arn).toBe(true);
    expect('webhook_secret' in mailboxes[0]).toBe(false);
  });

  it('has a null ARN for a member, who is sent only has_topic_arn', async () => {
    apiRequest.mockResolvedValue({ mailboxes: [{ ...row, has_topic_arn: true }] });
    const { mailboxes } = await getMailboxes(event);
    expect(mailboxes[0].topic_arn).toBeNull();
    expect(mailboxes[0].has_topic_arn).toBe(true);
  });
});
