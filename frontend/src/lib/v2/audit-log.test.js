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

  it('names both records of a merge', () => {
    expect(
      auditDetail({
        event_type: 'RECORD_MERGED',
        details: {
          entity: 'contact',
          kept_id: 'k1',
          kept_name: 'Liz Lopez',
          merged_id: 'm1',
          merged_name: 'Elizabeth Lopez'
        }
      })
    ).toBe('Merged contact "Elizabeth Lopez" into "Liz Lopez".');
  });

  it('tells two records with one name apart by their ids', () => {
    expect(
      auditDetail({
        event_type: 'RECORD_MERGED',
        details: {
          entity: 'contact',
          kept_id: '5e6f7a8b-0000-4000-8000-000000000000',
          kept_name: 'Rosalind Beck',
          merged_id: '1a2b3c4d-0000-4000-8000-000000000000',
          merged_name: 'Rosalind Beck'
        }
      })
    ).toBe('Merged contact "Rosalind Beck" (1a2b3c4d) into "Rosalind Beck" (5e6f7a8b).');
  });

  it('names a created token and says an unscoped one has full access', () => {
    expect(
      auditDetail({
        event_type: 'API_TOKEN_CREATED',
        actor: { id: 'u1' },
        details: {
          token_prefix: 'bcrm_pat_ab12',
          token_name: 'CI script',
          scopes: [],
          owner_id: 'u1',
          owner_name: 'Asha'
        }
      })
    ).toBe('Token "CI script" (bcrm_pat_ab12), full access.');
  });

  it('names the owner when an admin revoked a member token', () => {
    expect(
      auditDetail({
        event_type: 'API_TOKEN_REVOKED',
        actor: { id: 'admin' },
        details: {
          token_prefix: 'bcrm_pat_ab12',
          token_name: 'laptop',
          scopes: ['leads:read', 'contacts:read'],
          owner_id: 'u1',
          owner_name: 'Asha'
        }
      })
    ).toBe('Token "laptop" (bcrm_pat_ab12), scopes leads:read, contacts:read, owned by Asha.');
  });

  it('adds nothing for a calendar feed event, whose label says it all', () => {
    for (const event_type of [
      'CALENDAR_FEED_ENABLED',
      'CALENDAR_FEED_REGENERATED',
      'CALENDAR_FEED_DISABLED'
    ]) {
      expect(auditDetail({ event_type, details: {} })).toBe('');
    }
  });

  it('adds nothing for an event type it has never heard of', () => {
    expect(auditDetail({ event_type: 'SOMETHING_NEW', details: { whatever: 1 } })).toBe('');
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
