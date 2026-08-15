import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.utils import timezone

from common import scopes
from common.models import Org, Profile
from common.org_time import activate_org_timezone

logger = logging.getLogger(__name__)


def _denied(reason):
    """A 403 shaped like the one RequireOrgContext returns, so clients see one form."""
    return JsonResponse({"detail": reason}, status=403)


class GetProfileAndOrg:
    """
    Middleware to extract and validate organization context from JWT tokens.

    SECURITY: This middleware validates org_id from the SIGNED JWT token,
    not from client-provided headers. This prevents org spoofing attacks.

    The org context is cryptographically verified as part of the JWT signature.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # `process_request` returns a response when a non-interactive credential
        # is refused (out-of-scope, or reaching for another credential). The
        # return value used to be discarded, so a refusal had nowhere to go and
        # every check had to live in a view. Short-circuiting here is what makes
        # the scope boundary something a new view cannot forget to opt into.
        denial = self.process_request(request)
        if denial is not None:
            return denial

        # The org's day, for the rest of this request. Everything downstream
        # asks the framework what "today" is (`timezone.localdate()`), so
        # activating here is what makes one org's midnight different from
        # another's without any view knowing about it.
        #
        # The deactivate is in a `finally` for the same reason the RLS reset is
        # (see `rls_context.SetOrgContext.__call__`): the active timezone is a
        # thread-local, worker threads are reused, and a request that raised
        # after activating would hand the next tenant its own day boundary.
        activate_org_timezone(getattr(request, "org", None))
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()

    def process_request(self, request):
        # Skip JWT validation for authentication endpoints that don't need org context
        auth_skip_paths = [
            "/api/auth/google/",
            "/api/auth/refresh-token/",
            "/api/auth/me/",
            "/api/auth/switch-org/",
            # Sign-out carries a refresh token, not an access token, and is
            # reached most often when the access token has already expired.
            "/api/auth/logout/",
            "/api/auth/magic-link/request/",
            "/api/auth/magic-link/verify/",
        ]
        if request.path in auth_skip_paths:
            return

        # Initialize request attributes
        request.profile = None
        request.org = None
        request.portal_contact = None

        # Customer portal credential. Resolved here rather than left to DRF for
        # the same reason the PAT branch below is: DRF authentication runs after
        # RequireOrgContext, which would 403 the request before the view is ever
        # reached.
        #
        # The scope check is the point of it. A portal token belongs to a
        # customer and must never authenticate anything outside /api/portal/.
        # Refusing it here means a view written later cannot accept one by
        # forgetting a permission class.
        handled, portal_denial = self._process_portal_auth(request)
        if portal_denial is not None:
            return portal_denial
        if handled:
            # A portal token resolved and set the org. Never hand it onward to
            # the PAT extractor or the JWT decoder.
            return None

        # Personal Access Token (script / agent). Resolve here so org context is
        # set before RequireOrgContext runs (DRF auth runs too late for that).
        # MUST come before the JWT branch so a PAT bearer is never handed to
        # the JWT decoder.
        raw_pat = self._extract_pat(request)
        if raw_pat:
            return self._process_pat_auth(request, raw_pat)

        # Try JWT token first (primary authentication)
        if request.headers.get("Authorization"):
            self._process_jwt_auth(request)
            return None

        # Fall back to API key authentication
        api_key = request.headers.get("Token")
        if api_key:
            return self._process_api_key_auth(request, api_key)

        return None

    def _process_portal_auth(self, request):
        """Handle a customer portal bearer token.

        Returns ``(handled, denial)``. ``handled`` is True when the credential
        was a portal token and the org was resolved from it, in which case the
        caller must stop: no other credential branch should see it. ``denial``
        is a 403 response when the token was a portal one used out of scope, or
        when its org is gone.

        Both outcomes are ``(False, None)`` when there is no bearer token, or
        when the bearer belongs to another realm. Saying "not mine" quietly is
        what lets this sit in front of the PAT and JWT branches.
        """
        from common.portal_auth import peek_portal_claims

        header = request.META.get("HTTP_AUTHORIZATION", "")
        parts = header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return False, None

        claims = peek_portal_claims(parts[1])
        if claims is None:
            return False, None

        # The scope boundary. A portal token is only ever valid under
        # /api/portal/, and refusing it here is what makes that something a
        # future view cannot opt out of by accident.
        if not request.path.startswith("/api/portal/"):
            return False, _denied("This credential is not valid for this endpoint.")

        org = Org.objects.filter(id=claims["org_id"], is_active=True).first()
        if org is None:
            return False, _denied("Organization context is required.")

        request.org = org
        return True, None

    def _extract_pat(self, request):
        """Return a bcrm_pat_-prefixed token from the request, else None.

        Reuses the same extractor as the DRF auth class so the detection logic
        (Authorization: Bearer … or the Token header) lives in one place.
        """
        from common.pat_auth import _extract_raw

        return _extract_raw(request)

    def _process_pat_auth(self, request, raw):
        """Resolve a PAT, enforce its scopes, and set org context.

        Returns a 403 response when the token's scopes do not cover this request
        or when it reaches for another credential; otherwise ``None``.

        On an invalid/revoked/expired PAT we leave request.org unset so that
        RequireOrgContext returns a clean 403 (the same denial the org-key path
        produces for an unknown key once that exception surfaces). We swallow
        AuthenticationFailed here rather than re-raising so the request is
        denied cleanly downstream instead of 500-ing inside middleware.
        """
        from rest_framework.exceptions import AuthenticationFailed

        from common.pat_auth import resolve_valid_pat

        try:
            pat = resolve_valid_pat(raw)
        except AuthenticationFailed:
            # Leave org unset → RequireOrgContext denies with 403. The DRF
            # PATAuthentication class will also raise on this token, but the
            # middleware-level denial happens first.
            return None

        # The scope check runs BEFORE org context is attached. A refused request
        # must not leave a resolved profile behind for anything downstream to
        # act on, and it must not set the RLS session variable for a call that
        # is not going to happen.
        denial = scopes.check_request(pat.scopes, request.method, request.path)
        if denial is not None:
            logger.warning(
                "PAT %s refused for %s %s",
                pat.token_prefix,
                request.method,
                request.path,
            )
            return _denied(denial)

        request.profile = pat.profile
        request.org = pat.org
        request.META["org"] = str(pat.org.id)
        request.META["pat_token_id"] = str(pat.id)
        request._pat = pat
        return None

    def _process_jwt_auth(self, request):
        """
        Process JWT authentication and extract org context from token.

        SECURITY: Org ID is extracted from the signed JWT payload, not headers.
        """
        from rest_framework_simplejwt.tokens import AccessToken

        try:
            auth_header = request.headers.get("Authorization")
            token_value = auth_header.split(" ")[1]

            # Validate and decode token
            access_token = AccessToken(token_value)
            user_id = access_token["user_id"]

            # Get org_id from JWT claim (SECURE - cryptographically signed)
            org_id = access_token.get("org_id")

            if not org_id:
                # Token doesn't have org context - this is allowed for some endpoints
                # like /api/auth/me/ or initial login
                logger.debug("JWT token for user %s has no org_id claim", user_id)
                return

            # Validate user membership in the org
            try:
                profile = Profile.objects.select_related("org").get(
                    user_id=user_id, org_id=org_id, is_active=True
                )
                request.profile = profile
                request.org = profile.org
                logger.debug(
                    "Set org context from JWT: user=%s, org=%s", user_id, org_id
                )

            except Profile.DoesNotExist as exc:
                # User doesn't have access to this org anymore
                # This can happen if membership was revoked after token was issued
                logger.warning(
                    "User %s no longer has access to org %s. Token may be stale.",
                    user_id,
                    org_id,
                )
                raise PermissionDenied(
                    "You no longer have access to this organization. "
                    "Please login again."
                ) from exc

        except (IndexError, KeyError) as e:
            logger.warning("Malformed Authorization header: %s", e)
            # Let DRF authentication handle this
            return

        except Exception as e:
            logger.warning("JWT validation failed: %s", e)
            # Let DRF authentication handle invalid tokens
            return

    def _process_api_key_auth(self, request, api_key):
        """Process organization API key authentication.

        This key is one per org, never expires, and resolves to an arbitrary
        active ADMIN. Until this gate existed it was therefore a full admin
        session for that tenant: it could delete records, change roles, mint a
        personal access token owned by the human whose identity it borrowed, and
        read and rotate itself. A key that leaks into a log or a CI variable is
        an unbounded, unrevokable admin.

        Two limits now apply, both before any view runs:

        * ``ORG_API_KEY_AUTH_ENABLED`` turns the whole path off. Default True,
          so nothing breaks on upgrade; a deployment that has migrated its
          integrations to personal access tokens can shut the door.
        * The key is read-only, and the credential deny-list from
          ``common.scopes`` applies to it, so it cannot reach for another
          credential. Both are expressed as ``ORG_API_KEY_SCOPES`` so the org
          key and a read-only token share one implementation.

        This narrows what the key can do. It does not make the key safe: it
        still reads every record in the org. Prefer a scoped personal access
        token; see docs/api/tokens-and-api-keys.md.
        """
        if not getattr(settings, "ORG_API_KEY_AUTH_ENABLED", True):
            logger.warning("Organization API key auth is disabled; refusing request")
            return _denied(
                "Organization API key authentication is disabled on this "
                "deployment. Use a personal access token."
            )

        try:
            organization = Org.objects.get(api_key=api_key, is_active=True)

            # Get an admin profile for this org
            profile = Profile.objects.filter(
                org=organization, role="ADMIN", is_active=True
            ).first()

            if not profile:
                logger.error(
                    "No active admin profile found for org %s", organization.id
                )
                # Let DRF authentication reject this, raising AuthenticationFailed
                # here escapes the middleware stack as a 500 instead of a 401.
                return None

            denial = scopes.check_request(
                scopes.ORG_API_KEY_SCOPES, request.method, request.path
            )
            if denial is not None:
                logger.warning(
                    "Org API key refused for %s %s (org %s)",
                    request.method,
                    request.path,
                    organization.id,
                )
                return _denied(denial)

            request.profile = profile
            request.org = organization
            request.META["org"] = str(organization.id)
            request.META["org_api_key_auth"] = True

            logger.debug("Set org context from API key: org=%s", organization.id)

        except Org.DoesNotExist:
            logger.warning("Invalid API key attempted")
            # Same as above: APIKeyAuthentication raises inside the DRF layer,
            # which turns it into a proper 401 response.
            return None

        return None
