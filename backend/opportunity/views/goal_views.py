from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import HasOrgContext, is_org_admin
from common.validators import uuid_param
from opportunity.models import SalesGoal
from opportunity.serializer import SalesGoalCreateSerializer, SalesGoalSerializer


def _visible_to(profile):
    """Q() describing which goals a non-admin profile may see.

    One definition, used by the list query and by the leaderboard, because they
    disagreed before: the list narrowed a non-admin to their own goals and their
    teams' while the leaderboard declared only `IsAuthenticated` and
    `HasOrgContext` and scoped nothing. A member whose own list came back empty
    could still read every colleague's target, attainment and email off
    `/goals/leaderboard/`. `SalesGoalDetailView.get` refuses the same person the
    same goal with a 403, so all three now agree.

    Scope: this only ever narrows within one org. Every caller has already
    filtered on `org=request.profile.org`, and RLS is underneath that. It is not
    a substitute for either.
    """
    return Q(assigned_to=profile) | Q(team__in=profile.user_teams.all())


def _sees_every_goal(request):
    return is_org_admin(request.profile) or request.user.is_superuser


class SalesGoalListView(APIView, LimitOffsetPagination):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_queryset(self, request):
        org = request.profile.org
        queryset = SalesGoal.objects.filter(org=org)

        if not _sees_every_goal(request):
            queryset = queryset.filter(_visible_to(request.profile))

        params = request.query_params
        if params.get("active") == "true":
            queryset = queryset.filter(is_active=True)
        if params.get("current") == "true":
            today = timezone.localdate()
            queryset = queryset.filter(period_start__lte=today, period_end__gte=today)
        assigned_to = uuid_param(params, "assigned_to")
        if assigned_to:
            queryset = queryset.filter(assigned_to_id=assigned_to)
        team = uuid_param(params, "team")
        if team:
            queryset = queryset.filter(team_id=team)
        if params.get("period_type"):
            queryset = queryset.filter(period_type=params["period_type"])
        if params.get("search"):
            queryset = queryset.filter(name__icontains=params["search"])

        return queryset.select_related(
            "assigned_to", "assigned_to__user", "team"
        ).distinct()

    def get(self, request, *args, **kwargs):
        queryset = self.get_queryset(request)
        results = self.paginate_queryset(queryset, request, view=self)
        # Without this the serializer's progress fields run one aggregate query
        # per goal, and the web list asks for up to 1000 of them in a page.
        SalesGoal.attach_progress(results)
        serializer = SalesGoalSerializer(results, many=True)

        total_count = self.count
        next_offset = self.offset + len(results) if results else None
        offset = next_offset if (results and next_offset < total_count) else None

        return Response(
            {
                "goals": serializer.data,
                "goals_count": total_count,
                "offset": offset,
                "per_page": self.get_limit(request),
            }
        )

    def post(self, request, *args, **kwargs):
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": True, "errors": "Only admins can create goals."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = SalesGoalCreateSerializer(
            data=request.data, context={"request": request}
        )
        if serializer.is_valid():
            serializer.save(
                org=request.profile.org,
                created_by=request.profile.user,
            )
            return Response(
                {"error": False, "message": "Goal Created Successfully"},
                status=status.HTTP_201_CREATED,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class SalesGoalDetailView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def get_object(self, pk, request):
        return SalesGoal.objects.filter(id=pk, org=request.profile.org).first()

    def get(self, request, pk, *args, **kwargs):
        goal = self.get_object(pk, request)
        if not goal:
            return Response(
                {"error": True, "errors": "Goal not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        # Evaluated as a queryset filter rather than by hand in Python so that
        # it is literally the same predicate the list and the leaderboard use.
        # Writing it out separately is how the three drifted apart.
        if not _sees_every_goal(request) and not (
            SalesGoal.objects.filter(pk=goal.pk)
            .filter(_visible_to(request.profile))
            .exists()
        ):
            return Response(
                {"error": True, "errors": "You do not have permission."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = SalesGoalSerializer(goal)
        return Response(serializer.data)

    def put(self, request, pk, *args, **kwargs):
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": True, "errors": "Only admins can update goals."},
                status=status.HTTP_403_FORBIDDEN,
            )
        goal = self.get_object(pk, request)
        if not goal:
            return Response(
                {"error": True, "errors": "Goal not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = SalesGoalCreateSerializer(
            goal, data=request.data, partial=True, context={"request": request}
        )
        if serializer.is_valid():
            serializer.save()
            return Response(
                {"error": False, "message": "Goal Updated Successfully"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"error": True, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    def delete(self, request, pk, *args, **kwargs):
        if not is_org_admin(request.profile) and not request.user.is_superuser:
            return Response(
                {"error": True, "errors": "Only admins can delete goals."},
                status=status.HTTP_403_FORBIDDEN,
            )
        goal = self.get_object(pk, request)
        if not goal:
            return Response(
                {"error": True, "errors": "Goal not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        goal.delete()
        return Response(
            {"error": False, "message": "Goal Deleted Successfully"},
            status=status.HTTP_200_OK,
        )


class SalesGoalLeaderboardView(APIView):
    """Current individual goals for one period, ranked by attainment.

    Narrowed by `_visible_to` for a non-admin, the same predicate the list uses.
    A ranking is not a way around the rule that a member does not read a
    colleague's quota: before this, a member's own list came back empty while
    this endpoint handed them every row in the org.

    Ranks are assigned after narrowing, so a member sees their standing within
    what they may see rather than a position in a table they cannot read. A
    leaderboard of one is the honest answer for someone with a single goal and
    no team.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    def get(self, request, *args, **kwargs):
        org = request.profile.org
        today = timezone.localdate()

        period_type = request.query_params.get("period_type", "MONTHLY")

        goals = SalesGoal.objects.filter(
            org=org,
            is_active=True,
            period_type=period_type,
            period_start__lte=today,
            period_end__gte=today,
            assigned_to__isnull=False,
        ).select_related("assigned_to", "assigned_to__user")

        if not _sees_every_goal(request):
            goals = goals.filter(_visible_to(request.profile)).distinct()

        goals = SalesGoal.attach_progress(goals)

        leaderboard = []
        for goal in goals:
            # compute_progress() results are cached on the instance
            progress = goal.compute_progress()
            if goal.target_value and goal.target_value != 0:
                percent = min(
                    int(float(progress) / float(goal.target_value) * 100), 100
                )
            else:
                percent = 0
            leaderboard.append(
                {
                    "goal_id": str(goal.id),
                    "goal_name": goal.name,
                    # `User.name` is non-empty by construction (`User.save`
                    # falls back to the email local-part on first save), and
                    # this used to put the full email in the `name` slot and
                    # again in an `email` one, so both clients printed raw
                    # addresses in a ranked list. The address is not needed to
                    # name somebody, and a board that is now narrowed by
                    # `_visible_to` should not be the widest thing on the
                    # payload, so it is gone rather than merely unused.
                    "user": {
                        "id": str(goal.assigned_to.id),
                        "name": goal.assigned_to.user.name
                        or goal.assigned_to.user.email,
                    },
                    "target": float(goal.target_value),
                    "achieved": float(progress),
                    "percent": percent,
                }
            )

        leaderboard.sort(key=lambda x: x["percent"], reverse=True)

        for i, entry in enumerate(leaderboard, 1):
            entry["rank"] = i

        return Response({"leaderboard": leaderboard})


class SalesGoalHistoryView(APIView):
    """Finished goal periods with what each one actually attained.

    The list endpoint answers "how are we doing now"; nothing answered "how did
    we do". Goals whose period has closed stay queryable there, but only one at
    a time and with no rollup, so a manager could not see attainment move across
    quarters.

    Progress is recomputed from the won deals of each closed period rather than
    read from a stored snapshot. Those deals are themselves history: their
    `closed_on` is in the past and cannot move into or out of a finished window,
    so the recomputed number is the same one a snapshot would have frozen, and
    it costs no extra table to keep correct.

    Narrowed by `_visible_to` for a non-admin, the same predicate the list and
    the leaderboard use. A member reads their own attainment history, not the
    org's.
    """

    permission_classes = (IsAuthenticated, HasOrgContext)

    #: Periods returned when the caller does not ask for a number, and the most
    #: it will return at once. The window bounds the deal scan behind
    #: `attach_progress`: without it, an org in its fifth year would load every
    #: won deal it has ever had to render one page.
    DEFAULT_PERIODS = 12
    MAX_PERIODS = 60

    def _requested_periods(self, request):
        raw = request.query_params.get("periods")
        if not raw:
            return self.DEFAULT_PERIODS
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return self.DEFAULT_PERIODS
        return max(1, min(value, self.MAX_PERIODS))

    def get(self, request, *args, **kwargs):
        org = request.profile.org
        today = timezone.localdate()
        limit = self._requested_periods(request)

        finished = SalesGoal.objects.filter(org=org, period_end__lt=today)
        if not _sees_every_goal(request):
            finished = finished.filter(_visible_to(request.profile)).distinct()

        # A row is a period AND a goal type, never a period alone. Grouping on
        # the period by itself pooled a revenue target in currency with a
        # deals-closed target in deals and printed the sum as money: a month
        # holding a $340,000 quota and an 18-deal quota reported a target of
        # $340,018. Totals only mean anything within one unit.
        #
        # Resolved before the goals are read so that both the goal query and the
        # deal scan behind `attach_progress` stay bounded.
        windows = list(
            finished.values_list(
                "period_start", "period_end", "period_type", "goal_type"
            )
            .distinct()
            .order_by("-period_end", "-period_start", "goal_type")[:limit]
        )
        if not windows:
            return Response({"history": [], "periods_returned": 0})

        goals = list(
            finished.filter(
                period_start__gte=min(w[0] for w in windows),
                period_end__lte=max(w[1] for w in windows),
            )
            .select_related("assigned_to", "assigned_to__user", "team")
            .order_by("name")
        )
        SalesGoal.attach_progress(goals)

        by_window = {window: [] for window in windows}
        for goal in goals:
            key = (
                goal.period_start,
                goal.period_end,
                goal.period_type,
                goal.goal_type,
            )
            if key in by_window:
                by_window[key].append(goal)

        history = []
        for window in windows:
            start, end, period_type, goal_type = window
            window_goals = by_window[window]
            target = sum((g.target_value for g in window_goals), Decimal("0"))
            achieved = sum((g.compute_progress() for g in window_goals), Decimal("0"))
            history.append(
                {
                    "period_start": start,
                    "period_end": end,
                    "period_type": period_type,
                    "goal_type": goal_type,
                    "goals_count": len(window_goals),
                    # Attainment is per goal, not per pooled total: three reps,
                    # two of whom missed while the third doubled up, is not the
                    # same story as "the team made its number".
                    "attained_count": sum(
                        1
                        for g in window_goals
                        if g.target_value and g.compute_progress() >= g.target_value
                    ),
                    "target": float(target),
                    "achieved": float(achieved),
                    # Uncapped, unlike the live `progress_percent`. A settled
                    # period that came in 25% over is a different result from one
                    # that landed exactly on target, and capping made the two
                    # read identically in the one view where the difference is
                    # final.
                    "percent": (int(achieved / target * 100) if target else 0),
                    "goals": SalesGoalSerializer(window_goals, many=True).data,
                }
            )

        return Response({"history": history, "periods_returned": len(history)})
