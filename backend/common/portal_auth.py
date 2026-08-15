"""The customer portal credential realm.

Separate from the internal one on purpose, and the separation is enforced more
than once, because a boundary that depends on every future author remembering it
is not a boundary.

1. `PortalContactAuthentication` is never added to
   `DEFAULT_AUTHENTICATION_CLASSES`. That setting is global (see
   `crm/settings.py`), so anything listed there authenticates every view in the
   project. Portal views name this class explicitly instead.
2. The principal is not a Django `User` and its `is_authenticated` is `False`,
   so if a portal token ever does reach an internal view, DRF's own
   `IsAuthenticated` denies it by construction.
3. `GetProfileAndOrg.process_request` refuses a portal token on any path outside
   `/api/portal/`, before any view runs.

Traffic in the other direction needs no special handling. A portal token is
minted without a `user_id` claim, so SimpleJWT's `get_user` refuses it on its
own, and `peek_portal_claims` refuses an internal token here.
"""

from datetime import timedelta

from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from contacts.models import Contact

PORTAL_TYPE_CLAIM = "typ"
PORTAL_TOKEN_TYPE = "portal"
PORTAL_TOKEN_LIFETIME = timedelta(hours=24)


def mint_portal_token(contact) -> str:
    """A 24 hour access token for one contact in one org.

    There is no refresh token. When this expires the customer asks for another
    sign-in code, which keeps token rotation, refresh revocation and
    privilege-change invalidation out of the initial surface entirely. Those are
    the parts of an auth system that are hardest to get right and worst to get
    wrong, and a portal visited a few times per ticket does not need them.
    """
    token = AccessToken()
    token.set_exp(lifetime=PORTAL_TOKEN_LIFETIME)
    token[PORTAL_TYPE_CLAIM] = PORTAL_TOKEN_TYPE
    token["org_id"] = str(contact.org_id)
    token["contact_id"] = str(contact.id)
    return str(token)


def _bearer(request):
    """The raw bearer credential, or None. Shape only, nothing verified yet."""
    header = get_authorization_header(request).split()
    if len(header) != 2 or header[0].lower() != b"bearer":
        return None
    try:
        return header[1].decode()
    except UnicodeDecodeError:
        return None


def peek_portal_claims(raw):
    """Verified claims when `raw` is a portal token, otherwise None.

    Signature and expiry are checked here, so a caller acting on the result is
    acting on a token this server minted. Returns None rather than raising for a
    token from the other realm, because "not mine" is the ordinary case on the
    shared middleware path and is not an error there.
    """
    if not raw:
        return None
    try:
        token = AccessToken(raw)
    except (TokenError, KeyError, ValueError):
        return None
    if token.get(PORTAL_TYPE_CLAIM) != PORTAL_TOKEN_TYPE:
        return None
    org_id = token.get("org_id")
    contact_id = token.get("contact_id")
    if not org_id or not contact_id:
        return None
    return {
        PORTAL_TYPE_CLAIM: PORTAL_TOKEN_TYPE,
        "org_id": org_id,
        "contact_id": contact_id,
    }


class PortalPrincipal:
    """The authenticated customer.

    `is_authenticated` is False deliberately, and it is not an oversight. This
    object must never be a valid principal for the internal API, and DRF's
    `IsAuthenticated` is the backstop that guarantees it if every other layer
    has failed.

    `is_anonymous` is True for the matching reason, and it is load-bearing in a
    second place: `BaseModel.save` reads `crum.get_current_user()` and stamps
    `created_by` from it unless the user is None or anonymous
    (`common/base.py:104-109`). A customer is not a `User`, so there is nothing
    valid to put in that FK. Reporting anonymous makes every portal write take
    the "no request user" branch and leave the audit columns alone. Who filed
    the case is recorded by the `Case.contacts` link instead.

    Together the two flags say the accurate thing: authenticated for the portal,
    nobody at all as far as the rest of the application is concerned.
    """

    is_authenticated = False
    is_anonymous = True

    def __init__(self, contact):
        self.contact = contact

    def __str__(self):
        return f"PortalPrincipal({self.contact.id})"


class PortalContactAuthentication(BaseAuthentication):
    """Resolve a portal token to its Contact. Portal views only."""

    def authenticate(self, request):
        raw = _bearer(request)
        if raw is None:
            return None

        claims = peek_portal_claims(raw)
        if claims is None:
            raise AuthenticationFailed("Not a portal token.")

        contact = Contact.objects.filter(
            id=claims["contact_id"], org_id=claims["org_id"], is_active=True
        ).first()
        if contact is None:
            raise AuthenticationFailed("Portal access has been withdrawn.")

        request.portal_contact = contact
        return (PortalPrincipal(contact), claims)

    def authenticate_header(self, request):
        return "Bearer"


class IsPortalContact(BasePermission):
    """A live portal contact whose org matches the org resolved for the request."""

    message = "You do not have Permission to perform this action"

    def has_permission(self, request, view):
        contact = getattr(request, "portal_contact", None)
        org = getattr(request, "org", None)
        if contact is None or org is None:
            return False
        return bool(contact.is_active) and contact.org_id == org.id
