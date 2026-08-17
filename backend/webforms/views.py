"""Authenticated management API for web forms.

Routes (all under /api/webforms/):
    GET    /                  list the org's forms
    POST   /                  create (admin only)
    GET    /<id>/             retrieve
    PUT    /<id>/             update, including the whole field list (admin)
    DELETE /<id>/             delete (admin only)
    POST   /<id>/publish/     start accepting submissions (admin only)
    POST   /<id>/unpublish/   stop accepting submissions (admin only)
    GET    /<id>/submissions/ paginated submissions, accepted and rejected
    GET    /<id>/analytics/   views, submissions and conversion over 30 days

Read is open to every member of the org; write is admin only. Creating a form
mints something that writes leads into the org from an anonymous endpoint, so
the two halves are not the same risk. This is the split
`common/views/settings_views.py` already uses for API settings.
"""

import datetime

from django.db.models import Count
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.lookups import get_scoped_or_404
from common.permissions import HasOrgContext, is_org_admin
from webforms.constants import REQUIRED_LEAD_FIELD
from webforms.models import WebForm, WebFormDailyStat, WebFormSubmission
from webforms.serializers import (
    WebFormDetailSerializer,
    WebFormListSerializer,
    WebFormSubmissionSerializer,
)

ANALYTICS_WINDOW_DAYS = 30


def _admin_required():
    return Response(
        {"error": True, "errors": "Admin access required"},
        status=status.HTTP_403_FORBIDDEN,
    )


class WebFormBaseView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_form(self, request, pk):
        """The form, scoped to the caller's org.

        Another org's id answers 404 rather than 403, so the id space cannot
        be used to discover which forms exist elsewhere.
        """
        return get_scoped_or_404(WebForm, pk, request.profile.org)

    def serializer_context(self, request):
        return {"request": request, "org": request.profile.org}


class WebFormListCreateView(WebFormBaseView):
    def _totals(self, org):
        """Org-wide counts, over every form rather than the returned page.

        Both clients print "N published" beside the destination, and this list
        is paginated at PAGE_SIZE. A client counting its own page would be
        right until the org's eleventh form and quietly wrong afterwards.

        The submission window matches `WebFormAnalyticsView`, from the same
        constant, so the number on the list and the number on a form's own
        analytics cannot describe different spans of time.
        """
        start = timezone.now() - datetime.timedelta(days=ANALYTICS_WINDOW_DAYS)
        submissions = WebFormSubmission.objects.filter(org=org, created_at__gte=start)
        return {
            "count": WebForm.objects.filter(org=org).count(),
            "published": WebForm.objects.filter(org=org, is_published=True).count(),
            # `accepted_duplicate` counts here too: a returning visitor whose
            # details merged into an existing lead reached the org just as
            # surely as a new one, and excluding them would make a form that
            # mostly hears from repeat visitors look dead.
            "submissions_30d": submissions.filter(
                status__in=WebFormSubmission.ACCEPTED_STATUSES
            ).count(),
            "spam_30d": submissions.filter(
                status=WebFormSubmission.REJECTED_SPAM
            ).count(),
        }

    @extend_schema(tags=["Web forms"], responses=WebFormListSerializer(many=True))
    def get(self, request):
        queryset = (
            WebForm.objects.filter(org=request.profile.org)
            .annotate(
                submission_count=Count("submissions", distinct=True),
                field_count=Count("fields", distinct=True),
            )
            .order_by("-created_at")
        )
        paginator = LimitOffsetPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = WebFormListSerializer(
            page, many=True, context=self.serializer_context(request)
        )
        response = paginator.get_paginated_response(serializer.data)
        response.data["totals"] = self._totals(request.profile.org)
        return response

    @extend_schema(
        tags=["Web forms"],
        request=WebFormDetailSerializer,
        responses=WebFormDetailSerializer,
    )
    def post(self, request):
        if not is_org_admin(request.profile):
            return _admin_required()
        serializer = WebFormDetailSerializer(
            data=request.data, context=self.serializer_context(request)
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class WebFormDetailView(WebFormBaseView):
    @extend_schema(tags=["Web forms"], responses=WebFormDetailSerializer)
    def get(self, request, pk):
        form = self.get_form(request, pk)
        return Response(
            WebFormDetailSerializer(form, context=self.serializer_context(request)).data
        )

    @extend_schema(
        tags=["Web forms"],
        request=WebFormDetailSerializer,
        responses=WebFormDetailSerializer,
    )
    def put(self, request, pk):
        form = self.get_form(request, pk)
        if not is_org_admin(request.profile):
            return _admin_required()
        serializer = WebFormDetailSerializer(
            form,
            data=request.data,
            partial=True,
            context=self.serializer_context(request),
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @extend_schema(tags=["Web forms"])
    def delete(self, request, pk):
        form = self.get_form(request, pk)
        if not is_org_admin(request.profile):
            return _admin_required()
        form.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WebFormPublishView(WebFormBaseView):
    """Start accepting submissions.

    Validates the SOURCE state, not only the target. "published to published"
    is not a harmless no-op to wave through: it means the caller believes the
    form is in a state it is not.
    """

    @extend_schema(tags=["Web forms"])
    def post(self, request, pk):
        form = self.get_form(request, pk)
        if not is_org_admin(request.profile):
            return _admin_required()

        if form.is_published:
            return Response(
                {"error": True, "errors": "This form is already published."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not form.fields.filter(lead_field=REQUIRED_LEAD_FIELD).exists():
            return Response(
                {
                    "error": True,
                    "errors": (
                        "Add an email field before publishing. It is what lets "
                        "a repeat submission update the existing lead instead "
                        "of failing."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if form.success_mode == WebForm.SUCCESS_REDIRECT and not form.redirect_url:
            return Response(
                {
                    "error": True,
                    "errors": (
                        "This form redirects on success but has no redirect URL set."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        form.is_published = True
        form.save(update_fields=["is_published", "updated_at"])
        return Response({"error": False}, status=status.HTTP_200_OK)


class WebFormUnpublishView(WebFormBaseView):
    @extend_schema(tags=["Web forms"])
    def post(self, request, pk):
        form = self.get_form(request, pk)
        if not is_org_admin(request.profile):
            return _admin_required()

        if not form.is_published:
            return Response(
                {"error": True, "errors": "This form is not published."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        form.is_published = False
        form.save(update_fields=["is_published", "updated_at"])
        return Response({"error": False}, status=status.HTTP_200_OK)


class WebFormSubmissionListView(WebFormBaseView):
    @extend_schema(tags=["Web forms"], responses=WebFormSubmissionSerializer(many=True))
    def get(self, request, pk):
        form = self.get_form(request, pk)
        queryset = (
            WebFormSubmission.objects.filter(form=form, org=request.profile.org)
            .select_related("lead")
            .order_by("-created_at")
        )
        paginator = LimitOffsetPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = WebFormSubmissionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class WebFormAnalyticsView(WebFormBaseView):
    """Views, submissions and conversion over a fixed trailing 30 days.

    The window is not configurable. There is no caller for a range parameter
    today, and adding one later is cheap; guessing at it now is not.

    Submission counts are read from WebFormSubmission rather than denormalised
    onto WebFormDailyStat, so each number has exactly one source of truth.
    """

    @extend_schema(tags=["Web forms"])
    def get(self, request, pk):
        form = self.get_form(request, pk)
        today = timezone.localdate()
        start = today - datetime.timedelta(days=ANALYTICS_WINDOW_DAYS - 1)

        views_by_day = dict(
            WebFormDailyStat.objects.filter(
                form=form, org=request.profile.org, date__gte=start, date__lte=today
            ).values_list("date", "views")
        )

        submissions = WebFormSubmission.objects.filter(
            form=form, org=request.profile.org, created_at__date__gte=start
        )
        accepted_by_day = dict(
            submissions.filter(status__in=WebFormSubmission.ACCEPTED_STATUSES)
            .values_list("created_at__date")
            .annotate(n=Count("id"))
        )
        spam_by_day = dict(
            submissions.filter(status=WebFormSubmission.REJECTED_SPAM)
            .values_list("created_at__date")
            .annotate(n=Count("id"))
        )

        # Zero-filled in Python rather than in SQL: a day with no row at all is
        # a real zero, and a sparse series makes a chart lie about its shape.
        series = []
        total_views = total_accepted = total_spam = 0
        for offset in range(ANALYTICS_WINDOW_DAYS):
            day = start + datetime.timedelta(days=offset)
            views = views_by_day.get(day, 0)
            accepted = accepted_by_day.get(day, 0)
            spam = spam_by_day.get(day, 0)
            total_views += views
            total_accepted += accepted
            total_spam += spam
            series.append(
                {
                    "date": day.isoformat(),
                    "views": views,
                    "submissions": accepted,
                    "spam": spam,
                }
            )

        return Response(
            {
                "window_days": ANALYTICS_WINDOW_DAYS,
                "start": start.isoformat(),
                "end": today.isoformat(),
                "totals": {
                    "views": total_views,
                    "submissions": total_accepted,
                    "spam": total_spam,
                    # Guarded rather than computed blind: a brand new form has
                    # zero views and this is the first thing its page renders.
                    "conversion_rate": (
                        round(total_accepted / total_views, 4) if total_views else 0
                    ),
                },
                "series": series,
            }
        )
