"""Rate limits for the anonymous sign-in request endpoints, and the mixin every
view pairing a per-client throttle with a shared one uses.

`MagicLinkRequestView` and `PortalLoginRequestView` each mint a token and send
an email for a caller nobody has authenticated. Their own per-recipient check
(5 an hour per email or contact) caps what one inbox receives but not what one
caller sends: a fresh address is a fresh bucket. These layers sit in front of
it:

- Per client, bucketed on `common.request_meta.client_ip`. Both web pages call
  the API from the SvelteKit server, so every web visitor shares that server's
  bucket unless it sends their address signed with `RELAY_SECRET`. The default
  rates leave room for that, and `.env.example` says so.
- `MagicLinkGlobalThrottle`, across every caller. The magic link mails any
  address (sign-in also registers), so this is the backstop for a sender spread
  over many addresses.
- `PortalLoginOrgThrottle`, per org. The portal only mails an existing contact
  of that org, so the per-org cap is the useful backstop there.

All of them need the shared cache `crm/settings.py` builds from `CACHE_URL`.
"""

from rest_framework.throttling import SimpleRateThrottle

from common.request_meta import client_ip


class FirstRefusalThrottleMixin:
    """Stop at the first throttle that refuses, and list the per-client one first.

    DRF's `check_throttles` consults every throttle even after one refuses, and
    a `SimpleRateThrottle` records each request it allows. So a request the
    per-client limit refused still counted toward the shared one, and a single
    address could fill the shared bucket alone and lock every other visitor
    out of signing in.
    """

    def check_throttles(self, request):
        for throttle in self.get_throttles():
            if not throttle.allow_request(request, self):
                self.throttled(request, throttle.wait())


class _ClientIPThrottle(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": client_ip(request) or "unknown",
        }


class MagicLinkIPThrottle(_ClientIPThrottle):
    scope = "magic_link_ip"


class MagicLinkGlobalThrottle(SimpleRateThrottle):
    scope = "magic_link_global"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": "all"}


class PortalLoginIPThrottle(_ClientIPThrottle):
    """Not bucketed per org, unlike the web form limit: an org id is not secret
    (it is in every portal email), so a per-org IP bucket would let one caller
    multiply their allowance by the number of org ids they hold."""

    scope = "portal_login_ip"


class PortalLoginOrgThrottle(SimpleRateThrottle):
    scope = "portal_login_org"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": str(view.kwargs.get("org_id")),
        }
