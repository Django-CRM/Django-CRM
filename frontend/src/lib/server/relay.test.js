import { afterEach, describe, expect, it } from 'vitest';

const { relayHeaders } = await import('$lib/server/relay.js');
const { env } = await import('$env/dynamic/private');

const SECRET = 'r'.repeat(48);

/** @param {() => string} getClientAddress */
const event = (getClientAddress) => ({ getClientAddress });

afterEach(() => {
  delete env.RELAY_SECRET;
});

describe('relayHeaders', () => {
  it('names the visitor, signed, when a secret is set', () => {
    env.RELAY_SECRET = SECRET;
    expect(relayHeaders(event(() => '198.51.100.7'))).toEqual({
      'X-Forwarded-For': '198.51.100.7',
      'X-BottleCRM-Relay-Secret': SECRET,
      'X-BottleCRM-Client-IP': '198.51.100.7'
    });
  });

  it('sends only the pre-1.12 X-Forwarded-For when the secret is unset', () => {
    expect(relayHeaders(event(() => '198.51.100.7'))).toEqual({
      'X-Forwarded-For': '198.51.100.7'
    });
  });

  it('treats an empty secret as unset', () => {
    env.RELAY_SECRET = '';
    expect(relayHeaders(event(() => '198.51.100.7'))).toEqual({
      'X-Forwarded-For': '198.51.100.7'
    });
  });

  it('sends nothing when the address cannot be read', () => {
    env.RELAY_SECRET = SECRET;
    expect(
      relayHeaders(
        event(() => {
          throw new Error('no address');
        })
      )
    ).toEqual({});
  });

  it('sends nothing when the address is empty', () => {
    env.RELAY_SECRET = SECRET;
    expect(relayHeaders(event(() => ''))).toEqual({});
  });
});
