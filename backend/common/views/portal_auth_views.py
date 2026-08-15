"""Customer portal sign-in. Passwordless, per-org, and enumeration-safe.

Modelled on `MagicLinkRequestView` in `auth_views.py`, deliberately not sharing
its table. See `common.models.PortalLoginToken` for why.

The org comes from the URL and never from anything the caller holds, and an
attempt only ever resolves Contact rows in that one org. That is what stops one
tenant's sign-in page disclosing that a visitor is also a customer of another:
`Contact` is one row per org (`unique_contact_email_per_org`), so the same
address in two orgs is two unrelated relationships and must stay that way.
"""

import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from common.models import Org, PortalLoginToken
from common.portal_auth import mint_portal_token
from common.tasks import set_rls_context
from contacts.models import Contact

TOKEN_TTL = timedelta(minutes=10)
MAX_REQUESTS_PER_HOUR = 5
MAX_CODE_ATTEMPTS = 5

# One sentence for every outcome. A different message, a different status code,
# or a different amount of work done all leak the same fact: whether this
# address is a customer of this org.
GENERIC_MESSAGE = "If this email is valid, you will receive a sign-in code."


class PortalLoginRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PortalLoginVerifySerializer(serializers.Serializer):
    """The email the code was sent to, and the code.

    Keyed on the email rather than on an opaque row id because that is all the
    customer has: they are reading six digits off their phone.
    """

    email = serializers.EmailField()
    code = serializers.CharField(max_length=6)


class PortalLoginRequestView(APIView):
    """Mint and email a one-time sign-in code, if the caller is a contact here."""

    permission_classes = []
    authentication_classes = []

    def post(self, request, org_id):
        generic = Response({"message": GENERIC_MESSAGE}, status=status.HTTP_200_OK)

        payload = PortalLoginRequestSerializer(data=request.data)
        if not payload.is_valid():
            return generic

        org = Org.objects.filter(id=org_id, is_active=True).first()
        if org is None:
            return generic

        # `contacts` is org-scoped, so without this the lookup below runs under
        # an empty RLS context and returns zero rows on a correctly configured
        # database. Being allowed through the middleware is not the same as
        # being able to read the row; see backend/docs/PORTAL_RLS.md.
        set_rls_context(str(org.id))

        contact = (
            Contact.objects.filter(
                org=org,
                email__iexact=payload.validated_data["email"],
                is_active=True,
            )
            .exclude(email__isnull=True)
            .exclude(email="")
            .first()
        )
        if contact is None:
            return generic

        one_hour_ago = timezone.now() - timedelta(hours=1)
        recent = PortalLoginToken.objects.filter(
            contact=contact, created_at__gte=one_hour_ago
        ).count()
        if recent >= MAX_REQUESTS_PER_HOUR:
            return generic

        # A new request retires the previous one, so a forwarded or shoulder-read
        # older email stops working the moment the real customer asks again.
        PortalLoginToken.objects.filter(contact=contact, is_used=False).update(
            is_used=True, used_at=timezone.now()
        )

        raw_code = f"{secrets.randbelow(10**6):06d}"
        token_obj = PortalLoginToken.objects.create(
            org=org,
            contact=contact,
            code_hash=make_password(raw_code),
            expires_at=timezone.now() + TOKEN_TTL,
            ip_address=request.META.get("REMOTE_ADDR"),
        )

        from common.tasks import send_portal_login_email

        send_portal_login_email.delay(str(token_obj.id), raw_code)
        return generic


class PortalLoginVerifyView(APIView):
    """Exchange an emailed sign-in code for portal access."""

    permission_classes = []
    authentication_classes = []

    def _redeem_code(self, org, email, code):
        """Find this contact's live code and check it.

        `org` is part of every lookup on purpose: the same address in two orgs
        is two unrelated relationships, so a code minted by one tenant must not
        resolve at another tenant's endpoint.

        `select_for_update` matches the internal magic-link flow: without it two
        concurrent guesses can both read the same `attempts` value and the
        lockout counts one.

        A wrong code burns an attempt, and the row is retired entirely once the
        limit is reached, because six digits are guessable if guesses are free.
        """
        contact = Contact.objects.filter(
            org=org, email__iexact=email, is_active=True
        ).first()
        if contact is None:
            return None

        token_obj = (
            PortalLoginToken.objects.select_for_update()
            .filter(
                contact=contact,
                org=org,
                is_used=False,
                expires_at__gt=timezone.now(),
            )
            .order_by("-created_at")
            .first()
        )
        if token_obj is None:
            return None

        if not check_password(code, token_obj.code_hash):
            token_obj.attempts = (token_obj.attempts or 0) + 1
            if token_obj.attempts >= MAX_CODE_ATTEMPTS:
                token_obj.is_used = True
                token_obj.used_at = timezone.now()
            token_obj.save(update_fields=["attempts", "is_used", "used_at"])
            return None

        return token_obj

    def post(self, request, org_id):
        invalid = Response(
            {"error": "That code is not valid any more. Request a new one."},
            status=status.HTTP_400_BAD_REQUEST,
        )

        payload = PortalLoginVerifySerializer(data=request.data)
        if not payload.is_valid():
            return invalid

        org = Org.objects.filter(id=org_id, is_active=True).first()
        if org is None:
            return invalid

        set_rls_context(str(org.id))

        data = payload.validated_data
        with transaction.atomic():
            token_obj = self._redeem_code(org, data["email"], data["code"])
            if token_obj is None:
                return invalid

            # Re-read the contact rather than trusting the FK on a row minted
            # ten minutes ago: it may have been deactivated in between.
            contact = Contact.objects.filter(
                id=token_obj.contact_id, org=org, is_active=True
            ).first()
            if contact is None:
                return invalid

            token_obj.is_used = True
            token_obj.used_at = timezone.now()
            token_obj.save(update_fields=["is_used", "used_at"])

        return Response(
            {
                "access_token": mint_portal_token(contact),
                "contact": {
                    "id": str(contact.id),
                    "name": f"{contact.first_name} {contact.last_name}".strip(),
                    "email": contact.email,
                },
                "org": {"id": str(org.id), "name": org.name or ""},
            },
            status=status.HTTP_200_OK,
        )
