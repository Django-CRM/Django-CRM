"""Authenticated NEXTTHOUSE campaign and attribution API."""

import hashlib
import json
import re

from django.db import IntegrityError, transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import HasOrgContext, IsOrgAdmin
from leads.models import (
    DataSubjectRequest,
    DataSubjectRequestEvent,
    LeadAttributionTouch,
    MarketingCampaign,
)
from leads.serializer import (
    DataSubjectRequestActionSerializer,
    DataSubjectRequestCreateSerializer,
    DataSubjectRequestSerializer,
    LeadAttributionCreateSerializer,
    LeadAttributionSerializer,
    MarketingCampaignSerializer,
)

IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._~-]{32,200}$")


class AcquisitionCursorPagination(CursorPagination):
    page_size = 50
    max_page_size = 100
    ordering = ("-created_at", "-id")


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_request_digest(validated):
    data = {
        "lead_ref": str(validated["lead"].id),
        "campaign_ref": (
            str(validated["campaign"].id) if validated.get("campaign") else None
        ),
        "touch_type": validated["touch_type"],
        "occurred_at": validated["occurred_at"].isoformat(),
        "source": validated["source"],
        "medium": validated.get("medium", ""),
        "campaign_key": validated.get("campaign_key", ""),
        "content_key": validated.get("content_key", ""),
        "term_key": validated.get("term_key", ""),
        "landing_page_ref": validated.get("landing_page_ref", ""),
        "referrer_domain": validated.get("referrer_domain", ""),
        "lawful_basis": validated["lawful_basis"],
        "privacy_notice_version": validated["privacy_notice_version"],
        "consent_evidence_ref": validated.get("consent_evidence_ref", ""),
    }
    return _digest(json.dumps(data, sort_keys=True, separators=(",", ":")))


class CampaignListCreateView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext, IsOrgAdmin)

    @extend_schema(
        tags=["NEXTTHOUSE Acquisition"],
        responses=MarketingCampaignSerializer(many=True),
    )
    def get(self, request):
        rows = MarketingCampaign.objects.filter(org=request.profile.org).order_by(
            "-created_at", "-id"
        )
        paginator = AcquisitionCursorPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(
            MarketingCampaignSerializer(page, many=True).data
        )

    @extend_schema(
        tags=["NEXTTHOUSE Acquisition"],
        request=MarketingCampaignSerializer,
        responses={201: MarketingCampaignSerializer},
    )
    def post(self, request):
        serializer = MarketingCampaignSerializer(
            data=request.data, context={"org": request.profile.org}
        )
        serializer.is_valid(raise_exception=True)
        campaign = serializer.save(
            org=request.profile.org, created_by=request.profile.user
        )
        return Response(MarketingCampaignSerializer(campaign).data, status=201)


class AttributionListCreateView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext, IsOrgAdmin)

    @extend_schema(
        tags=["NEXTTHOUSE Acquisition"], responses=LeadAttributionSerializer(many=True)
    )
    def get(self, request):
        rows = LeadAttributionTouch.objects.filter(org=request.profile.org).order_by(
            "-created_at", "-id"
        )
        paginator = AcquisitionCursorPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(
            LeadAttributionSerializer(page, many=True).data
        )

    @extend_schema(
        tags=["NEXTTHOUSE Acquisition"],
        request=LeadAttributionCreateSerializer,
        responses={200: LeadAttributionSerializer, 201: LeadAttributionSerializer},
    )
    def post(self, request):
        raw_key = request.headers.get("Idempotency-Key", "")
        if not IDEMPOTENCY_KEY.fullmatch(raw_key):
            return Response(
                {"code": "idempotency_key_invalid"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        key_digest = _digest(raw_key)
        existing = LeadAttributionTouch.objects.filter(
            org=request.profile.org, idempotency_key_digest=key_digest
        ).first()
        serializer = LeadAttributionCreateSerializer(
            data=request.data,
            context={
                "org": request.profile.org,
                "allow_existing_touch": existing is not None,
            },
        )
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data
        request_digest = _canonical_request_digest(validated)

        if existing is not None:
            if existing.request_digest != request_digest:
                return Response(
                    {"code": "idempotency_conflict"},
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(LeadAttributionSerializer(existing).data)

        try:
            with transaction.atomic():
                created = LeadAttributionTouch.objects.create(
                    **validated,
                    org=request.profile.org,
                    created_by=request.profile.user,
                    idempotency_key_digest=key_digest,
                    request_digest=request_digest,
                )
        except IntegrityError:
            existing = LeadAttributionTouch.objects.filter(
                org=request.profile.org, idempotency_key_digest=key_digest
            ).first()
            if existing is None or existing.request_digest != request_digest:
                return Response(
                    {"code": "idempotency_conflict"},
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(LeadAttributionSerializer(existing).data)

        return Response(LeadAttributionSerializer(created).data, status=201)


class DataSubjectRequestListCreateView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext, IsOrgAdmin)

    def get(self, request):
        rows = DataSubjectRequest.objects.filter(org=request.profile.org).order_by(
            "-created_at", "-id"
        )
        paginator = AcquisitionCursorPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(
            DataSubjectRequestSerializer(page, many=True).data
        )

    def post(self, request):
        raw_key = request.headers.get("Idempotency-Key", "")
        if not IDEMPOTENCY_KEY.fullmatch(raw_key):
            return Response({"code": "idempotency_key_invalid"}, status=400)
        serializer = DataSubjectRequestCreateSerializer(
            data=request.data, context={"org": request.profile.org}
        )
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        subject_ref_digest = _digest(f"{request.profile.org_id}:{values['lead'].id}")
        request_digest = _digest(
            json.dumps(
                {
                    "subject_ref_digest": subject_ref_digest,
                    "request_type": values["request_type"],
                    "due_at": values["due_at"].isoformat(),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        key_digest = _digest(raw_key)
        existing = DataSubjectRequest.objects.filter(
            org=request.profile.org, idempotency_key_digest=key_digest
        ).first()
        if existing:
            if existing.request_digest != request_digest:
                return Response({"code": "idempotency_conflict"}, status=409)
            return Response(DataSubjectRequestSerializer(existing).data)
        try:
            with transaction.atomic():
                row = DataSubjectRequest.objects.create(
                    org=request.profile.org,
                    subject_ref_digest=subject_ref_digest,
                    request_type=values["request_type"],
                    due_at=values["due_at"],
                    idempotency_key_digest=key_digest,
                    request_digest=request_digest,
                    created_by=request.profile.user,
                )
                DataSubjectRequestEvent.objects.create(
                    request=row,
                    org=request.profile.org,
                    sequence=1,
                    event_type="submitted",
                    actor_ref_digest=_digest(str(request.profile.user_id)),
                    created_by=request.profile.user,
                )
        except IntegrityError:
            row = DataSubjectRequest.objects.filter(
                org=request.profile.org, idempotency_key_digest=key_digest
            ).first()
            if row is None or row.request_digest != request_digest:
                return Response({"code": "idempotency_conflict"}, status=409)
            return Response(DataSubjectRequestSerializer(row).data)
        return Response(DataSubjectRequestSerializer(row).data, status=201)


class DataSubjectRequestActionView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext, IsOrgAdmin)

    def post(self, request, pk):
        serializer = DataSubjectRequestActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        command = serializer.validated_data
        with transaction.atomic():
            row = (
                DataSubjectRequest.objects.select_for_update()
                .filter(org=request.profile.org, pk=pk)
                .first()
            )
            if row is None:
                return Response({"code": "privacy_request_not_found"}, status=404)
            if row.version != command["expected_version"]:
                return Response({"code": "version_conflict"}, status=409)
            action = command["action"]
            if row.status == "rejected":
                return Response({"code": "request_terminal"}, status=409)
            transitions = {
                "verify_identity": ("verified", row.legal_hold, "identity_verified"),
                "place_legal_hold": (row.status, True, "legal_hold_placed"),
                "release_legal_hold": (row.status, False, "legal_hold_released"),
                "reject": ("rejected", row.legal_hold, "rejected"),
            }
            if action == "release_legal_hold" and not row.legal_hold:
                return Response({"code": "legal_hold_not_active"}, status=409)
            if action == "place_legal_hold" and row.legal_hold:
                return Response({"code": "legal_hold_already_active"}, status=409)
            if action == "verify_identity" and row.status != "submitted":
                return Response({"code": "identity_already_verified"}, status=409)
            row.status, row.legal_hold, event_type = transitions[action]
            row.version += 1
            row.updated_by = request.profile.user
            row.save(
                update_fields=[
                    "status",
                    "legal_hold",
                    "version",
                    "updated_by",
                    "updated_at",
                ]
            )
            DataSubjectRequestEvent.objects.create(
                request=row,
                org=request.profile.org,
                sequence=row.version,
                event_type=event_type,
                reason_code=command.get("reason_code", ""),
                evidence_ref_digest=(
                    _digest(command["evidence_ref"])
                    if command.get("evidence_ref")
                    else ""
                ),
                actor_ref_digest=_digest(str(request.profile.user_id)),
                created_by=request.profile.user,
            )
        return Response(DataSubjectRequestSerializer(row).data)
