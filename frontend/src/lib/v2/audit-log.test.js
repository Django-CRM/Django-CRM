import { describe, it, expect } from 'vitest';
import { auditActor, auditDetail, auditWebhookId } from '$lib/v2/audit-log.js';

describe('auditActor', () => {
  it('prefers the name, then the email', () => {
    expect(auditActor({ actor: { name: 'Asha', email: 'a@x.com' } })).toBe('Asha');
    expect(auditActor({ actor: { name: '', email: 'a@x.com' } })).toBe('a@x.com');
  });

  it('says so when there is no person', () => {
    expect(auditActor({ actor: null })).toBe('No user');
  });
});

describe('auditDetail', () => {
  it('shows the reason a webhook was paused', () => {
    const reason = 'Paused because the admin who created it was deactivated.';
    expect(auditDetail({ event_type: 'WEBHOOK_PAUSED', details: { pause_reason: reason } })).toBe(
      reason
    );
  });

  it('describes a re-enable', () => {
    expect(auditDetail({ event_type: 'WEBHOOK_REENABLED', details: {} })).toMatch(/answers for/);
  });

  it('names what a take-over changed', () => {
    expect(
      auditDetail({ event_type: 'WEBHOOK_CHANGED', details: { changed: ['url', 'secret'] } })
    ).toBe('Changed url, secret, and now answers for the webhook.');
  });

  it('describes a refused action and a sample-data clear', () => {
    expect(
      auditDetail({
        event_type: 'PERMISSION_DENIED',
        details: { action: 'ORG_SWITCH', resource: 'org:1' }
      })
    ).toBe('ORG_SWITCH on org:1');
    expect(auditDetail({ event_type: 'SAMPLE_DATA_CLEARED', details: { deleted_count: 0 } })).toBe(
      '0 sample leads removed'
    );
  });

  it('is empty when there is nothing to add', () => {
    expect(auditDetail({ event_type: 'LOGIN_SUCCESS', details: {} })).toBe('');
  });
});

describe('auditWebhookId', () => {
  it('reads the endpoint id, and nothing else', () => {
    expect(auditWebhookId({ details: { endpoint_id: 'e1' } })).toBe('e1');
    expect(auditWebhookId({ details: {} })).toBeNull();
    expect(auditWebhookId({ details: { endpoint_id: 5 } })).toBeNull();
  });
});
