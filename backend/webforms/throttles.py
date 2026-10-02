"""Rate limits for the public web form submit endpoint.

Two layers, because neither is sufficient alone:

- `WebFormIPThrottle` buckets on `common.request_meta.client_ip`, which
  believes only the `X-Forwarded-For` entries our own proxies appended, so
  rotating the header does not buy a fresh bucket. It stops one visitor or one
  bot from flooding.
- `WebFormGlobalThrottle` caps submissions per form across all clients, the
  backstop for a sender spread across many real addresses. The view checks
  them in that order and stops at the first refusal
  (`common.throttles.FirstRefusalThrottleMixin`), so a client the per-IP limit
  refused cannot fill the form's daily cap alone.

Both need a shared cache to mean anything across workers. `crm/settings.py`
configures one from `CACHE_URL` and says what happens when it is not set.
"""

from rest_framework.throttling import SimpleRateThrottle

from common.request_meta import client_ip


class WebFormIPThrottle(SimpleRateThrottle):
    scope = "webform_submit_ip"

    def get_cache_key(self, request, view):
        # Bucketed per form as well as per IP, so a visitor who submits one
        # org's form is not locked out of a different org's form.
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"{view.kwargs.get('form_id')}:{client_ip(request) or 'unknown'}",
        }


class WebFormGlobalThrottle(SimpleRateThrottle):
    scope = "webform_submit_global"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": str(view.kwargs.get("form_id")),
        }
