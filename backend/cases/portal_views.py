"""The customer's half of a support case.

Every query carries both the org filter and the contact filter. RLS is the
safety net underneath; these two are the contract. `_my_cases` is the single
place that decides what "mine" means, so widening it to account-wide later is
one edit rather than an audit of every view.
"""

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from rest_framework import status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from cases.kb_text import snippet, text_match
from cases.models import Case, Solution
from cases.portal_serializers import (
    PortalCaseCreateSerializer,
    PortalCaseDetailSerializer,
    PortalCaseSerializer,
    PortalCommentCreateSerializer,
    PortalCommentSerializer,
    PortalSolutionDetailSerializer,
    PortalSolutionSerializer,
)
from common.models import Comment
from common.portal_auth import IsPortalContact, PortalContactAuthentication
from common.utils import STATUS_CHOICE

VALID_STATUSES = {choice[0] for choice in STATUS_CHOICE}

# Three, and not configurable. A deflection panel is a nudge beside a form, so
# the useful question is "is the answer already here", not "how many can I
# scroll". The agent picker is the surface that wants a tunable limit.
SUGGEST_LIMIT = 3

# Same reasoning as SUGGEST_LIMIT: a short list under an article people are
# already reading, not a second index of the knowledge base.
RELATED_LIMIT = 3


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

    def _published_articles(self, request):
        """The only definition of "an article a customer may read".

        Both conditions, not just `is_published`. The pairing is enforced on
        write by `SolutionSerializer.validate`, but that rule arrived after the
        model did, so rows created before it can be published drafts. Requiring
        `approved` here means such a row is never shown to a customer, and the
        first edit to it repairs the pair anyway.
        """
        return Solution.objects.filter(
            org=request.org, is_published=True, status="approved"
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


class PortalArticleListView(PortalBaseView, LimitOffsetPagination):
    def get(self, request):
        queryset = self._published_articles(request)

        # Narrowed from the visible set, never applied to Solution.objects. A
        # search box that reaches rows the list cannot is the same bug as a
        # missing org filter, just harder to notice.
        search = request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) | Q(description__icontains=search)
            )

        queryset = queryset.order_by("title")
        page = self.paginate_queryset(queryset, request, view=self)
        return Response(
            {
                "articles": PortalSolutionSerializer(page, many=True).data,
                "articles_count": self.count,
            }
        )


class PortalArticleDetailView(PortalBaseView):
    def get(self, request, pk):
        article = self._published_articles(request).filter(pk=pk).first()
        if article is None:
            # Same response for "no such article" and "not published yet", for
            # the reason `_not_found` gives about cases: telling them apart
            # confirms that a draft with this id exists.
            return Response(
                {"error": "Article not found"}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(
            {
                "article": PortalSolutionDetailSerializer(article).data,
                "related": self._related(request, article),
            }
        )

    def _related(self, request, article):
        """Other articles the agents filed under the same tags.

        The tag vocabulary is shared with leads and deals and reads like
        "At Risk" and "VIP", so it is used to *find* these rows and never
        appears in the response. Ids and titles only.

        Built from `_published_articles`, so a shared tag cannot reach a draft
        or another org's article: relatedness narrows the visible set, it never
        widens it.
        """
        tag_ids = list(article.tags.values_list("id", flat=True))
        if not tag_ids:
            return []

        siblings = (
            self._published_articles(request)
            .filter(tags__id__in=tag_ids)
            .exclude(pk=article.pk)
            .distinct()
            .order_by("-updated_at")[:RELATED_LIMIT]
        )
        return [{"id": str(s.id), "title": s.title} for s in siblings]


class PortalArticleSuggestView(PortalBaseView):
    """Deflection, at the moment the customer is about to file a request.

    Query-driven rather than case-driven: the agent suggester hangs off an
    existing case, and here there is not one yet. That is the whole reason this
    is a separate endpoint rather than a reuse of `SolutionSuggestionsView`.
    """

    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        if not q:
            # No seeding from recent articles, unlike the agent side. An agent
            # scanning the newest answers is doing their job; a customer shown
            # three unrelated articles just learns to ignore the panel.
            return Response({"articles": []})

        results = (
            self._published_articles(request)
            .filter(text_match(q))
            .order_by("-updated_at")[:SUGGEST_LIMIT]
        )
        return Response(
            {
                "articles": [
                    {
                        "id": str(article.id),
                        "title": article.title,
                        "snippet": snippet(article.description),
                    }
                    for article in results
                ]
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
