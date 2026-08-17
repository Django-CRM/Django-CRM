"""Request metadata derived server-side.

Shared by the views that record it and the throttles that bucket on it, so both
agree on what "the client" means. A throttle keyed on one definition of the
client and a stored value using another is a bug that only shows up during an
incident.

`common/audit_log.py` carries an unvalidated inline version of the same idea.
It is deliberately left alone: changing it is outside the scope of the web
forms work and would need its own tests.
"""

from django.core.exceptions import ValidationError
from django.core.validators import validate_ipv46_address

REFERER_MAX_LENGTH = 512


def client_ip(request):
    """Best-effort originating client IP, or None when nothing validates.

    A reverse proxy makes the socket peer the proxy rather than the visitor, so
    every visitor would otherwise collapse into a single throttle bucket.
    `X-Forwarded-For` carries the original client as its first entry.

    That header is submitter-controlled for anything that can reach the
    endpoint directly, so the result is informational: it is stored for triage
    and used to bucket the per-IP throttle, and it is never an input to an
    authorization decision. The global throttle is the control a forged header
    cannot evade.

    Every candidate is validated before it is returned, because the caller
    writes this into a GenericIPAddressField and Django does not run field
    validators on save().
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    candidates = [part.strip() for part in forwarded.split(",") if part.strip()]
    candidates.append(request.META.get("REMOTE_ADDR", ""))

    for candidate in candidates:
        if not candidate:
            continue
        try:
            validate_ipv46_address(candidate)
        except ValidationError:
            continue
        return candidate
    return None


def referer(request):
    """The Referer header, truncated to the column width.

    Returns an empty string rather than None: the model field is
    `blank=True, default=""`, and None would be a second spelling of absent.
    """
    return request.META.get("HTTP_REFERER", "")[:REFERER_MAX_LENGTH]
