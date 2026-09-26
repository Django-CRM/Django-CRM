import json
import logging
import secrets
import uuid
from datetime import timedelta

import requests
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from common import serializer
from common.models import Org, Profile, User
from common.permissions import is_org_admin
from common.request_meta import client_ip
from common.serializer import OrgAwareRefreshToken
from common.utils import CURRENCY_SYMBOLS

logger = logging.getLogger(__name__)


def _org_payload(org, profile=None):
    """The org record every auth response hands a client.

    Currency belongs here because a client picks it up at sign-in and holds it
    for the whole session: the mobile deal and lead forms default a new
    record's currency from it, and the dashboard prices its totals with it.
    All three read fields that no endpoint was sending, so an org keeping its
    books in euros still created dollar deals and drew a dollar dashboard.

    ``default_currency`` and ``timezone`` are normalised rather than passed
    through, so a blank column answers with the same "USD" and "UTC" the rest of
    the codebase assumes.

    With ``profile`` (the caller's membership of ``org``) it also carries
    ``role`` and ``is_organization_admin``. The mobile client gates admin UI on
    the latter, which is ``is_org_admin`` and so admits a superuser's profile;
    ``role`` is for display.
    """
    currency = org.default_currency or "USD"
    payload = {
        "id": str(org.id),
        "name": org.name,
        "default_currency": currency,
        "currency_symbol": CURRENCY_SYMBOLS.get(currency, "$"),
        "default_country": org.default_country,
        # The org's day, so a client can label a date the same way the server
        # computed it instead of guessing from the device.
        "timezone": org.timezone or "UTC",
    }
    if profile is not None:
        payload["role"] = profile.role
        payload["is_organization_admin"] = is_org_admin(profile)
    return payload


def _google_email_is_verified(claims):
    """Whether Google asserts the caller controls the email in ``claims``.

    Fails closed: a missing ``email_verified`` claim counts as unverified.
    Google always sends it alongside the ``email`` scope, so its absence means
    the payload is not the shape we think it is. Without this check, a Google
    account carrying an address whose owner never proved control of it is enough
    to become that address here, which matters because other parts of the
    system key off the email string.
    """
    verified = claims.get("email_verified")
    return verified is True or str(verified).strip().lower() == "true"


def _disabled_account_response():
    """403 for a login attempt by a deactivated account.

    Deactivation is the offboarding lever, so every login path must honour it,
    otherwise a revoked user simply signs in again. Kept identical across the
    Google and magic-link flows so clients can handle one shape.
    """
    return Response(
        {"error": "User account is disabled"},
        status=status.HTTP_403_FORBIDDEN,
    )


class GoogleOAuthCallbackView(APIView):
    """
    Handle Google OAuth authorization code exchange with PKCE.
    """

    permission_classes = []
    authentication_classes = []

    @extend_schema(
        tags=["auth"],
        request=inline_serializer(
            name="GoogleOAuthRequest",
            fields={
                "code": serializers.CharField(),
                "code_verifier": serializers.CharField(),
                "redirect_uri": serializers.CharField(),
            },
        ),
        responses={
            200: inline_serializer(
                name="GoogleOAuthResponse",
                fields={
                    "access_token": serializers.CharField(),
                    "refresh_token": serializers.CharField(),
                    "user": serializers.DictField(),
                },
            )
        },
    )
    def post(self, request):
        import base64

        from django.utils import timezone

        code = request.data.get("code")
        code_verifier = request.data.get("code_verifier")
        redirect_uri = request.data.get("redirect_uri")

        if not all([code, code_verifier, redirect_uri]):
            return Response(
                {"error": "Missing required parameters"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Exchange code for tokens with Google
        try:
            token_response = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": code_verifier,
                },
                timeout=30,
            )
        except requests.RequestException:
            return Response(
                {"error": "Failed to communicate with Google"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        if token_response.status_code != 200:
            error_data = token_response.json() if token_response.content else {}
            return Response(
                {"error": error_data.get("error_description", "Token exchange failed")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token_data = token_response.json()
        id_token = token_data.get("id_token")
        if not id_token:
            return Response(
                {"error": "No ID token received"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Decode ID token payload (no verification needed - we just got it from Google over HTTPS)
        try:
            payload_part = id_token.split(".")[1]
            # Add padding if needed
            payload_part += "=" * (4 - len(payload_part) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_part))
            email = payload.get("email")
            picture = payload.get("picture", "")
            google_name = (payload.get("name") or "").strip()[:255]
        except (IndexError, ValueError, json.JSONDecodeError):
            return Response(
                {"error": "Invalid ID token format"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not email:
            return Response(
                {"error": "No email in token"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Reject before touching the database so an unverified address cannot
        # even provision an account.
        if not _google_email_is_verified(payload):
            return Response(
                {"error": "Google account email is not verified"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get or create user
        created = False
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            user = User.objects.create(
                email=email,
                name=google_name,
                profile_pic=picture,
                password=make_password(secrets.token_urlsafe(32)),
            )
            created = True

        if not user.is_active:
            return _disabled_account_response()

        # Backfill name from Google when the user hasn't set one yet.
        if not user.name and google_name:
            user.name = google_name
            user.save(update_fields=["name"])

        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])

        if created:
            from common.tasks import send_welcome_email

            send_welcome_email.delay(str(user.id))

        # Generate JWT tokens (with user info embedded)
        token = OrgAwareRefreshToken.for_user_and_org(user, None)

        # The same row a magic-link sign-in writes. No org yet: this token is
        # org-less until the user picks one.
        from common.audit_log import audit_log

        audit_log.login_success(user, None, request)

        return Response(
            {
                "access_token": str(token.access_token),
                "refresh_token": str(token),
                "user": {"id": str(user.id), "email": user.email},
            }
        )


class GoogleIdTokenView(APIView):
    """
    Handle Google Sign-In from mobile apps using ID token.
    Mobile app sends Google ID token, backend verifies and returns JWT.
    """

    permission_classes = []
    authentication_classes = []

    @extend_schema(
        tags=["auth"],
        request=inline_serializer(
            name="GoogleIdTokenRequest",
            fields={"idToken": serializers.CharField()},
        ),
        responses={
            200: inline_serializer(
                name="GoogleIdTokenResponse",
                fields={
                    "JWTtoken": serializers.CharField(),
                    "refresh_token": serializers.CharField(),
                    "user": serializers.DictField(),
                    "organizations": serializers.ListField(),
                },
            )
        },
    )
    def post(self, request):
        from django.utils import timezone
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        id_token_str = request.data.get("idToken")
        if not id_token_str:
            return Response(
                {"error": "Missing idToken"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Verify the ID token with Google
        try:
            idinfo = id_token.verify_oauth2_token(
                id_token_str,
                google_requests.Request(),
                settings.GOOGLE_CLIENT_ID,
            )
            email = idinfo.get("email")
            picture = idinfo.get("picture", "")
            google_name = (idinfo.get("name") or "").strip()[:255]
        except ValueError:
            logger.warning("Google OAuth token validation failed", exc_info=True)
            return Response(
                {"error": "Invalid token"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not email:
            return Response(
                {"error": "No email in token"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Reject before touching the database so an unverified address cannot
        # even provision an account.
        if not _google_email_is_verified(idinfo):
            return Response(
                {"error": "Google account email is not verified"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get or create user
        user, _created = User.objects.get_or_create(
            email=email,
            defaults={
                "name": google_name,
                "profile_pic": picture,
                "password": make_password(secrets.token_urlsafe(32)),
            },
        )

        if not user.is_active:
            return _disabled_account_response()

        # Backfill name from Google for existing users who don't have one.
        if not user.name and google_name:
            user.name = google_name
            user.save(update_fields=["name"])

        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])

        # Get user's organizations. Active profiles only: `OrgSwitchView`
        # requires `is_active=True`, so listing a deactivated membership here
        # offered an org that answers 403 the moment it is chosen.
        profiles = Profile.objects.filter(user=user, is_active=True).select_related(
            "org", "user"
        )
        organizations = [_org_payload(p.org, profile=p) for p in profiles]

        # Generate JWT token
        token = OrgAwareRefreshToken.for_user_and_org(user, None)

        # The same row a magic-link sign-in writes; org-less, as above.
        from common.audit_log import audit_log

        audit_log.login_success(user, None, request)

        return Response(
            {
                "JWTtoken": str(token.access_token),
                "refresh_token": str(token),
                "user": {
                    "id": str(user.id),
                    "email": user.email,
                    "name": email.split("@")[0],
                    "profileImage": user.profile_pic,
                },
                "organizations": organizations,
            }
        )


class MeView(APIView):
    """
    Get current authenticated user details
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get current authenticated user with organizations",
        responses={200: serializer.UserDetailSerializer},
    )
    def get(self, request):
        user_serializer = serializer.UserDetailSerializer(request.user)
        return Response(user_serializer.data, status=status.HTTP_200_OK)


class OrgAwareTokenRefreshView(APIView):
    """
    Custom token refresh that validates org membership.

    When refreshing a token, this view:
    1. Validates the refresh token
    2. Checks that user still has access to the org in the token
    3. Issues new tokens with the same org context

    If membership was revoked, returns 403 and user must login again.
    """

    permission_classes = []
    authentication_classes = []

    @extend_schema(
        description="Refresh access token with org membership validation",
        request=inline_serializer(
            name="OrgAwareTokenRefreshRequest",
            fields={"refresh": serializers.CharField(help_text="Refresh token")},
        ),
        responses={
            200: inline_serializer(
                name="OrgAwareTokenRefreshResponse",
                fields={
                    "access": serializers.CharField(),
                    "refresh": serializers.CharField(),
                },
            )
        },
    )
    def post(self, request):
        from django.db import transaction
        from rest_framework_simplejwt.exceptions import TokenError
        from rest_framework_simplejwt.tokens import RefreshToken as BaseRefreshToken

        from common.audit_log import audit_log

        refresh_token = request.data.get("refresh")

        if not refresh_token:
            return Response(
                {"error": "Refresh token is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Decode and validate refresh token
            token = BaseRefreshToken(refresh_token)
            user_id = token["user_id"]
            org_id = token.get("org_id")

            # Get user
            user = User.objects.get(id=user_id)

            if not user.is_active:
                return Response(
                    {"error": "User account is disabled"},
                    status=status.HTTP_403_FORBIDDEN,
                )

            # If token has org context, validate membership
            org = None
            profile = None
            if org_id:
                try:
                    profile = Profile.objects.get(
                        user=user, org_id=org_id, is_active=True
                    )
                    org = profile.org
                except Profile.DoesNotExist:
                    # Membership revoked - user must login again
                    audit_log.token_revoked(
                        user, None, f"Membership revoked for org {org_id}", request
                    )
                    return Response(
                        {
                            "error": "Organization membership revoked. Please login again."
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )

            # Rotate: a refresh token is single-use. Blacklisting the presented
            # token means a copy stolen via XSS, a proxy, or a log cannot keep
            # minting access tokens once the legitimate client has refreshed.
            # Both writes share one transaction so a failure can never leave the
            # caller tokenless while the old token stays usable.
            # `profile` is None when there is no org context, which is exactly
            # what for_user_and_org expects.
            with transaction.atomic():
                token.blacklist()
                new_token = OrgAwareRefreshToken.for_user_and_org(user, org, profile)

            audit_log.token_refresh(user, org, request)

            return Response(
                {"access": str(new_token.access_token), "refresh": str(new_token)},
                status=status.HTTP_200_OK,
            )

        except TokenError:
            return Response(
                {"error": "Invalid or expired token"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        except User.DoesNotExist:
            return Response(
                {"error": "User not found"}, status=status.HTTP_401_UNAUTHORIZED
            )


class LogoutView(APIView):
    """
    Sign out by blacklisting the presented refresh token.

    Until this existed, signing out only cleared the client's own storage. The
    refresh token stayed valid for its full fourteen days, so a copy lifted
    from a shared machine, a device backup, or a proxy log kept minting access
    tokens long after the user believed the session was over. Rotation and
    blacklisting were already switched on; the endpoint to use them was the
    piece that was missing.

    UNAUTHENTICATED BY DESIGN, and this is the part worth reading twice. The
    access token is often already expired when someone presses Sign Out: a
    phone that sat in a pocket, a tab left open overnight. Requiring it would
    fail exactly when signing out matters most, and would leave the refresh
    token alive. The refresh token is itself the credential here, presenting
    one proves possession, and the only thing this view will do with it is
    destroy it. A caller who does not hold a valid token cannot revoke anyone
    else's, because a token that is unparseable, expired, or already
    blacklisted never reaches `blacklist()`.

    WHAT IT DELIBERATELY DOES NOT DO. Access tokens are stateless and stay
    valid until they expire, up to an hour. Killing those needs a denylist
    consulted on every request, which is a different trade in a different
    change. This narrows the exposure from fourteen days to at most one hour.

    It also revokes one token, not every token the user holds, so signing out
    on a phone leaves a desktop signed in. "Sign out everywhere" is a separate
    feature and should stay one.
    """

    permission_classes = []
    authentication_classes = []

    @extend_schema(
        description=(
            "Sign out. Blacklists the supplied refresh token so it can no "
            "longer mint access tokens."
        ),
        request=inline_serializer(
            name="LogoutRequest",
            fields={"refresh": serializers.CharField(help_text="Refresh token")},
        ),
        responses={
            200: inline_serializer(
                name="LogoutResponse",
                fields={"detail": serializers.CharField()},
            )
        },
    )
    def post(self, request):
        from rest_framework_simplejwt.exceptions import TokenError
        from rest_framework_simplejwt.tokens import RefreshToken as BaseRefreshToken

        from common.audit_log import audit_log

        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response(
                {"error": "Refresh token is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            token = BaseRefreshToken(refresh_token)
        except TokenError:
            # Expired, malformed, or already blacklisted. The end state the
            # caller asked for is already true, so answering 200 keeps a client
            # from getting stuck on a sign-out that in fact succeeded. Nothing
            # is leaked either way: both cases mean "this token is unusable".
            return Response({"detail": "Signed out."}, status=status.HTTP_200_OK)

        token.blacklist()

        # Best effort, and only for the log. A user row that has since been
        # deleted must not turn a successful sign-out into a 500.
        user = User.objects.filter(id=token.get("user_id")).first()
        if user is not None:
            org = Org.objects.filter(id=token.get("org_id")).first()
            audit_log.logout(user, org, request)

        return Response({"detail": "Signed out."}, status=status.HTTP_200_OK)


class OrgSwitchView(APIView):
    """
    Switch to a different organization and get new JWT tokens.

    This endpoint validates that the user has access to the target
    organization and issues new tokens with the org_id claim.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def _retire_presented_refresh_token(self, request):
        """Blacklist the refresh token the caller is switching away from.

        Optional by design: clients already in the wild do not send ``refresh``,
        and failing their org switch would be worse than letting one token expire
        on its own schedule. When it *is* sent, the old token stops working
        immediately instead of staying valid against the previous org for the
        rest of its 14-day life.

        The token must belong to the authenticated caller. Without that check
        this field would let anyone retire any refresh token they can observe,
        turning a convenience parameter into a remote logout for other users.
        """
        from rest_framework_simplejwt.exceptions import TokenError
        from rest_framework_simplejwt.tokens import RefreshToken as BaseRefreshToken

        presented = request.data.get("refresh")
        if not presented:
            return

        try:
            token = BaseRefreshToken(presented)
        except TokenError:
            # Expired, malformed, or already blacklisted: nothing left to retire,
            # and the switch itself does not depend on it.
            return

        if str(token.get("user_id")) != str(request.user.id):
            return

        token.blacklist()

    @extend_schema(
        description="Switch to a different organization and get new JWT tokens",
        request=inline_serializer(
            name="OrgSwitchRequest",
            fields={
                "org_id": serializers.UUIDField(help_text="Target organization ID"),
                "refresh": serializers.CharField(
                    required=False,
                    help_text=(
                        "Optional. The refresh token being replaced; it is "
                        "blacklisted so it cannot be reused after the switch. "
                        "Ignored unless it belongs to the caller."
                    ),
                ),
            },
        ),
        responses={
            200: inline_serializer(
                name="OrgSwitchResponse",
                fields={
                    "access_token": serializers.CharField(),
                    "refresh_token": serializers.CharField(),
                    "current_org": serializers.DictField(),
                    "profile": serializers.DictField(),
                },
            )
        },
    )
    def post(self, request):
        from django.db import transaction

        from common.audit_log import audit_log

        org_id = request.data.get("org_id")

        if not org_id:
            return Response(
                {"error": "org_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            uuid.UUID(str(org_id))
        except (ValueError, AttributeError):
            return Response(
                {"error": "org_id must be a valid UUID"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get current org for audit logging
        from_org = getattr(request, "org", None)

        # Validate user has access to the target org
        try:
            profile = Profile.objects.get(
                user=request.user, org_id=org_id, is_active=True
            )
        except Profile.DoesNotExist:
            audit_log.permission_denied(
                request.user, from_org, "ORG_SWITCH", f"org:{org_id}", request
            )
            return Response(
                {"error": "User does not have access to this organization"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Retire the outgoing refresh token and issue its replacement together,
        # so a failure cannot leave the caller without a usable token while the
        # old one is already dead.
        with transaction.atomic():
            self._retire_presented_refresh_token(request)
            token = OrgAwareRefreshToken.for_user_and_org(
                request.user, profile.org, profile
            )

        # Audit log the org switch
        audit_log.org_switch(request.user, from_org, profile.org, request)

        return Response(
            {
                "access_token": str(token.access_token),
                "refresh_token": str(token),
                "current_org": _org_payload(profile.org),
                "profile": {
                    "id": str(profile.id),
                    "role": profile.role,
                    "is_organization_admin": is_org_admin(profile),
                },
            },
            status=status.HTTP_200_OK,
        )


class MagicLinkRequestView(APIView):
    """
    Request a magic link for passwordless login/registration.
    Always returns 200 to prevent email enumeration.
    """

    permission_classes = []
    authentication_classes = []

    @extend_schema(
        tags=["auth"],
        request=serializer.MagicLinkRequestSerializer,
        responses={
            200: inline_serializer(
                name="MagicLinkRequestResponse",
                fields={"message": serializers.CharField()},
            )
        },
    )
    def post(self, request):
        from common.models import MagicLinkToken
        from common.tasks import send_magic_link_email

        generic_response = Response(
            {"message": "If this email is valid, you will receive a sign-in link."},
            status=status.HTTP_200_OK,
        )

        serializer_obj = serializer.MagicLinkRequestSerializer(data=request.data)
        if not serializer_obj.is_valid():
            return generic_response

        email = serializer_obj.validated_data["email"].lower()
        delivery = serializer_obj.validated_data.get("delivery", "link")

        # Rate limit: max 5 tokens per email per hour
        one_hour_ago = timezone.now() - timedelta(hours=1)
        recent_count = MagicLinkToken.objects.filter(
            email=email, created_at__gte=one_hour_ago
        ).count()
        if recent_count >= 5:
            return generic_response

        # Invalidate any existing unused tokens for this email
        MagicLinkToken.objects.filter(email=email, is_used=False).update(is_used=True)

        # For code delivery, generate a 6-digit OTP. Plaintext stays in memory
        # only long enough to ship to the Celery task; the row stores only the
        # PBKDF2 hash.
        raw_code = None
        code_hash = ""
        if delivery == "code":
            raw_code = f"{secrets.randbelow(10**6):06d}"
            code_hash = make_password(raw_code)

        # Create new token
        token_obj = MagicLinkToken.objects.create(
            email=email,
            token=secrets.token_hex(32),
            delivery=delivery,
            code_hash=code_hash,
            expires_at=timezone.now() + timedelta(minutes=10),
            ip_address=client_ip(request),
        )

        # Send email via Celery. Pass raw_code only when delivery is "code".
        send_magic_link_email.delay(str(token_obj.id), raw_code=raw_code)

        return Response(
            {"message": "If this email is valid, you will receive a sign-in link."},
            status=status.HTTP_200_OK,
        )


class MagicLinkVerifyView(APIView):
    """
    Verify a magic link token and return JWT tokens.
    Creates a new user if the email doesn't exist.
    """

    permission_classes = []
    authentication_classes = []

    @extend_schema(
        tags=["auth"],
        request=serializer.MagicLinkVerifySerializer,
        responses={
            200: inline_serializer(
                name="MagicLinkVerifyResponse",
                fields={
                    "access_token": serializers.CharField(),
                    "refresh_token": serializers.CharField(),
                    "user": serializers.DictField(),
                },
            )
        },
    )
    def post(self, request):
        from django.contrib.auth.hashers import make_password

        from common.audit_log import audit_log
        from common.models import MagicLinkToken

        serializer_obj = serializer.MagicLinkVerifySerializer(data=request.data)
        if not serializer_obj.is_valid():
            return Response(
                {"error": "Invalid request"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token_value = serializer_obj.validated_data["token"]

        # Opportunistic cleanup: delete expired tokens
        MagicLinkToken.objects.filter(expires_at__lt=timezone.now()).delete()

        # Atomically mark token as used (prevents race condition)
        updated = MagicLinkToken.objects.filter(
            token=token_value,
            is_used=False,
            expires_at__gt=timezone.now(),
        ).update(is_used=True, used_at=timezone.now())

        if not updated:
            return Response(
                {"error": "Invalid or expired link"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token_obj = MagicLinkToken.objects.get(token=token_value)

        # Get or create user
        email = token_obj.email
        created = False
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            user = User.objects.create(
                email=email,
                password=make_password(secrets.token_urlsafe(32)),
                is_active=True,
            )
            created = True

        if not user.is_active:
            return _disabled_account_response()

        if created:
            from common.tasks import send_welcome_email

            send_welcome_email.delay(str(user.id))

        # Update last_login
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])

        # Get user's organizations
        profiles = Profile.objects.filter(user=user, is_active=True)
        default_org = None
        profile = None

        if profiles.exists():
            profile = profiles.first()
            default_org = profile.org

        if default_org:
            token = OrgAwareRefreshToken.for_user_and_org(user, default_org, profile)
        else:
            token = OrgAwareRefreshToken.for_user_and_org(user, None)

        audit_log.login_success(user, default_org, request)

        user_serializer = serializer.UserDetailSerializer(user)
        response_data = {
            "access_token": str(token.access_token),
            "refresh_token": str(token),
            "user": user_serializer.data,
        }

        if default_org:
            response_data["current_org"] = _org_payload(default_org)

        return Response(response_data, status=status.HTTP_200_OK)


class MagicLinkVerifyCodeView(APIView):
    """
    Verify a 6-digit OTP code (mobile flow) and return JWT tokens.
    Creates a new user if the email doesn't exist.
    """

    permission_classes = []
    authentication_classes = []

    MAX_ATTEMPTS = 5

    @extend_schema(
        tags=["auth"],
        request=serializer.MagicLinkVerifyCodeSerializer,
        responses={
            200: inline_serializer(
                name="MagicLinkVerifyCodeResponse",
                fields={
                    "access_token": serializers.CharField(),
                    "refresh_token": serializers.CharField(),
                    "user": serializers.DictField(),
                },
            )
        },
    )
    def post(self, request):
        from django.contrib.auth.hashers import check_password
        from django.db import transaction

        from common.audit_log import audit_log
        from common.models import MagicLinkToken

        serializer_obj = serializer.MagicLinkVerifyCodeSerializer(data=request.data)
        if not serializer_obj.is_valid():
            return Response(
                {"error": "Invalid request"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = serializer_obj.validated_data["email"].lower()
        code = serializer_obj.validated_data["code"]

        # Opportunistic cleanup of expired rows.
        MagicLinkToken.objects.filter(expires_at__lt=timezone.now()).delete()

        # Lock the most-recent unused code-delivery token for this email and
        # check the OTP. select_for_update protects against two concurrent
        # verify requests both observing the same `attempts` value.
        invalid = Response(
            {"error": "Invalid or expired code"},
            status=status.HTTP_400_BAD_REQUEST,
        )

        with transaction.atomic():
            token_obj = (
                MagicLinkToken.objects.select_for_update()
                .filter(
                    email=email,
                    delivery="code",
                    is_used=False,
                    expires_at__gt=timezone.now(),
                )
                .order_by("-created_at")
                .first()
            )
            if not token_obj:
                return invalid

            if not check_password(code, token_obj.code_hash):
                token_obj.attempts = (token_obj.attempts or 0) + 1
                if token_obj.attempts >= self.MAX_ATTEMPTS:
                    token_obj.is_used = True
                    token_obj.used_at = timezone.now()
                token_obj.save(update_fields=["attempts", "is_used", "used_at"])
                return invalid

            token_obj.is_used = True
            token_obj.used_at = timezone.now()
            token_obj.save(update_fields=["is_used", "used_at"])

        # Get or create user
        created = False
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            user = User.objects.create(
                email=email,
                password=make_password(secrets.token_urlsafe(32)),
                is_active=True,
            )
            created = True

        if not user.is_active:
            return _disabled_account_response()

        if created:
            from common.tasks import send_welcome_email

            send_welcome_email.delay(str(user.id))

        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])

        profiles = list(
            Profile.objects.filter(user=user, is_active=True)
            .select_related("org", "user")
            .order_by("org__name")
        )
        # Same shape the Google flow returns, so a client can offer the same
        # picker whichever way the user signed in.
        organizations = [_org_payload(p.org, profile=p) for p in profiles]

        # Bind the session to an org only when there is no choice to make.
        # Picking `profiles.first()` out of several was an arbitrary answer to
        # a question only the user can answer: it dropped a three-org admin
        # into whichever org the database happened to return first, named
        # nowhere on screen, with the picker suppressed because an org had
        # already been chosen for them.
        default_org = None
        profile = None
        if len(profiles) == 1:
            profile = profiles[0]
            default_org = profile.org

        token = OrgAwareRefreshToken.for_user_and_org(user, default_org, profile)

        audit_log.login_success(user, default_org, request)

        user_serializer = serializer.UserDetailSerializer(user)
        response_data = {
            "access_token": str(token.access_token),
            "refresh_token": str(token),
            "user": user_serializer.data,
            "organizations": organizations,
        }
        if default_org:
            response_data["current_org"] = _org_payload(default_org)
        return Response(response_data, status=status.HTTP_200_OK)
