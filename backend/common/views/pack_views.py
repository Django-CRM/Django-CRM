"""Vertical-pack endpoints.

Authorization: any authenticated member may list packs; only an org admin
(``common.permissions.is_org_admin``: the ADMIN role, or a superuser) may apply
one or clear sample data. The org is always request.profile.org, never a
body field. Org.vertical is descriptive and is never read here for access control.
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.packs.applier import apply_pack, clear_sample_data
from common.packs.loader import get_pack, get_registry
from common.permissions import HasOrgContext, is_org_admin


def _require_admin(request):
    """Return a 403 Response if the caller is not an org admin, else None."""
    if not is_org_admin(getattr(request, "profile", None)):
        return Response(
            {"error": True, "errors": "Only an organization admin can do this."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


class PackListView(APIView):
    # No HasOrgContext: this list is static repo content (JSON files),
    # byte-identical for every tenant, with no tenant data in the response.
    # It backs the pack chooser on the org-creation page, where a user
    # creating their first org has no org_id claim yet. HasOrgContext would
    # 403 exactly that audience. /api/packs/ is exempted from
    # RequireOrgContext by exact match (not prefix) in
    # common/middleware/rls_context.py; see the comment there for why the
    # write/destroy endpoints below must not be. IsAuthenticated still
    # applies. This is not anonymous.
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        packs = [
            {
                "id": p["id"],
                "name": p["name"],
                "description": p.get("description", ""),
                "version": p["version"],
            }
            for p in get_registry().values()
        ]
        return Response({"packs": sorted(packs, key=lambda p: p["name"])})


class PackApplyView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def post(self, request, pack_id):
        denied = _require_admin(request)
        if denied:
            return denied
        # Registry lookup only. pack_id is a dict key, never a path segment.
        pack = get_pack(pack_id)
        if pack is None:
            return Response(
                {"error": True, "errors": "Unknown pack."},
                status=status.HTTP_404_NOT_FOUND,
            )
        report = apply_pack(request.profile.org, pack, request.profile)
        return Response({"report": report})


class PackSampleDataView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def delete(self, request):
        denied = _require_admin(request)
        if denied:
            return denied
        result = clear_sample_data(request.profile.org, request.profile)
        # `deleted` stays an integer for the existing client: it was the total
        # count before sample data covered more than leads, and the settings page
        # renders it directly. The per-type breakdown and the retained rows are
        # added alongside it rather than replacing it.
        return Response(
            {
                "deleted": sum(result["deleted"].values()),
                "deleted_by_type": result["deleted"],
                "retained_by_type": result["retained"],
            }
        )
