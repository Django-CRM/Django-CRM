"""The per-user task calendar feed (G14): managing it, and serving it.

Two halves with opposite trust models.

``CalendarFeedView`` (``/api/profile/calendar-feed/``) is the signed-in member
managing their own feed. It is on the credential deny-list in
``common/scopes.py``, so no personal access token and not the org API key can
read or mint a feed URL: the URL is itself a standing credential, and minting
one from a token would outlive revoking that token. Every query filters on
``org=request.profile.org`` AND ``profile=request.profile``. Enabling,
regenerating and disabling each write one ``CALENDAR_FEED_*`` row to the org's
security audit log.

``PublicCalendarFeedView`` (``/api/public/calendar/<token>.ics``) is what a
calendar app polls. It takes no credential except the token in the path.

READ THE ORDER BEFORE CHANGING IT, for the reason ``cases/help_center_views.py``
gives: ``task`` is under RLS, so it reads nothing until the context is set. The
token row (not under RLS, see ``CalendarFeedToken``) names the profile; the
profile names the org; the context is set from that org; only then are tasks
read. The feed is the member's own tasks (ones they created or are assigned),
for everyone including admins, whose org-wide view would bury their own work.
It is also narrowed by ``visible_tasks_qs``, so it never shows a task the
member could not open in the app today.
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from common.audit_log import audit_log
from common.calendar_feed import FEED_PATH_PREFIX, render_calendar
from common.links import api_url, frontend_url
from common.models import CalendarFeedToken, Profile
from common.org_time import activate_org_timezone
from common.permissions import HasOrgContext
from common.request_meta import client_ip
from common.tasks import set_rls_context
from tasks.access import visible_tasks_qs

# What the feed carries, all owner decisions: open tasks only, due from 90 days
# ago (so recent overdue work is still on the calendar) to a year ahead, and a
# hard cap so one feed can never become an unbounded export. The cap keeps the
# tasks nearest today: upcoming ones soonest first, then overdue ones most
# recent first.
OPEN_STATUSES = ("New", "In Progress")
PAST_DAYS = 90
FUTURE_DAYS = 365
MAX_EVENTS = 1000

# How stale `last_used_at` may get before a fetch writes it again.
TOUCH_INTERVAL = timedelta(hours=1)

# One body for every miss: a malformed token, an unknown one, a regenerated or
# disabled one, and one whose member, user or org is no longer active. Telling
# any two apart would tell a stranger holding an old URL something about the
# account behind it.
NOT_FOUND = {"error": "Not found"}


def _state(feed):
    return {
        "error": False,
        "enabled": feed is not None,
        "created_at": feed.created_at if feed else None,
        "last_used_at": feed.last_used_at if feed else None,
    }


class CalendarFeedView(APIView):
    permission_classes = (IsAuthenticated, HasOrgContext)

    def _mine(self, request):
        return CalendarFeedToken.objects.filter(
            org=request.profile.org, profile=request.profile
        )

    @extend_schema(tags=["Calendar feed"], operation_id="calendar_feed_status")
    def get(self, request):
        return Response(_state(self._mine(request).first()))

    @extend_schema(
        tags=["Calendar feed"],
        operation_id="calendar_feed_issue",
        request=None,
        description=(
            "Create the feed, or replace it. Any previous URL stops working. "
            "The response carries `url` once; it cannot be read back."
        ),
    )
    def post(self, request):
        with transaction.atomic():
            # The lock `issue` takes, taken first, so that of two racing
            # requests the second sees the first's row and is audited as the
            # regenerate it is.
            Profile.objects.select_for_update().filter(pk=request.profile.pk).first()
            replaced = self._mine(request).exists()
            raw, feed = CalendarFeedToken.issue(request.profile)
        audit_log.calendar_feed(
            "CALENDAR_FEED_REGENERATED" if replaced else "CALENDAR_FEED_ENABLED",
            request.user,
            request.profile.org,
            request,
        )
        data = _state(feed)
        # Not from the request: the web app asks from its own server, whose
        # view of this host is internal (see `common.links.api_url`).
        data["url"] = api_url(f"{FEED_PATH_PREFIX}{raw}.ics")
        return Response(data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=["Calendar feed"], operation_id="calendar_feed_disable")
    def delete(self, request):
        deleted, _ = self._mine(request).delete()
        # Disabling a feed that is already off changes nothing and is not logged.
        if deleted:
            audit_log.calendar_feed(
                "CALENDAR_FEED_DISABLED", request.user, request.profile.org, request
            )
        return Response(_state(None))


def _token_ident(view):
    return CalendarFeedToken.hash_token(str(view.kwargs.get("token", "")))


class CalendarFeedIPThrottle(SimpleRateThrottle):
    """Per-address limit, bucketed on `client_ip`.

    Generous on purpose: calendar services fetch from shared addresses, so one
    Google or Microsoft fetcher can carry many users' feeds at once.
    """

    scope = "calendar_feed_ip"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": client_ip(request) or "unknown",
        }


class CalendarFeedTokenThrottle(SimpleRateThrottle):
    """Per-feed limit, bucketed on the token's hash (never the raw token)."""

    scope = "calendar_feed_token"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": _token_ident(view)}


class _AlwaysJSON(BaseContentNegotiation):
    """Answer errors in JSON whatever the client asked for.

    A calendar app sends `Accept: text/calendar`, which no DRF renderer offers,
    so default negotiation turned every fetch, the successful ones included,
    into a 406. The feed itself is a plain `HttpResponse` and never rendered.
    """

    def select_parser(self, request, parsers):
        return parsers[0]

    def select_renderer(self, request, renderers, format_suffix=None):
        return (renderers[0], renderers[0].media_type)


class PublicCalendarFeedView(APIView):
    authentication_classes: list = []
    permission_classes = (AllowAny,)
    throttle_classes = [CalendarFeedIPThrottle, CalendarFeedTokenThrottle]
    renderer_classes = [JSONRenderer]
    content_negotiation_class = _AlwaysJSON

    def _live_feed(self, token):
        """The feed row for ``token`` if its member may still read it, else None."""
        feed = (
            CalendarFeedToken.objects.select_related("profile__user", "profile__org")
            .filter(token_hash=CalendarFeedToken.hash_token(token))
            .first()
        )
        if feed is None:
            return None
        profile = feed.profile
        if not (profile.is_active and profile.user.is_active and profile.org.is_active):
            return None
        return feed

    @extend_schema(
        tags=["Calendar feed"],
        operation_id="calendar_feed_ics",
        responses={
            (200, "text/calendar"): OpenApiResponse(description="iCalendar feed"),
            404: OpenApiResponse(description="Unknown, disabled or inactive"),
        },
    )
    def get(self, request, token):
        feed = self._live_feed(token)
        if feed is None:
            return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)

        profile = feed.profile
        org = profile.org
        set_rls_context(org.id)
        activate_org_timezone(org)
        today = timezone.localdate()
        mine = (
            visible_tasks_qs(profile)
            .filter(Q(created_by=profile.user) | Q(assigned_to=profile))
            .filter(status__in=OPEN_STATUSES)
            .only("id", "title", "priority", "due_date", "updated_at")
            .distinct()
        )
        upcoming = list(
            mine.filter(
                due_date__gte=today, due_date__lte=today + timedelta(days=FUTURE_DAYS)
            ).order_by("due_date", "id")[:MAX_EVENTS]
        )
        overdue = list(
            mine.filter(
                due_date__gte=today - timedelta(days=PAST_DAYS), due_date__lt=today
            ).order_by("-due_date", "id")[: MAX_EVENTS - len(upcoming)]
        )
        tasks = upcoming + overdue
        events = []
        for task in tasks:
            link = frontend_url(f"/tasks/{task.id}")
            events.append(
                {
                    "uid": f"task-{task.id}@bottlecrm",
                    "stamp": task.updated_at,
                    "date": task.due_date,
                    "summary": task.title,
                    "description": f"Priority: {task.priority}\n{link}",
                    "url": link,
                }
            )

        now = timezone.now()
        if feed.last_used_at is None or now - feed.last_used_at >= TOUCH_INTERVAL:
            CalendarFeedToken.objects.filter(pk=feed.pk).update(last_used_at=now)

        response = HttpResponse(
            render_calendar(events), content_type="text/calendar; charset=utf-8"
        )
        response["Cache-Control"] = "private, max-age=300"
        response["X-Robots-Tag"] = "noindex"
        return response
