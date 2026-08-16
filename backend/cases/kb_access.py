"""Who may do what to a knowledge-base article, three rules, beside the
three in `cases.access` and for the same reason.

Before this module the Solution endpoints had **no authorization at all**.
`IsAuthenticated` was the whole of it, so any member of the org could rewrite,
approve, publish, unpublish and hard-delete any article, including one an admin
had written and released. Proven against the running server as a plain `USER`
profile: `PATCH` a colleague's article 200, `POST .../publish/` 200,
`DELETE` 204.

    write     author · admin      title, body, and moving draft ↔ reviewed
    release   admin               status → approved, and publish / unpublish
    delete    author · admin

**Why `release` is narrower than `write`.** The model already carries a review
workflow (draft → reviewed → approved, plus a separate `is_published`), and
the whole point of it is that somebody other than the writer says the answer is
right before customers are shown it. If the author can approve their own
article the workflow is theatre; the rules, the states and the UI would all
still be there and none of them would mean anything. That is the same failure
already recorded against the case close-approval gate, so it is not a
hypothetical shape of bug for this codebase.

`read` is deliberately absent: every member of the org may read every article.
A knowledge base whose articles are hidden from the agents answering the
tickets is not a knowledge base.

`is_published` is not an access rule for agents either. It is the
customer-visibility switch, and it now switches something: a published,
approved article is readable by any signed-in portal contact in the org, via
`PortalBaseView._published_articles`. That is why `release` is the narrowest
rule here. Approving is the moment an article stops being an internal note.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework.exceptions import PermissionDenied

from cases.access import is_org_admin
from cases.models import Solution

_DENIED = "You do not have Permission to perform this action"


def get_solution_or_404(profile, pk):
    """Fetch an article in the requester's org, or raise ``Http404``.

    ``Solution.id`` is a UUID column, so ``filter(pk="nobody")`` raises
    Django's ``ValidationError`` instead of returning nothing. The views
    caught ``Solution.DoesNotExist`` and nothing else, so every verb. Read,
    update, delete, publish, unpublish. Answered 500 for any id that was not
    a well-formed UUID. A bad id names an article that does not exist.
    """
    try:
        solution = Solution.objects.filter(pk=pk, org=profile.org).first()
    except (DjangoValidationError, ValueError):
        raise Http404("No such solution.")
    if solution is None:
        raise Http404("No such solution.")
    return solution


def has_solution_write_access(profile, solution):
    """Non-raising form of `write`."""
    if is_org_admin(profile):
        return True
    return profile.user_id == solution.created_by_id


def assert_solution_write_access(profile, solution):
    """Raise 403 unless ``profile`` may edit ``solution``."""
    if not has_solution_write_access(profile, solution):
        raise PermissionDenied(_DENIED)


def assert_solution_release_access(profile):
    """Raise 403 unless ``profile`` may approve or publish.

    Takes no article on purpose. Being the author is the one thing that must
    *not* grant this, so there is nothing about the article to consult.
    """
    if not is_org_admin(profile):
        raise PermissionDenied(
            "Only an admin can approve or publish a knowledge-base article."
        )


def assert_solution_delete_access(profile, solution):
    """Raise 403 unless ``profile`` may destroy ``solution``.

    Same shape as writing rather than as releasing: deleting your own draft is
    ordinary tidying, and deleting somebody else's is not.
    """
    if not has_solution_write_access(profile, solution):
        raise PermissionDenied(_DENIED)
