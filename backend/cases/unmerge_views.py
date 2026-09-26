"""Case unmerge endpoint.

Reverses a prior merge using the audit blob written to
`source.merge_record` at merge time. Moves comments / attachments / inbound
emails back from target → source (filtering by the IDs we captured), strips
the inherited thread keys off the target's `alt_thread_ids`, and restores
the source's pre-merge `status` and `closed_on`. Emits UNMERGED on the
source and UNMERGE_TARGET on the target.

Permission model matches merge: admin OR the creator of both cases.
"""

from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils.dateparse import parse_date
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.merge_views import _can_merge
from cases.models import Case, EmailMessage
from cases.serializer import CaseSerializer, parent_access_context
from cases.signals import _create_activity
from common.models import Attachments, Comment
from common.permissions import HasOrgContext


class CaseUnmergeView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        operation_id="cases_unmerge",
        tags=["Cases"],
        responses={
            200: inline_serializer(
                name="CaseUnmergeResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "source_case": CaseSerializer(),
                },
            )
        },
    )
    def post(self, request, pk: str, format=None):
        org = request.profile.org

        with transaction.atomic():
            try:
                source = Case.objects.select_for_update().get(id=pk, org=org)
            except Case.DoesNotExist:
                return Response(
                    {"error": True, "errors": "Ticket not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if source.merged_into_id is None:
                return Response(
                    {"error": True, "errors": "This ticket is not currently merged."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            record = source.merge_record or {}
            if not record or not record.get("target_id"):
                return Response(
                    {
                        "error": True,
                        "errors": (
                            "Unmerge metadata is missing for this ticket (it "
                            "was merged before unmerge tracking was added). "
                            "Please restore manually."
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                target = Case.objects.select_for_update().get(
                    id=source.merged_into_id, org=org
                )
            except Case.DoesNotExist:
                return Response(
                    {
                        "error": True,
                        "errors": "Target ticket no longer exists; cannot unmerge.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not _can_merge(request.profile, source, target):
                return Response(
                    {
                        "error": True,
                        "errors": (
                            "You must be an admin, or the creator of both "
                            "tickets, to unmerge them."
                        ),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            case_ct = ContentType.objects.get_for_model(Case)
            comment_ids = record.get("moved_comment_ids") or []
            attachment_ids = record.get("moved_attachment_ids") or []
            email_ids = record.get("moved_email_ids") or []
            inherited_ids = record.get("inherited_thread_ids") or []

            # Move content back. Filter on (id, current parent=target) so
            # rows that have since moved elsewhere or been deleted are
            # silently skipped.
            comments_restored = Comment.objects.filter(
                id__in=comment_ids,
                content_type=case_ct,
                object_id=target.id,
                org=org,
            ).update(object_id=source.id)
            attachments_restored = Attachments.objects.filter(
                id__in=attachment_ids,
                content_type=case_ct,
                object_id=target.id,
                org=org,
            ).update(object_id=source.id)
            emails_restored = EmailMessage.objects.filter(
                id__in=email_ids, case=target, org=org
            ).update(case=source)

            # Remove only the thread IDs we added during merge.
            target_alts = [
                tid for tid in (target.alt_thread_ids or []) if tid not in inherited_ids
            ]
            target.alt_thread_ids = target_alts
            target.save(update_fields=["alt_thread_ids", "updated_at"])

            prior_status = record.get("source_prior_status") or "New"
            prior_closed_on_raw = record.get("source_prior_closed_on")
            prior_closed_on = (
                parse_date(prior_closed_on_raw) if prior_closed_on_raw else None
            )

            target_id_for_activity = str(target.id)
            source.merged_into = None
            source.merged_at = None
            source.merged_by = None
            source.status = prior_status
            source.closed_on = prior_closed_on
            source.merge_record = None
            source.save(
                update_fields=[
                    "merged_into",
                    "merged_at",
                    "merged_by",
                    "status",
                    "closed_on",
                    "merge_record",
                    "updated_at",
                ]
            )

            _create_activity(
                source,
                "UNMERGED",
                metadata={
                    "from_target_id": target_id_for_activity,
                    "comments_restored": comments_restored,
                    "attachments_restored": attachments_restored,
                    "emails_restored": emails_restored,
                },
                actor=request.profile,
            )
            _create_activity(
                target,
                "UNMERGE_TARGET",
                metadata={
                    "source_id": str(source.id),
                    "comments_restored": comments_restored,
                    "attachments_restored": attachments_restored,
                    "emails_restored": emails_restored,
                },
                actor=request.profile,
            )

        return Response(
            {
                "error": False,
                "message": "Tickets unmerged",
                "source_case": CaseSerializer(
                    source, context=parent_access_context(request.profile, [source])
                ).data,
            },
            status=status.HTTP_200_OK,
        )
