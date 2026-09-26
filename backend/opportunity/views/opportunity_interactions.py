from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.lookups import get_on_visible_record_or_404
from common.models import Attachments, Comment
from common.permissions import HasOrgContext, is_org_admin
from common.serializer import CommentSerializer
from opportunity import swagger_params
from opportunity.access import visible_deals_qs
from opportunity.serializer import OpportunityCommentEditSwaggerSerializer


class OpportunityCommentView(APIView):
    model = Comment
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk):
        """404, like a missing id, for a comment on a deal the caller cannot open."""
        return get_on_visible_record_or_404(
            self.model,
            pk,
            self.request.profile.org,
            visible_deals_qs(self.request.profile),
        )

    @extend_schema(
        tags=["Opportunities"],
        parameters=swagger_params.organization_params,
        request=OpportunityCommentEditSwaggerSerializer,
        responses={
            200: inline_serializer(
                name="OpportunityCommentUpdateResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def put(self, request, pk, format=None):
        params = request.data
        obj = self.get_object(pk)
        if is_org_admin(request.profile) or request.profile == obj.commented_by:
            # No `if params.get("comment")` guard here: a body with a blank or
            # absent `comment` used to fall out of this branch and hit the 403
            # below, which told an author they may not edit their own comment.
            # `comment` is a non-blank CharField, so the serializer answers
            # with a 400 naming the field.
            serializer = CommentSerializer(obj, data=params)
            if serializer.is_valid():
                serializer.save()
                return Response(
                    {"error": False, "message": "Comment Submitted"},
                    status=status.HTTP_200_OK,
                )
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "error": True,
                "errors": "You don't have permission to perform this action.",
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    @extend_schema(
        tags=["Opportunities"],
        parameters=swagger_params.organization_params,
        request=OpportunityCommentEditSwaggerSerializer,
        description="Partial Comment Update",
        responses={
            200: inline_serializer(
                name="OpportunityCommentPatchResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def patch(self, request, pk, format=None):
        """Handle partial updates to a comment."""
        params = request.data
        obj = self.get_object(pk)
        if is_org_admin(request.profile) or request.profile == obj.commented_by:
            serializer = CommentSerializer(obj, data=params, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response(
                    {"error": False, "message": "Comment Updated"},
                    status=status.HTTP_200_OK,
                )
            return Response(
                {"error": True, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "error": True,
                "errors": "You don't have permission to perform this action.",
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    @extend_schema(
        tags=["Opportunities"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="OpportunityCommentDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, format=None):
        self.object = self.get_object(pk)
        if is_org_admin(request.profile) or request.profile == self.object.commented_by:
            self.object.delete()
            return Response(
                {"error": False, "message": "Comment Deleted Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {
                "error": True,
                "errors": "You do not have permission to perform this action",
            },
            status=status.HTTP_403_FORBIDDEN,
        )


class OpportunityAttachmentView(APIView):
    model = Attachments
    permission_classes = (IsAuthenticated, HasOrgContext)

    @extend_schema(
        tags=["Opportunities"],
        parameters=swagger_params.organization_params,
        responses={
            200: inline_serializer(
                name="OpportunityAttachmentDeleteResponse",
                fields={
                    "error": serializers.BooleanField(),
                    "message": serializers.CharField(),
                },
            )
        },
    )
    def delete(self, request, pk, format=None):
        # Two defects here, not one.
        #
        # `objects.get(pk=pk)` had no org filter, so an admin of any org could
        # delete any attachment in the system by id, and a missing or malformed
        # id raised out of the view as a 500 rather than answering 404. The
        # comment view above already scopes its lookup.
        #
        # And `request.profile == self.object.created_by` compared a `Profile`
        # to a `User`: `created_by` is a FK to `common.User` via
        # `UserAuditModel`. That is never equal, so the uploader branch was dead
        # and a non-admin could not delete the attachment they had just
        # uploaded. The leads twin already spells it `request.profile.user`.
        # Repairing the lookup without repairing the comparison would have left
        # the fix looking complete while the branch stayed unreachable.
        # And on a deal the caller cannot open, the same 404 as a missing id.
        self.object = get_on_visible_record_or_404(
            self.model,
            pk,
            request.profile.org,
            visible_deals_qs(request.profile),
        )
        if (
            is_org_admin(request.profile)
            or request.profile.user == self.object.created_by
        ):
            self.object.delete()
            return Response(
                {"error": False, "message": "Attachment Deleted Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {
                "error": True,
                "errors": "You don't have permission to perform this action.",
            },
            status=status.HTTP_403_FORBIDDEN,
        )
