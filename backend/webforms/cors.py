"""Per-form cross-origin permission for the public submit route.

Uses django-cors-headers' `check_request_enabled` signal rather than a bespoke
middleware, so there is one CORS implementation in the project and the global
`CORS_ALLOWED_ORIGINS` and `CORS_ORIGIN_ALLOW_ALL` settings stay untouched.

Three deliberate narrowings, each with a test:

* Only the `/submit/` route. The embed routes are plain GETs of a document and
  a script, neither of which CORS applies to, so widening to them would grant
  something for nothing.
* Exact origin match. No subdomain wildcards and no scheme coercion: an origin
  is a (scheme, host, port) triple and treating it as anything looser hands the
  form to whoever controls a neighbouring name.
* An empty `allowed_origins` returns False rather than True. The VIEW treats an
  empty list as "any origin may POST", which is a reasonable default for the
  iframe embed and for server-side callers. Reflecting an arbitrary `Origin`
  header back as CORS-allowed is a much larger promise on a production API, and
  an org that wants the script embed can list its origins.
"""

import logging
import re

from webforms.models import WebForm

logger = logging.getLogger(__name__)

# Anchored at both ends on purpose. A route added later under a longer path
# must not inherit this permission by accident.
SUBMIT_PATH = re.compile(
    r"^/api/public/forms/"
    r"(?P<org_id>[0-9a-fA-F-]{36})/"
    r"(?P<form_id>[0-9a-fA-F-]{36})/submit/$"
)


def allow_webform_origin(sender, request, **kwargs):
    """Whether django-cors-headers should add CORS headers to this response."""
    match = SUBMIT_PATH.match(request.path)
    if match is None:
        return False

    origin = request.META.get("HTTP_ORIGIN", "")
    if not origin:
        return False

    # Imported here rather than at module scope to keep this module importable
    # from AppConfig.ready() without pulling the ORM in too early.
    from common.tasks import set_rls_context

    org_id = match.group("org_id")
    set_rls_context(org_id)

    try:
        allowed = (
            WebForm.objects.filter(
                id=match.group("form_id"), org_id=org_id, is_published=True
            )
            .values_list("allowed_origins", flat=True)
            .first()
        )
    except Exception:
        # A malformed id reaches the database as a bad UUID and raises. Refusing
        # is the safe answer: this decides whether to relax a cross-origin
        # protection, so an error must never read as permission.
        logger.warning("CORS lookup failed for %s", request.path, exc_info=True)
        return False

    return bool(allowed) and origin in allowed
