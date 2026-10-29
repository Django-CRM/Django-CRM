"""The org's lead sources: any member reads them, an admin edits them.

Not gated by a module: deals and contacts read the same list.
"""

from django.db import IntegrityError, transaction
from django.db.models import Q
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.lookups import get_scoped_or_404
from common.permissions import HasOrgContext, may_configure
from leads.models import LeadSource
from leads.sources import derive_key, org_sources

LABEL_MAX = 64  # a key is the label lowercased, and a key holds 64

ADMIN_ONLY = "Only admins can change lead sources"
DUPLICATE = "A source with this name already exists."


def _row(source) -> dict:
    return {
        "id": str(source.id),
        "key": source.key,
        "label": source.label,
        "is_active": source.is_active,
        "order": source.order,
    }


def _refuse(errors, code=status.HTTP_400_BAD_REQUEST):
    return Response({"error": True, "errors": errors}, status=code)


def _forbidden():
    return Response({"error": ADMIN_ONLY}, status=status.HTTP_403_FORBIDDEN)


class LeadSourceSerializer(serializers.Serializer):
    """Response-only: one `_row`."""

    id = serializers.CharField()
    key = serializers.CharField()
    label = serializers.CharField()
    is_active = serializers.BooleanField()
    order = serializers.IntegerField()


_SOURCE_LIST = inline_serializer(
    "LeadSourceListResponse", {"sources": LeadSourceSerializer(many=True)}
)


class SourceCreateSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=LABEL_MAX)

    def validate_label(self, value):
        if not derive_key(value):
            raise serializers.ValidationError("This field may not be blank.")
        return value


class SourceUpdateSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=LABEL_MAX, required=False)
    is_active = serializers.BooleanField(required=False)

    def validate_label(self, value):
        if not derive_key(value):
            raise serializers.ValidationError("This field may not be blank.")
        return value


class SourceOrderSerializer(serializers.Serializer):
    keys = serializers.ListField(child=serializers.CharField(), allow_empty=False)


class LeadSourceListCreateView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Leads"], operation_id="lead_sources_list", responses={200: _SOURCE_LIST}
    )
    def get(self, request):
        sources = org_sources(request.profile.org)
        return Response({"sources": [_row(s) for s in sources]})

    @extend_schema(
        tags=["Leads"],
        operation_id="lead_sources_create",
        request=SourceCreateSerializer,
        responses={201: LeadSourceSerializer},
    )
    def post(self, request):
        if not may_configure(request.profile):
            return _forbidden()
        org = request.profile.org
        serializer = SourceCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return _refuse(serializer.errors)
        label = " ".join(serializer.validated_data["label"].split())
        key = derive_key(label)

        # Seeds the defaults first, so a clash with one of them is seen and the
        # new source lands after them.
        sources = org_sources(org)
        if any(s.key == key for s in sources):
            return _refuse({"label": [DUPLICATE]})
        order = max((s.order for s in sources), default=-1) + 1
        try:
            with transaction.atomic():
                source = LeadSource.objects.create(
                    org=org, key=key, label=label, order=order
                )
        except IntegrityError:
            return _refuse({"label": [DUPLICATE]})
        return Response(_row(source), status=status.HTTP_201_CREATED)


class LeadSourceDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Leads"],
        operation_id="lead_sources_update",
        request=SourceUpdateSerializer,
        responses={200: LeadSourceSerializer},
    )
    def patch(self, request, pk):
        if not may_configure(request.profile):
            return _forbidden()
        source = get_scoped_or_404(LeadSource, pk, request.profile.org)
        serializer = SourceUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return _refuse(serializer.errors)
        data = serializer.validated_data
        # The key never changes, so records keep pointing at it; a rename only
        # changes what people read.
        if "label" in data:
            label = " ".join(data["label"].split())
            if (
                LeadSource.objects.filter(org=source.org)
                .exclude(pk=source.pk)
                .filter(Q(label__iexact=label) | Q(key=derive_key(label)))
                .exists()
            ):
                return _refuse({"label": [DUPLICATE]})
            source.label = label
        if "is_active" in data:
            source.is_active = data["is_active"]
        source.save(update_fields=["label", "is_active", "updated_at"])
        return Response(_row(source))


class LeadSourceOrderView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Leads"],
        operation_id="lead_sources_order",
        request=SourceOrderSerializer,
        responses={200: _SOURCE_LIST},
    )
    @transaction.atomic
    def post(self, request):
        if not may_configure(request.profile):
            return _forbidden()
        org = request.profile.org
        serializer = SourceOrderSerializer(data=request.data)
        if not serializer.is_valid():
            return _refuse(serializer.errors)
        keys = serializer.validated_data["keys"]

        sources = org_sources(org)
        by_key = {s.key: s for s in sources}
        unknown = [k for k in keys if k not in by_key]
        if unknown or len(set(keys)) != len(keys):
            return _refuse(
                {"keys": ["Send each of this organisation's source keys once."]}
            )
        # Keys left out follow the listed ones in their current order.
        rest = [s.key for s in sources if s.key not in set(keys)]
        for position, key in enumerate(keys + rest):
            if by_key[key].order != position:
                by_key[key].order = position
                by_key[key].save(update_fields=["order", "updated_at"])
        ordered = sorted(by_key.values(), key=lambda s: s.order)
        return Response({"sources": [_row(s) for s in ordered]})
