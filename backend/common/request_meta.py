"""Request metadata derived server-side.

Shared by every view that records a client IP (the security audit log, web form
submissions, estimate acceptance, magic-link and portal sign-in tokens) and by
the throttles that bucket on it, so all of them agree on what "the client"
means. One function: a second definition is how one of them ended up trusting a
header the caller writes.
"""

import hmac

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_ipv46_address
from rest_framework.settings import api_settings

REFERER_MAX_LENGTH = 512

# A SvelteKit server relaying an anonymous visitor names the visitor in
# RELAY_CLIENT_IP_HEADER and RELAY_USER_AGENT_HEADER and proves it is ours with
# RELAY_SECRET_HEADER. These are the WSGI META keys for
# `X-BottleCRM-Relay-Secret`, `X-BottleCRM-Client-IP` and
# `X-BottleCRM-User-Agent`.
RELAY_SECRET_HEADER = "HTTP_X_BOTTLECRM_RELAY_SECRET"
RELAY_CLIENT_IP_HEADER = "HTTP_X_BOTTLECRM_CLIENT_IP"
RELAY_USER_AGENT_HEADER = "HTTP_X_BOTTLECRM_USER_AGENT"
# The same floor `crm/settings.py` refuses to start below. Checked here too so
# an empty or short value never matches, whatever set it.
RELAY_SECRET_MIN_LENGTH = 32


def _valid_ip(candidate):
    """`candidate` if it is an IPv4 or IPv6 address, else None. Callers write
    the result into a GenericIPAddressField, and Django does not run field
    validators on save(), so junk would reach the `inet` column as a 500."""
    try:
        validate_ipv46_address(candidate)
    except ValidationError:
        return None
    return candidate


def _from_trusted_relay(request):
    """Whether the request carries `settings.RELAY_SECRET`, compared in
    constant time. Without it no visitor header is ever read, so a caller
    cannot choose what is recorded about them by sending one."""
    secret = getattr(settings, "RELAY_SECRET", "") or ""
    if len(secret) < RELAY_SECRET_MIN_LENGTH:
        return False
    sent = request.META.get(RELAY_SECRET_HEADER, "")
    return hmac.compare_digest(sent.encode(), secret.encode())


def _relayed_ip(request):
    """The visitor a trusted relay names, or None.

    A named address that is not an IP is None too, and the caller falls back
    to the socket rule.
    """
    if not _from_trusted_relay(request):
        return None
    return _valid_ip(request.META.get(RELAY_CLIENT_IP_HEADER, "").strip())


def user_agent(request):
    """The visitor's User-Agent, untruncated; callers cut it to their column.

    A request relayed by our SvelteKit servers carries the server's HTTP
    client (`axios/1.20.0`) as its own User-Agent, so a trusted relay's
    `X-BottleCRM-User-Agent` is the visitor's, even when empty. A trusted
    relay that sends no such header (one deployed before it existed) and
    every other caller get their own. Unlike the address this is never more
    than the visitor's own claim, so it decides nothing; the secret only
    keeps the recorded value consistent with the recorded address.
    """
    if _from_trusted_relay(request) and RELAY_USER_AGENT_HEADER in request.META:
        return request.META[RELAY_USER_AGENT_HEADER]
    return request.META.get("HTTP_USER_AGENT", "")


def client_ip(request):
    """The client IP as far as the proxies we run can vouch for it, or None.

    A relay that proves itself with the shared secret is believed about the
    visitor it names (`_relayed_ip`). Without the SvelteKit servers doing
    that, every visitor they relay (help center pages, estimate acceptance,
    the marketing contact form) arrives from the relay's own address and
    shares its per-IP throttle bucket.

    Otherwise the proxies decide. Each proxy appends the address it received the request from to
    `X-Forwarded-For`, so only the last `NUM_PROXIES` entries were written by
    infrastructure; anything to their left is whatever the caller sent, and
    trusting it let a caller pick their own throttle bucket and their own
    recorded address. With `REST_FRAMEWORK["NUM_PROXIES"] = n` the answer is
    the n-th entry from the right, the address the outermost proxy saw. Unset
    or 0 means no proxy is trusted: the header is ignored and the socket peer
    (`REMOTE_ADDR`) is the answer. DRF's own setting, so its `get_ident` and
    this cannot disagree.

    Hosted production leaves `NUM_PROXIES` unset: uvicorn runs with
    `--proxy-headers --forwarded-allow-ips=127.0.0.1`, so `REMOTE_ADDR` already
    is the address nginx saw, and setting it on top of that counts nginx
    twice. Set it only when `REMOTE_ADDR` is the proxy itself (gunicorn behind
    nginx); unset there, every visitor shares the proxy's per-IP throttle
    bucket.
    """
    relayed = _relayed_ip(request)
    if relayed:
        return relayed
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
