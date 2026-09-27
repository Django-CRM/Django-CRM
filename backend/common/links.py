"""Absolute links into the web app, for emails, and to this API's public routes.

Every link that leaves this system in an email points at the SvelteKit frontend,
not at the API: ``/portal/invoice/<token>`` and ``/portal/estimate/<token>`` for
customers, ``/csat/<token>`` for a survey, ``/invoices/<id>`` for a colleague.
None of those paths exist on the Django host.

They were built four different wrong ways, each independently broken:

* ``f"{protocol}://{domain}/portal/invoice/{token}"`` where every caller passed
  ``domain=settings.DOMAIN_NAME``, which already carries a scheme. The emitted
  link was ``http://http://localhost:8000/portal/invoice/…``: two schemes, dead
  on arrival even when ``DOMAIN_NAME`` was set correctly for a real deployment.
* ``DOMAIN_NAME`` names the **backend**. Even with the double scheme removed,
  the link pointed at the API host, where ``/portal/...`` 404s.
* ``send_estimate_to_client`` is dispatched with no ``domain`` at all, so it
  fell back to the ``"localhost"`` default and emitted ``http://localhost/…``.
* ``backend/.env`` sets ``DOMAIN_NAME=""``, so the CSAT builder's
  ``f"{domain}/csat/{token}"`` produced the relative ``"/csat/<token>"``, which
  is not a link at all inside an email body.

``FRONTEND_URL`` already existed and already meant exactly this: it is what the
welcome email and the magic-link sign-in URL are built from. Routing the rest
through it makes one setting answer one question, and gives an operator a single
value to get right.
"""

from django.conf import settings


def frontend_url(path):
    """Return an absolute URL to ``path`` on the web app.

    ``path`` is a root-relative path such as ``/portal/invoice/<token>``. The
    leading slash is optional and the base's trailing slash is stripped, so an
    operator who sets ``FRONTEND_URL=https://app.example.com/`` gets the same
    result as one who omits it.
    """
    base = (getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
    suffix = path if path.startswith("/") else f"/{path}"
    return f"{base}{suffix}"


def api_url(path):
    """Return an absolute URL to ``path`` on this API's public origin.

    For URLs a person copies out of the app and uses elsewhere: the calendar
    feed a calendar app subscribes to, and a web form's embed snippet pasted
    onto a customer's site. They cannot come from the incoming request. The web
    app asks for them from its own server, through ``PUBLIC_DJANGO_API_URL``,
    which in Docker is ``http://backend:8000``, a host nobody outside can
    reach; and behind a TLS terminator the request reads as ``http://`` unless
    the proxy header is trusted. ``DOMAIN_NAME`` names the API's public origin
    and is checked at startup outside dev (``crm/settings.py``).
    """
    base = (getattr(settings, "DOMAIN_NAME", "") or "").rstrip("/")
    suffix = path if path.startswith("/") else f"/{path}"
    return f"{base}{suffix}"
