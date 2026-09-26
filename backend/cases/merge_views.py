"""Case merge endpoint.

Merges a duplicate ("source") Case into a primary ("target") Case in a single
atomic transaction. Re-points comments, attachments, and inbound email
messages from source → target; sets `source.merged_into = target` and stamps
status=Duplicate / closed_on=today; emits MERGED on the source and
MERGE_TARGET on the target.
"""

from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.models import Case, EmailMessage
from cases.notifications import case_link
from cases.serializer import CaseSerializer, parent_access_context
from cases.signals import _create_activity
from common.models import Attachments, Comment
from common.permissions import HasOrgContext, is_org_admin


def _can_merge(profile, source: Case, target: Case) -> bool:
    """Admin OR the creator of BOTH cases."""
    if is_org_admin(profile):
        return True
    user = profile.user
    return source.created_by_id == user.id and target.created_by_id == user.id


class CaseMergeView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        operation_id="cases_merge",
        tags=["Cases"],
        responses={
            200: inline_serializer(
                name="CaseMergeResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "already_merged": serializers.BooleanField(required=False),
                    "target_case": CaseSerializer(),
                    "source_case_id": serializers.CharField(),
                    "redirected_url": serializers.CharField(),
                },
            )
        },
    )
    def post(self, request, pk: str, into_id: str, format=None):
        org = request.profile.org

        if pk == into_id:
            return Response(
                {"error": True, "errors": "Cannot merge a ticket into itself."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Lock both rows in a deterministic order to avoid deadlocks under
        # concurrent merges that touch overlapping pairs.
        ordered_ids = sorted([pk, into_id])
        with transaction.atomic():
            locked = list(
                Case.objects.select_for_update().filter(id__in=ordered_ids, org=org)
            )
            by_id = {str(c.id): c for c in locked}
            source = by_id.get(str(pk))
            target = by_id.get(str(into_id))

            if not source or not target:
                return Response(
                    {"error": True, "errors": "Ticket not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if not _can_merge(request.profile, source, target):
                return Response(
                    {
                        "error": True,
                        "errors": (
                            "You must be an admin, or the creator of both "
                            "tickets, to merge them."
                        ),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Idempotency: source already merged into the same target → no-op.
            if source.merged_into_id is not None:
                if str(source.merged_into_id) == str(target.id):
                    return Response(
                        {
                            "error": False,
                            "message": "Tickets already merged.",
                            "already_merged": True,
                            "target_case": CaseSerializer(
                                target,
                                context=parent_access_context(
                                    request.profile, [target]
                                ),
                            ).data,
                            "source_case_id": str(source.id),
                            "redirected_url": case_link(target.id),
                        },
                        status=status.HTTP_200_OK,
                    )
                return Response(
                    {
                        "error": True,
                        "errors": "Source ticket is already merged into a different ticket.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Reject chains: target itself is a duplicate of someone else.
            if target.merged_into_id is not None:
                return Response(
                    {
                        "error": True,
                        "errors": "Target ticket has already been merged elsewhere.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            case_ct = ContentType.objects.get_for_model(Case)

            # Capture the IDs of every row we relocate so unmerge can reverse
            # them cleanly. Stored on `source.merge_record` below.
            moved_comment_ids = list(
                Comment.objects.filter(
                    content_type=case_ct, object_id=source.id, org=org
                ).values_list("id", flat=True)
            )
            moved_attachment_ids = list(
                Attachments.objects.filter(
                    content_type=case_ct, object_id=source.id, org=org
                ).values_list("id", flat=True)
            )
            moved_email_ids = list(
                EmailMessage.objects.filter(case=source, org=org).values_list(
                    "id", flat=True
                )
            )

            comments_moved = Comment.objects.filter(id__in=moved_comment_ids).update(
                object_id=target.id
            )
            attachments_moved = Attachments.objects.filter(
                id__in=moved_attachment_ids
            ).update(object_id=target.id)
            emails_moved = EmailMessage.objects.filter(id__in=moved_email_ids).update(
                case=target
            )

            # Inherit the duplicate's thread keys onto the primary so future
            # inbound emails to the old thread land on the primary.
            target_alts = list(target.alt_thread_ids or [])
            source_alts = list(source.alt_thread_ids or [])
            inherited_candidates = source_alts + (
                [source.external_thread_id] if source.external_thread_id else []
            )
            inherited_added = []
            for tid in inherited_candidates:
                if tid and tid not in target_alts:
                    target_alts.append(tid)
                    inherited_added.append(tid)
            target.alt_thread_ids = target_alts
            target.save(update_fields=["alt_thread_ids", "updated_at"])

            now = timezone.now()
            prior_closed_on = source.closed_on.isoformat() if source.closed_on else None
            source.merge_record = {
                "target_id": str(target.id),
                "moved_comment_ids": [str(i) for i in moved_comment_ids],
                "moved_attachment_ids": [str(i) for i in moved_attachment_ids],
                "moved_email_ids": [str(i) for i in moved_email_ids],
                "inherited_thread_ids": inherited_added,
                "source_prior_status": source.status,
                "source_prior_closed_on": prior_closed_on,
            }
            source.merged_into = target
            source.merged_at = now
            source.merged_by = request.profile
            source.status = "Duplicate"
            source.closed_on = timezone.localdate()
            source.save(
                update_fields=[
                    "merge_record",
                    "merged_into",
                    "merged_at",
                    "merged_by",
                    "status",
                    "closed_on",
                    "updated_at",
                ]
            )

            _create_activity(
                target,
                "MERGE_TARGET",
                metadata={
                    "source_id": str(source.id),
                    "comments_moved": comments_moved,
                    "attachments_moved": attachments_moved,
                    "emails_moved": emails_moved,
                },
                actor=request.profile,
            )
            _create_activity(
                source,
                "MERGED",
                metadata={"merged_into": str(target.id)},
                actor=request.profile,
            )

        return Response(
            {
                "error": False,
                "message": "Tickets merged",
                "target_case": CaseSerializer(
                    target, context=parent_access_context(request.profile, [target])
                ).data,
                "source_case_id": str(source.id),
                "redirected_url": case_link(target.id),
            },
            status=status.HTTP_200_OK,
        )
