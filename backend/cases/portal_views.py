"""The customer's half of a support case.

Every query carries both the org filter and the contact filter. RLS is the
safety net underneath; these two are the contract. `_my_cases` is the single
place that decides what "mine" means, so widening it to account-wide later is
one edit rather than an audit of every view.
"""

from django.contrib.contenttypes.models import ContentType
from rest_framework import status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.models import Case
from cases.portal_serializers import (
    PortalCaseCreateSerializer,
    PortalCaseDetailSerializer,
    PortalCaseSerializer,
    PortalCommentCreateSerializer,
    PortalCommentSerializer,
)
from common.models import Comment
from common.portal_auth import IsPortalContact, PortalContactAuthentication
from common.utils import STATUS_CHOICE

VALID_STATUSES = {choice[0] for choice in STATUS_CHOICE}


class PortalBaseView(APIView):
    # Declared here rather than inherited from settings, on purpose. The portal
    # credential is deliberately absent from DEFAULT_AUTHENTICATION_CLASSES,
    # because that setting applies to every view in the project.
    authentication_classes = (PortalContactAuthentication,)
    permission_classes = (IsPortalContact,)

    def _my_cases(self, request):
        """The only definition of "my cases" in the portal.

        Contact-scoped, not account-scoped: a colleague at the same company
        cannot read a ticket they were not put on. Widening is a migration,
        narrowing later would be a breach notification.
        """
        return Case.objects.filter(
            org=request.org, contacts=request.portal_contact, is_active=True
        )

    def _case_or_none(self, request, pk):
        return self._my_cases(request).filter(pk=pk).first()

    def _not_found(self):
        """One response for "does not exist" and "not yours".

        Telling the two apart would confirm that a case id belongs to somebody
        else in this org.
        """
        return Response({"error": "Case not found"}, status=status.HTTP_404_NOT_FOUND)


class PortalCaseListView(PortalBaseView, LimitOffsetPagination):
    def get(self, request):
        queryset = self._my_cases(request)

        requested_status = request.query_params.get("status")
        if requested_status is not None:
            if requested_status not in VALID_STATUSES:
                return Response(
                    {"error": "Unknown status."}, status=status.HTTP_400_BAD_REQUEST
                )
            queryset = queryset.filter(status=requested_status)

        queryset = queryset.order_by("-created_at")
        page = self.paginate_queryset(queryset, request, view=self)
        return Response(
            {
                "cases": PortalCaseSerializer(page, many=True).data,
                "cases_count": self.count,
            }
        )

    def post(self, request):
        payload = PortalCaseCreateSerializer(data=request.data)
        if not payload.is_valid():
            return Response(payload.errors, status=status.HTTP_400_BAD_REQUEST)

        # org, status and the contact link are all set here rather than read
        # from the body. The serializer does not declare them, so a body that
        # supplies them is ignored rather than honoured.
        case = Case.objects.create(
            org=request.org,
            status="New",
            **payload.validated_data,
        )
        case.contacts.add(request.portal_contact)
        return Response(
            {"case": PortalCaseDetailSerializer(case).data},
            status=status.HTTP_201_CREATED,
        )


class PortalCaseDetailView(PortalBaseView):
    def get(self, request, pk):
        case = self._case_or_none(request, pk)
        if case is None:
            return self._not_found()

        # is_internal=False is applied in the query, not in the serializer.
        # An internal note is never loaded, so it cannot be leaked by a later
        # change to how the response is rendered.
        comments = Comment.objects.filter(
            org=request.org,
            content_type=ContentType.objects.get_for_model(Case),
            object_id=case.id,
            is_internal=False,
        ).order_by("commented_on")

        return Response(
            {
                "case": PortalCaseDetailSerializer(case).data,
                "comments": PortalCommentSerializer(
                    comments,
                    many=True,
                    context={"portal_contact": request.portal_contact},
                ).data,
            }
        )


class PortalCaseCommentView(PortalBaseView):
    def post(self, request, pk):
        case = self._case_or_none(request, pk)
        if case is None:
            return self._not_found()

        payload = PortalCommentCreateSerializer(data=request.data)
        if not payload.is_valid():
            return Response(payload.errors, status=status.HTTP_400_BAD_REQUEST)

        comment = Comment.objects.create(
            org=request.org,
            content_type=ContentType.objects.get_for_model(Case),
            object_id=case.id,
            comment=payload.validated_data["comment"],
            commented_by_contact=request.portal_contact,
            is_internal=False,
        )
        return Response(
            {
                "comment": PortalCommentSerializer(
                    comment, context={"portal_contact": request.portal_contact}
                ).data
            },
            status=status.HTTP_201_CREATED,
        )
