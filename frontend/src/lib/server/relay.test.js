import { afterEach, describe, expect, it } from 'vitest';

const { relayHeaders } = await import('$lib/server/relay.js');
const { env } = await import('$env/dynamic/private');

const SECRET = 'r'.repeat(48);

const BROWSER = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/142.0 Safari/537.36';

/**
 * @param {() => string} getClientAddress
 * @param {Record<string, string>} [headers] the visitor's own request headers
 */
const event = (getClientAddress, headers = { 'user-agent': BROWSER }) => ({
  getClientAddress,
  request: new Request('http://app.test/', { headers })
});

afterEach(() => {
  delete env.RELAY_SECRET;
});

describe('relayHeaders', () => {
  it('names the visitor and their browser, signed, when a secret is set', () => {
    env.RELAY_SECRET = SECRET;
    expect(relayHeaders(event(() => '198.51.100.7'))).toEqual({
      'X-Forwarded-For': '198.51.100.7',
      'X-BottleCRM-Relay-Secret': SECRET,
      'X-BottleCRM-Client-IP': '198.51.100.7',
      'X-BottleCRM-User-Agent': BROWSER
    });
  });

  it('names an empty browser when the visitor sent no User-Agent', () => {
    env.RELAY_SECRET = SECRET;
    expect(relayHeaders(event(() => '198.51.100.7', {}))).toMatchObject({
      'X-BottleCRM-User-Agent': ''
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
