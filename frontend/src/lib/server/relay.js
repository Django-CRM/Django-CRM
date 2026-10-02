/**
 * Headers that tell the API which visitor this server is relaying for: their
 * address, and their browser's User-Agent (this server's own is its HTTP
 * client, `axios` or `node`, which is what every relayed sign-in recorded
 * before). The API reads `X-BottleCRM-User-Agent` only from a request that
 * also carries the secret (`common.request_meta.user_agent`), so it is sent
 * with the signed pair.
 *
 * Anonymous pages that this server fetches on a visitor's behalf (the help
 * center, estimate acceptance, sign-in and sign-out, token refresh and org
 * switch) reach the API from this server's address, so without these every
 * visitor shares one per-IP throttle bucket and one recorded address. The API
 * believes `X-BottleCRM-Client-IP` only alongside `X-BottleCRM-Relay-Secret`
 * matching its own `RELAY_SECRET` (`common.request_meta.client_ip`), so that
 * pair is sent only when `RELAY_SECRET` is set.
 *
 * `X-Forwarded-For` carries the same visitor address, never the incoming
 * header (the visitor writes that). It is compatibility with APIs before
 * django-crm 1.12.0, which read the first `X-Forwarded-For` entry, so this
 * server can deploy before the API does without collapsing every visitor into
 * one bucket. 1.12.0 ignores it unless `NUM_PROXIES` is set.
 *
 * `getClientAddress()` is the visitor only when adapter-node is told which
 * header nginx writes: run it with `ADDRESS_HEADER=X-Forwarded-For` and
 * `XFF_DEPTH=1`, bound to `HOST=127.0.0.1` so nothing can reach it except
 * through nginx. Without them it is nginx's own loopback address.
 */

import { env } from '$env/dynamic/private';

/**
 * @param {{ getClientAddress: () => string, request: Request }} event
 * @returns {Record<string, string>}
 */
export function relayHeaders(event) {
  let address;
  try {
    address = event.getClientAddress();
  } catch {
    return {};
  }
  if (!address) return {};
  const secret = env.RELAY_SECRET;
  return {
    'X-Forwarded-For': address,
    ...(secret
      ? {
          'X-BottleCRM-Relay-Secret': secret,
          'X-BottleCRM-Client-IP': address,
          'X-BottleCRM-User-Agent': event.request.headers.get('user-agent') ?? ''
        }
      : {})
  };
}
