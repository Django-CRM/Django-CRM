"""REST endpoints for macros / canned responses.

Routes (all under /api/macros/):
    GET    /: list macros visible to the requester
                                (org scope + own personal scope).
    POST   /: create. Non-admins forced into personal scope.
    GET    /<id>/: retrieve.
    PUT    /<id>/: update. Same scope rules as create.
    PATCH  /<id>/: partial update.
    DELETE /<id>/: soft-deactivate org macros, hard-delete
                                personal ones (per spec).
    POST   /<id>/render/: server-side substitute placeholders against
                                the requested case and return the rendered
                                body. Increments usage_count.
"""

from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.access import get_case_or_404
from common.permissions import HasOrgContext, is_org_admin
from macros.models import Macro
from macros.render import (
    SUPPORTED_PLACEHOLDERS,
    find_unknown_placeholders,
    render_macro,
)
from macros.serializers import MacroSerializer


def _visible_qs(profile):
    """Macros the requester can see: every org-scope row in the org plus
    any personal-scope row owned by the requester."""
    return Macro.objects.filter(org=profile.org).filter(
        Q(scope=Macro.SCOPE_ORG) | Q(scope=Macro.SCOPE_PERSONAL, owner=profile)
    )


def _compute_totals(visible_qs) -> dict:
    """Summary counts over the requester's *visible* set, for the stat cards.

    Computed over the full visible queryset independent of any active/search
    filter on the list, so "Turned off" and "Broken placeholders" stay
    meaningful when the list itself is filtered. `with_unknown_placeholders`
    is not a DB aggregate (it depends on `find_unknown_placeholders`, the same
    check the serializer surfaces per row), so we fetch the three columns we
    need and fold in Python. The visible set is small (org macros + the
    requester's own personal ones).
    """
    rows = list(visible_qs.values_list("scope", "is_active", "body"))
    return {
        "count": len(rows),
        "org": sum(1 for scope, _, _ in rows if scope == Macro.SCOPE_ORG),
        "personal": sum(1 for scope, _, _ in rows if scope == Macro.SCOPE_PERSONAL),
        "inactive": sum(1 for _, is_active, _ in rows if not is_active),
        "with_unknown_placeholders": sum(
            1 for _, _, body in rows if find_unknown_placeholders(body)
        ),
    }


def _resolve_scope_and_owner(profile, payload, instance=None):
    """Apply the spec's create/update rules.

    - Non-admins may only manage `scope=personal` macros owned by themselves.
    - Admins may create either; org-scope rows must have owner=None.
    Returns `(scope, owner_profile)` or raises a ValueError with a message
    describing the violation.
    """
    desired_scope = payload.get(
        "scope",
        getattr(instance, "scope", None) or Macro.SCOPE_ORG,
    )
    if desired_scope not in (Macro.SCOPE_ORG, Macro.SCOPE_PERSONAL):
        raise ValueError("scope must be 'org' or 'personal'.")

    if desired_scope == Macro.SCOPE_ORG:
        if not is_org_admin(profile):
            raise ValueError("Only admins can manage org-scope macros.")
        return desired_scope, None
    return desired_scope, profile


class MacroListCreateView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get(self, request, *args, **kwargs):
        visible = _visible_qs(request.profile)
        totals = _compute_totals(visible)
        qs = visible
        active_param = request.query_params.get("active")
        if active_param is not None:
            qs = qs.filter(is_active=(active_param.lower() == "true"))
        search = request.query_params.get("search")
        if search:
            qs = qs.filter(Q(title__icontains=search) | Q(body__icontains=search))
        qs = qs.order_by("-updated_at")
        return Response(
            {
                "results": MacroSerializer(qs, many=True).data,
                "totals": totals,
                # The supported set is server-owned (macros/render.py); the
                # reference card renders exactly what the renderer expands.
                "placeholders": list(SUPPORTED_PLACEHOLDERS),
            }
        )

    def post(self, request, *args, **kwargs):
        serializer = MacroSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            scope, owner = _resolve_scope_and_owner(request.profile, request.data)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        macro = Macro.objects.create(
            org=request.profile.org,
            scope=scope,
            owner=owner,
            title=serializer.validated_data["title"],
            body=serializer.validated_data["body"],
            is_active=serializer.validated_data.get("is_active", True),
        )
        return Response(MacroSerializer(macro).data, status=status.HTTP_201_CREATED)


class MacroDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def _get_writable(self, request, pk):
        """Fetch the macro and confirm the requester is allowed to mutate it.

        Returns either the Macro instance or a Response (which the caller
        should return as-is). Cross-org access returns 404 to mirror RLS.
        """
        macro = get_object_or_404(Macro, pk=pk, org=request.profile.org)
        if macro.scope == Macro.SCOPE_ORG and not is_org_admin(request.profile):
            # An org macro IS visible to this non-admin; the refusal is an
            # authorization one (403), not a hidden object.
            return Response(
                {"error": "Only admins can edit org-scope macros."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if macro.scope == Macro.SCOPE_PERSONAL and macro.owner_id != request.profile.id:
            # A personal macro you don't own is invisible to you. The list and
            # GET hide it (404). The write verbs must not confirm it exists via
            # a 403 either, or the id space leaks which rows are somebody else's
            # personal macros. Mirror the GET: 404.
            return Response(
                {"detail": "Not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return macro

    def get(self, request, pk, *args, **kwargs):
        macro = get_object_or_404(Macro, pk=pk, org=request.profile.org)
        # Visibility: same rule as the list filter.
        if macro.scope == Macro.SCOPE_PERSONAL and macro.owner_id != request.profile.id:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(MacroSerializer(macro).data)

    def put(self, request, pk, *args, **kwargs):
        return self._update(request, pk, partial=False)

    def patch(self, request, pk, *args, **kwargs):
        return self._update(request, pk, partial=True)

    def _update(self, request, pk, *, partial):
        result = self._get_writable(request, pk)
        if isinstance(result, Response):
            return result
        macro = result
        serializer = MacroSerializer(macro, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        # Allow scope changes only inside the same authority bucket: an
        # admin can flip personal<->org, a non-admin trying to flip their
        # personal macro into org gets blocked here.
        try:
            scope, owner = _resolve_scope_and_owner(
                request.profile, request.data, instance=macro
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        for field in ("title", "body", "is_active"):
            if field in serializer.validated_data:
                setattr(macro, field, serializer.validated_data[field])
        macro.scope = scope
        macro.owner = owner
        macro.save()
        return Response(MacroSerializer(macro).data)

    def delete(self, request, pk, *args, **kwargs):
        result = self._get_writable(request, pk)
        if isinstance(result, Response):
            return result
        macro = result
        if macro.scope == Macro.SCOPE_ORG:
            macro.is_active = False
            macro.save(update_fields=["is_active", "updated_at"])
        else:
            macro.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MacroRenderView(APIView):
    """POST /<id>/render/, substitute placeholders against a case."""

    permission_classes = (IsAuthenticated, HasOrgContext)

    def post(self, request, pk, *args, **kwargs):
        macro = get_object_or_404(Macro, pk=pk, org=request.profile.org)
        # Mirror visibility rules: a personal macro from another user
        # should not be discoverable by id either.
        if macro.scope == Macro.SCOPE_PERSONAL and macro.owner_id != request.profile.id:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if not macro.is_active:
            return Response(
                {"error": "Macro is inactive."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        case_id = request.data.get("case_id")
        if not case_id:
            return Response(
                {"error": "case_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # The rendered text carries the case's subject and its contact's name
        # and email, so the case must be one the caller may open. A same-org
        # case they may not read, another org's case and a malformed id all
        # answer the same 404 (`get_case_or_404`, the case detail's own rule).
        case = get_case_or_404(request.profile, case_id)

        rendered = render_macro(macro, case, request.profile)
        with transaction.atomic():
            Macro.objects.filter(pk=macro.pk).update(usage_count=F("usage_count") + 1)
        return Response({"rendered_body": rendered})
