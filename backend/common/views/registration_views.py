import logging
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from common import serializer
from common.models import AccountSignupChallenge, Org, Profile, User
from common.request_meta import client_ip
from common.serializer import OrgAwareRefreshToken
from common.throttles import (
    AccountRecoveryGlobalThrottle,
    AccountRecoveryIPThrottle,
    FirstRefusalThrottleMixin,
)
from common.views.auth_views import _org_payload

logger = logging.getLogger(__name__)

TERMS_VERSION = "2026-11-18"
PRIVACY_VERSION = "2026-11-18"
MAX_CODE_ATTEMPTS = 5
MAX_EMAIL_CODES_PER_HOUR = 3


def _send_activation_email(challenge, code):
    """Send the dedicated signup activation email; existing auth templates are untouched."""
    html = render_to_string(
        "account_activation_code_email.html",
        {
            "first_name": challenge.first_name,
            "last_name": challenge.last_name,
            "code": code,
        },
    )
    message = EmailMultiAlternatives(
        f"Activate your Godan CRM account: {code}",
        (
            f"Hello {challenge.first_name} {challenge.last_name},\n\n"
            f"Your Godan CRM account activation code is {code}. "
            "It expires in 10 minutes and can only be used once. "
            "If you did not start this registration, ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[challenge.email],
    )
    message.attach_alternative(html, "text/html")
    message.extra_headers = {"Idempotency-Key": f"account-activation-{challenge.id}"}
    return bool(message.send())


class AccountSignupRequestView(FirstRefusalThrottleMixin, APIView):
    """Stage a signup and email a one-time code; do not create a User or Org yet."""

    permission_classes = []
    authentication_classes = []
    throttle_classes = [AccountRecoveryIPThrottle, AccountRecoveryGlobalThrottle]

    def post(self, request):
        data = request.data
        email = str(data.get("email", "")).strip().lower()
        first_name = str(data.get("first_name", "")).strip()
        last_name = str(data.get("last_name", "")).strip()
        phone = str(data.get("phone", "")).strip()
        country = str(data.get("country", "")).strip().upper()
        organization_name = str(data.get("organization_name", "")).strip()
        password = data.get("password")
        confirm_password = data.get("confirm_password")
        if not all((email, first_name, last_name, phone, country, organization_name)):
            return Response({"error": "Complete all required account details."}, status=400)
        try:
            serializers.EmailField().run_validation(email)
        except serializers.ValidationError:
            return Response({"error": "Enter a valid work email address."}, status=400)
        if len(first_name) > 100 or len(last_name) > 100 or len(organization_name) > 100:
            return Response({"error": "One or more fields are too long."}, status=400)
        if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
            return Response({"error": "Enter your phone number in international format, including the + country code."}, status=400)
        if not re.fullmatch(r"[A-Z]{2}", country):
            return Response({"error": "Choose a valid country."}, status=400)
        if not isinstance(password, str) or len(password) < 8 or password != confirm_password:
            return Response({"error": "Use a password of at least 8 characters and make sure both password fields match."}, status=400)
        if data.get("accept_terms") is not True or data.get("accept_privacy") is not True:
            return Response({"error": "You must accept the Terms of Service and Privacy Policy to create an account."}, status=400)
        if User.objects.filter(email__iexact=email).exists():
            return Response({"error": "An account with this email already exists. Sign in or use account recovery."}, status=409)

        hour_ago = timezone.now() - timedelta(hours=1)
        if AccountSignupChallenge.objects.filter(email__iexact=email, created_at__gte=hour_ago).count() >= MAX_EMAIL_CODES_PER_HOUR:
            return Response({"error": "Too many activation codes requested. Please wait before trying again."}, status=429)

        candidate = User(email=email, name=f"{first_name} {last_name}")
        try:
            validate_password(password, candidate)
        except ValidationError as exc:
            return Response({"error": " ".join(exc.messages)}, status=400)

        AccountSignupChallenge.objects.filter(
            email__iexact=email, is_used=False
        ).update(is_used=True, used_at=timezone.now())
        code = f"{secrets.randbelow(10**6):06d}"
        now = timezone.now()
        challenge = AccountSignupChallenge.objects.create(
            email=email,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            country=country,
            organization_name=organization_name,
            password_hash=make_password(password),
            code_hash=make_password(code),
            terms_version=TERMS_VERSION,
            privacy_version=PRIVACY_VERSION,
            terms_accepted_at=now,
            privacy_accepted_at=now,
            marketing_consent=data.get("marketing_consent") is True,
            expires_at=now + timedelta(minutes=10),
            ip_address=client_ip(request),
        )
        try:
            sent = _send_activation_email(challenge, code)
        except Exception:
            logger.exception("Account activation email delivery failed for challenge %s", challenge.id)
            sent = False
        if not sent:
            challenge.is_used = True
            challenge.used_at = timezone.now()
            challenge.save(update_fields=["is_used", "used_at"])
            return Response({"error": "We could not send your activation code. Please try again."}, status=503)
        return Response(
            {
                "message": "An activation code has been sent to your email address.",
                "challenge_id": str(challenge.id),
                "email": challenge.email,
                "expires_in": 600,
            },
            status=status.HTTP_201_CREATED,
        )


class AccountSignupResendView(FirstRefusalThrottleMixin, APIView):
    permission_classes = []
    authentication_classes = []
    throttle_classes = [AccountRecoveryIPThrottle, AccountRecoveryGlobalThrottle]

    def post(self, request):
        challenge_id = request.data.get("challenge_id")
        challenge = AccountSignupChallenge.objects.filter(
            id=challenge_id,
            is_used=False,
            created_at__gte=timezone.now() - timedelta(hours=24),
        ).first()
        if not challenge:
            return Response({"error": "Your signup session has expired. Start again."}, status=400)
        if AccountSignupChallenge.objects.filter(
            email=challenge.email, created_at__gte=timezone.now() - timedelta(hours=1)
        ).count() > MAX_EMAIL_CODES_PER_HOUR:
            return Response({"error": "Too many activation codes requested. Please wait before trying again."}, status=429)
        code = f"{secrets.randbelow(10**6):06d}"
        challenge.code_hash = make_password(code)
        challenge.attempts = 0
        challenge.expires_at = timezone.now() + timedelta(minutes=10)
        challenge.save(update_fields=["code_hash", "attempts", "expires_at"])
        try:
            sent = _send_activation_email(challenge, code)
        except Exception:
            logger.exception("Account activation resend failed for challenge %s", challenge.id)
            sent = False
        if not sent:
            return Response({"error": "We could not send a new activation code. Please try again."}, status=503)
        return Response({"message": "A new activation code has been sent.", "expires_in": 600}, status=200)


class AccountSignupVerifyView(FirstRefusalThrottleMixin, APIView):
    """Redeem the activation code and atomically create the user and owner workspace."""

    permission_classes = []
    authentication_classes = []
    throttle_classes = [AccountRecoveryIPThrottle, AccountRecoveryGlobalThrottle]

    def post(self, request):
        challenge_id = request.data.get("challenge_id")
        code = str(request.data.get("code", "")).strip()
        if not code.isdigit() or len(code) != 6:
            return Response({"error": "Enter the six-digit activation code."}, status=400)
        with transaction.atomic():
            challenge = (
                AccountSignupChallenge.objects.select_for_update()
                .filter(id=challenge_id, is_used=False)
                .first()
            )
            if not challenge or challenge.expires_at <= timezone.now():
                return Response({"error": "This activation code is invalid or expired. Request a new code."}, status=400)
            if challenge.attempts >= MAX_CODE_ATTEMPTS:
                return Response({"error": "Too many incorrect attempts. Request a new code."}, status=429)
            if not check_password(code, challenge.code_hash):
                challenge.attempts += 1
                challenge.save(update_fields=["attempts"])
                return Response({"error": "That activation code is incorrect. Check it and try again."}, status=400)
            if User.objects.filter(email__iexact=challenge.email).exists():
                challenge.is_used = True
                challenge.used_at = timezone.now()
                challenge.save(update_fields=["is_used", "used_at"])
                return Response({"error": "An account with this email already exists. Sign in instead."}, status=409)

            try:
                user = User(
                    email=challenge.email,
                    name=f"{challenge.first_name} {challenge.last_name}",
                    password=challenge.password_hash,
                    is_active=True,
                )
                user.save(force_insert=True)
                org = Org.objects.create(
                    name=challenge.organization_name,
                    company_name=challenge.organization_name,
                    email=challenge.email,
                    phone=challenge.phone,
                    default_country=challenge.country,
                )
                profile = Profile.objects.create(
                    user=user,
                    org=org,
                    phone=challenge.phone,
                    role="ADMIN",
                )
            except IntegrityError:
                return Response({"error": "We could not finish creating this account. Please sign in or try again."}, status=409)

            challenge.is_used = True
            challenge.used_at = timezone.now()
            challenge.save(update_fields=["is_used", "used_at"])
            user.last_login = timezone.now()
            user.save(update_fields=["last_login"])

        token = OrgAwareRefreshToken.for_user_and_org(user, org, profile)
        from common.audit_log import audit_log
        audit_log.login_success(user, org, request)
        return Response(
            {
                "access_token": str(token.access_token),
                "refresh_token": str(token),
                "user": serializer.UserDetailSerializer(user).data,
                "organizations": [_org_payload(org, profile=profile)],
                "current_org": _org_payload(org),
            },
            status=status.HTTP_201_CREATED,
        )
