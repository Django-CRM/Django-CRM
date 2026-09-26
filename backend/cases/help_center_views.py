"""The public help center: an org's knowledge base, readable by anyone.

Anonymous and indexable by design, and opt-in per org (`Org.help_center_enabled`,
off by default). Mounted at `/api/public/help/<slug>/` and rendered by the web
app at `/help-center/<slug>`. (Not `/help/`: the web app already serves the
signed-in user's own support tickets there.)

READ THE ORDER BEFORE CHANGING IT, for the reason `webforms/public_views.py`
gives: `solution` is an org-scoped table, so under an empty RLS context it
returns zero rows on a correctly configured Postgres. The org is resolved from
the slug first (`organization` carries no RLS policy), the context is set from
it, and only then is anything org-scoped read.

What a visitor may read is `published_articles`, the same definition the
signed-in portal uses. Search and related articles narrow that set; neither
starts from `Solution.objects`.

No credential is accepted or consulted. `authentication_classes` is empty and
nothing here reads `request.user`, `request.profile` or `request.org`, so a
staff JWT sent along with the request changes nothing about the answer.
"""

from rest_framework import status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from cases.kb_text import snippet, text_match
from cases.portal_serializers import (
    PortalSolutionDetailSerializer,
    PortalSolutionSerializer,
)
from cases.portal_views import published_articles, related_articles
from common.models import Org
from common.request_meta import client_ip
from common.tasks import set_rls_context

# A search term longer than this is not a question anybody typed.
SEARCH_MAX = 200

# One body for every miss: no such slug, a help center switched off, an
# inactive org, and an article that is a draft, unpublished, or another org's.
# Telling any two of those apart would let a stranger probe which orgs use the
# product and which article ids exist behind the published set.
NOT_FOUND = {"error": "Not found"}


class HelpCenterIPThrottle(SimpleRateThrottle):
    """Per-visitor rate limit, bucketed on the forwarded client IP.

    The web app renders these pages server-side and forwards the visitor's
    address, so the bucket is the visitor rather than the web server. The
    header is spoofable, which makes this a brake on casual scraping, not a
    wall; the data behind it is public by the org's own choice.
    """

    scope = "help_center_ip"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": client_ip(request) or "unknown",
        }


class HelpCenterGlobalThrottle(SimpleRateThrottle):
    """Per-help-center rate limit across every visitor.

    The backstop for `HelpCenterIPThrottle`: rotating `X-Forwarded-For` buys a
    fresh per-visitor bucket every request but cannot touch this one. Bucketed
    on the slug, as the web form limit is bucketed on the form, so a scraper
    hammering one org's help center cannot lock readers out of another's.
    """

    scope = "help_center_global"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": str(view.kwargs.get("slug")),
        }


class HelpCenterPagination(LimitOffsetPagination):
    default_limit = 20
    # Capped because the caller is anonymous. The sitemap walks pages of this
    # size rather than asking for everything at once.
    max_limit = 100


class PublicHelpCenterView(APIView):
    authentication_classes: list = []
    permission_classes = (AllowAny,)
    throttle_classes = [HelpCenterIPThrottle, HelpCenterGlobalThrottle]

    def _org(self, slug):
        """The org publishing a help center at `slug`, with RLS set, or None.

        Slugs are stored lowercase, so the match is exact.
        """
        org = Org.objects.filter(
            help_center_slug=slug, help_center_enabled=True, is_active=True
        ).first()
        if org is not None:
            set_rls_context(org.id)
        return org

    def _not_found(self):
        return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)

    def _header(self, org):
        # Name only. The logo is left out because in production it is a signed,
        # expiring storage URL, which a cached or indexed page would outlive.
        return {"name": org.company_name or org.name or ""}


class PublicHelpCenterListView(PublicHelpCenterView):
    def get(self, request, slug):
        org = self._org(slug)
        if org is None:
            return self._not_found()

        queryset = published_articles(org)
        q = (request.query_params.get("q") or "").strip()[:SEARCH_MAX]
        if q:
            queryset = queryset.filter(text_match(q))
        queryset = queryset.order_by("title", "id")

        paginator = HelpCenterPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return Response(
            {
                "help_center": self._header(org),
                "articles": [
                    {
                        **PortalSolutionSerializer(article).data,
                        "snippet": snippet(article.description),
                    }
                    for article in page
                ],
                "articles_count": paginator.count,
            }
        )


class PublicHelpCenterArticleView(PublicHelpCenterView):
    def get(self, request, slug, pk):
        org = self._org(slug)
        if org is None:
            return self._not_found()

        article = published_articles(org).filter(pk=pk).first()
        if article is None:
            return self._not_found()

        return Response(
            {
                "help_center": self._header(org),
                "article": PortalSolutionDetailSerializer(article).data,
                "related": related_articles(org, article),
            }
        )
