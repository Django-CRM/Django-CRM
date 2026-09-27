"""Personal Access Token CRUD for programmatic REST API access.

Tokens authenticate scripts, integrations and AI agents against the API under
``/api/`` with an ``Authorization: Bearer bcrm_pat_...`` header. A token acts
AS its owning profile and inherits that user's role, org and RLS scope.

A user manages ONLY their own tokens. Both list and revoke are filtered by
``org=request.profile.org`` AND ``profile=request.profile`` so another user's
token id 404s rather than leaking or being revoked (IDOR guard). The raw token
is returned exactly once, on create; ``token_hash`` is never serialized.
Creating a token and revoking one (here or by an admin below) each write one
``API_TOKEN_*`` row to the org's security audit log.

The ``Org*`` views below are the admin's *oversight* half. Org-wide read and
revoke, gated to admins, and are deliberately kept SEPARATE from the
self-scoped views above rather than widening them, so the "a user manages only
their own" guard is never relaxed. ``personal_access_token`` is intentionally
NOT RLS-protected (it is looked up by ``token_hash`` before any tenant context
exists), so on every view here the explicit ``org=request.profile.org`` filter
is the *only* tenant barrier, load-bearing, not a belt over RLS's braces.
"""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.audit_log import audit_log
from common.models import PersonalAccessToken
from common.permissions import HasOrgContext, IsOrgAdmin
from common.serializer import (
    PersonalAccessTokenCreateSerializer,
    PersonalAccessTokenListSerializer,
)


def _revoke(request, pat):
    """Revoke ``pat`` and audit it, once. Revoking an already-revoked token is
    a no-op and writes no row. The conditional update means two revokes racing
    on one token still write one row between them."""
    revoked = PersonalAccessToken.objects.filter(
        pk=pat.pk, revoked_at__isnull=True
    ).update(revoked_at=timezone.now())
    if revoked:
        audit_log.api_token_revoked(request.user, pat, request)


class PersonalAccessTokenListCreateView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["API Tokens"], operation_id="pat_list")
    def get(self, request):
        qs = PersonalAccessToken.objects.filter(
            org=request.profile.org, profile=request.profile
        ).order_by("-created_at")
        return Response(
            {
                "error": False,
                "tokens": PersonalAccessTokenListSerializer(qs, many=True).data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["API Tokens"],
        operation_id="pat_create",
        request=PersonalAccessTokenCreateSerializer,
    )
    def post(self, request):
        ser = PersonalAccessTokenCreateSerializer(data=request.data)
        if not ser.is_valid():
            return Response(
                {"error": True, "errors": ser.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        raw, pat = PersonalAccessToken.generate(
            profile=request.profile,
            name=ser.validated_data["name"],
            scopes=ser.validated_data.get("scopes", []),
            expires_at=ser.validated_data.get("expires_at"),
        )
        audit_log.api_token_created(request.user, pat, request)
        data = PersonalAccessTokenListSerializer(pat).data
        data["token"] = raw  # shown ONCE, never retrievable again
        return Response({"error": False, **data}, status=status.HTTP_201_CREATED)


class PersonalAccessTokenDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(tags=["API Tokens"], operation_id="pat_revoke")
    def delete(self, request, pk):
        pat = get_object_or_404(
            PersonalAccessToken.objects.select_related("profile__user"),
            pk=pk,
            org=request.profile.org,
            profile=request.profile,
        )
        _revoke(request, pat)
        return Response({"error": False}, status=status.HTTP_200_OK)


class OrgAccessTokenListView(APIView):
    """Org-wide token oversight. ADMIN only, read.

    The self-scoped list above answers "my tokens". This one answers the
    admin's question: which tokens exist across the whole org, and which two
    kinds are worth acting on.

    * a token whose owner has been deactivated, and
    * a token nobody has used in a long time.

    Neither is a raw value: only ``token_prefix`` is ever exposed (the model
    keeps a hash), and ``token_hash`` never leaves the DB. The owner block is
    the minimum needed to name and risk-rank a row: display name, role, and
    ``is_active``.

    A NOTE ON "OWNER DEACTIVATED": ``profile.is_active`` here is *exactly* the
    flag ``resolve_valid_pat`` checks, so a token on a deactivated owner is
    already rejected at login (see
    ``common/tests/test_pat_auth.py::test_inactive_profile_raises``). It is a
    dormant liability, not a live credential. It would authenticate again only
    if the account were reactivated. The count is surfaced so an admin can
    revoke it as part of offboarding, not because it is "still working".

    A NOTE ON "UNUSED 90+ DAYS": a token that has never been used is measured
    from ``created_at``, not counted outright. ``last_used_at`` is null both for
    a token issued three years ago and for one issued a minute ago, and the
    count used to treat the two the same, so creating a token through the page
    put the very token you just made into "unused for 90+ days" on the next
    reload. A figure that names a duration has to have measured one.
    """

    permission_classes = (IsAuthenticated, HasOrgContext, IsOrgAdmin)

    @extend_schema(tags=["API Tokens"], operation_id="org_pat_list")
    def get(self, request):
        now = timezone.now()
        qs = (
            PersonalAccessToken.objects.filter(org=request.profile.org)
            .select_related("profile", "profile__user")
            .order_by("-created_at")
        )
        tokens = []
        totals = {"count": 0, "live": 0, "orphaned": 0, "unused_90d": 0}
        for pat in qs:
            # Reuse the vetted read serializer for the safe field set (it lists
            # them explicitly and cannot leak the hash), then attach the owner
            # and the derived risk flags the oversight page needs.
            row = dict(PersonalAccessTokenListSerializer(pat).data)
            owner = pat.profile
            row["owner"] = {
                "id": str(owner.id),
                "name": owner.user.name or owner.user.email,
                "role": owner.role,
                "is_active": owner.is_active,
            }
            live = pat.is_valid()
            row["is_live"] = live
            tokens.append(row)

            totals["count"] += 1
            if live:
                totals["live"] += 1
                if not owner.is_active:
                    totals["orphaned"] += 1
                # Never used falls back to when it was issued, so the age is
                # always a measured one. See the class docstring.
                if (now - (pat.last_used_at or pat.created_at)).days > 90:
                    totals["unused_90d"] += 1

        return Response(
            {"error": False, "tokens": tokens, "totals": totals},
            status=status.HTTP_200_OK,
        )


class OrgAccessTokenDetailView(APIView):
    """Admin revoke of ANY token in the org, org-scoped, not profile-scoped.

    Separate from ``PersonalAccessTokenDetailView`` (which stays self-only): an
    admin cleaning up a deactivated colleague's token cannot use the self path.
    The org filter is the sole tenant barrier (no RLS on this table), so a pk in
    another org 404s rather than being revoked cross-tenant. Revoke is
    idempotent. A second call on an already-revoked token is a no-op 200.
    """

    permission_classes = (IsAuthenticated, HasOrgContext, IsOrgAdmin)

    @extend_schema(tags=["API Tokens"], operation_id="org_pat_revoke")
    def delete(self, request, pk):
        pat = get_object_or_404(
            PersonalAccessToken.objects.select_related("profile__user"),
            pk=pk,
            org=request.profile.org,
        )
        _revoke(request, pat)
        return Response({"error": False}, status=status.HTTP_200_OK)
