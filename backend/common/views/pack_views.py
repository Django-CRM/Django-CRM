"""Vertical-pack endpoints.

Authorization: any authenticated member may list packs; only an org admin
(``common.permissions.is_org_admin``: the ADMIN role, or a superuser) may apply
one or clear sample data. The org is always request.profile.org, never a
body field. Org.vertical is descriptive and is never read here for access control.
"""

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.apps_catalogue import AppConflict, install_app
from common.models import PackApplication
from common.packs.applier import clear_sample_data
from common.packs.loader import get_pack, get_registry
from common.permissions import HasOrgContext, may_configure


def _skipped():
    return inline_serializer(
        name="PackSkippedItem",
        many=True,
        fields={
            "type": serializers.CharField(),
            "name": serializers.CharField(),
            "reason": serializers.CharField(),
        },
    )


_REPORT = inline_serializer(
    name="PackApplyReport",
    fields={
        "created": inline_serializer(
            name="PackCreatedItem",
            many=True,
            fields={"type": serializers.CharField(), "name": serializers.CharField()},
        ),
        "skipped": _skipped(),
        # Nothing records a failure today, so it is always empty.
        "failed": _skipped(),
    },
)


def _counts():
    return serializers.DictField(child=serializers.IntegerField())


def _require_admin(request):
    """Return a 403 Response if the caller is not an org admin, else None."""
    if not may_configure(getattr(request, "profile", None)):
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

    @extend_schema(
        responses={
            200: inline_serializer(
                name="PackListResponse",
                fields={
                    "packs": inline_serializer(
                        name="PackSummary",
                        many=True,
                        fields={
                            "id": serializers.CharField(),
                            "name": serializers.CharField(),
                            "description": serializers.CharField(),
                            "version": serializers.IntegerField(),
                        },
                    )
                },
            )
        }
    )
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
    """Installs the app with sample data, for phones in the stores that still
    apply packs, and answers with the run's report as apply did. An app already
    installed adds nothing; ``/api/apps/`` updates it."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        request=None,
        responses={
            200: inline_serializer(name="PackApplyResponse", fields={"report": _REPORT})
        },
    )
    def post(self, request, pack_id):
        denied = _require_admin(request)
        if denied:
            return denied
        # Registry lookup only. pack_id is a dict key, never a path segment.
        if get_pack(pack_id) is None:
            return Response(
                {"error": True, "errors": "Unknown pack."},
                status=status.HTTP_404_NOT_FOUND,
            )
        org = request.profile.org
        try:
            install_app(org, pack_id, request.profile, True, request=request)
        except AppConflict:
            return Response({"report": {"created": [], "skipped": [], "failed": []}})
        run = PackApplication.objects.filter(org=org, pack_id=pack_id).first()
        report = {key: run.result[key] for key in ("created", "skipped", "failed")}
        return Response({"report": report})


class PackSampleDataView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        responses={
            200: inline_serializer(
                name="PackSampleDataClearResponse",
                fields={
                    "deleted": serializers.IntegerField(),
                    "deleted_by_type": _counts(),
                    "retained_by_type": _counts(),
                    "switched_off_by_type": _counts(),
                },
            )
        }
    )
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
                "switched_off_by_type": result["switched_off"],
            }
        )
