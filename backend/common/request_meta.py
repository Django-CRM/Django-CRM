"""Request metadata derived server-side.

Shared by every view that records a client IP (the security audit log, web form
submissions, estimate acceptance, magic-link and portal sign-in tokens) and by
the throttles that bucket on it, so all of them agree on what "the client"
means. One function: a second definition is how one of them ended up trusting a
header the caller writes.
"""

from django.core.exceptions import ValidationError
from django.core.validators import validate_ipv46_address
from rest_framework.settings import api_settings

REFERER_MAX_LENGTH = 512


def _valid_ip(candidate):
    """`candidate` if it is an IPv4 or IPv6 address, else None. Callers write
    the result into a GenericIPAddressField, and Django does not run field
    validators on save(), so junk would reach the `inet` column as a 500."""
    try:
        validate_ipv46_address(candidate)
    except ValidationError:
        return None
    return candidate


def client_ip(request):
    """The client IP as far as the proxies we run can vouch for it, or None.

    Each proxy appends the address it received the request from to
    `X-Forwarded-For`, so only the last `NUM_PROXIES` entries were written by
    infrastructure; anything to their left is whatever the caller sent, and
    trusting it let a caller pick their own throttle bucket and their own
    recorded address. With `REST_FRAMEWORK["NUM_PROXIES"] = n` the answer is
    the n-th entry from the right, the address the outermost proxy saw. Unset
    or 0 means no proxy is trusted: the header is ignored and the socket peer
    (`REMOTE_ADDR`) is the answer. DRF's own setting, so its `get_ident` and
    this cannot disagree.

    Behind a proxy with `NUM_PROXIES` unset, every visitor is the proxy, so
    the per-IP throttles become one shared bucket. Production sets it.
    """
    remote = request.META.get("REMOTE_ADDR", "")
    num_proxies = api_settings.NUM_PROXIES or 0
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if num_proxies <= 0 or not forwarded:
        return _valid_ip(remote)
    hops = [part.strip() for part in forwarded.split(",")]
    return _valid_ip(hops[-min(num_proxies, len(hops))])


def referer(request):
    """The Referer header, truncated to the column width.

    Returns an empty string rather than None: the model field is
    `blank=True, default=""`, and None would be a second spelling of absent.
    """
    return request.META.get("HTTP_REFERER", "")[:REFERER_MAX_LENGTH]
