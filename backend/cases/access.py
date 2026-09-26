"""Who may do what to a case, one place, three rules.

Before this module the question was answered in five places and had drifted
into three different answers:

* `CaseListView` and `watcher_views._visible_cases_qs` said **admin, creator,
  assignee or watcher**.
* `CaseDetailView.get` / `.put` / `.patch` / `.post` and
  `CaseActivityListView` said **admin, creator or assignee**, no watcher.
* `CaseDetailView.delete` said **admin or creator**, no assignee either.

The visible consequence was a queue that lied. A watcher's ticket list showed
the one case they follow; opening it answered 403. That is the same shape of
contradiction accounts and contacts had, and the same conclusion applies:
when the verbs disagree by accident it is a mistake, not a policy.

Where they disagree *on purpose*, they now say so. Reading, writing and
deleting are three rules on three lines, deliberately not one:

    read    admin · creator · assignee · watcher
    write   admin · creator · assignee
    delete  admin · creator

Watching is subscribing to a ticket, not being handed the keys to it, so the
watcher clause that was missing from reads is not quietly added to writes.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.http import Http404
from rest_framework.exceptions import PermissionDenied

from cases.models import Case

# Re-exported, not redefined. Four modules already import `is_org_admin` from
# here, and this used to be its own copy of the rule, one of ten across the
# backend. The single definition now lives in `common.permissions`, which
# nothing about cases needs to be imported; this name stays so those four
# imports keep working and so the cases access rules still read in one place.
from common.permissions import is_org_admin  # noqa: F401

_DENIED = "You do not have Permission to perform this action"


def visible_cases_qs(profile):
    """Cases ``profile`` is allowed to open. The queryset form of `read`.

    The watcher clause is what lets somebody keep following a ticket after
    they are unassigned, and it is the clause the detail view was missing.
    """
    qs = Case.objects.filter(org=profile.org)
    if is_org_admin(profile):
        return qs
    return qs.filter(
        Q(created_by=profile.user) | Q(assigned_to=profile) | Q(watchers=profile)
    ).distinct()


def writable_cases_qs(profile):
    """Cases ``profile`` may change or reply on. The queryset form of `write`.

    `visible_cases_qs` without the watcher clause, for any list that offers an
    action on each row: a watcher can open the ticket but is refused the reply,
    so offering them one is a button that answers 403.
    """
    qs = Case.objects.filter(org=profile.org)
    if is_org_admin(profile):
        return qs
    return qs.filter(Q(created_by=profile.user) | Q(assigned_to=profile)).distinct()


def get_case_or_404(profile, pk):
    """Fetch a case in the requester's org, or raise ``Http404``.

    ``Case.id`` is a UUID column, so ``filter(id="nobody")`` raises Django's
    ``ValidationError`` rather than returning nothing, which surfaced as a
    500 on every verb for any id that was not a well-formed UUID. A bad id is
    a request for a case that does not exist; that is a 404.
    """
    try:
        case = Case.objects.filter(pk=pk, org=profile.org).first()
    except (DjangoValidationError, ValueError):
        raise Http404("No such case.")
    if case is None:
        raise Http404("No such case.")
    return case


def has_case_read_access(profile, case):
    """Non-raising form of `read`, for computing response flags."""
    if has_case_write_access(profile, case):
        return True
    return case.watchers.filter(id=profile.id).exists()


def has_case_write_access(profile, case):
    """Non-raising form of `write`."""
    if is_org_admin(profile):
        return True
    if profile.user_id == case.created_by_id:
        return True
    return profile.id in {p.id for p in case.assigned_to.all()}


def assert_case_read_access(profile, case):
    """Raise 403 unless ``profile`` may open ``case``."""
    if not has_case_read_access(profile, case):
        raise PermissionDenied(_DENIED)


def assert_case_write_access(profile, case):
    """Raise 403 unless ``profile`` may change ``case`` or reply on it."""
    if not has_case_write_access(profile, case):
        raise PermissionDenied(_DENIED)


def assert_case_delete_access(profile, case):
    """Raise 403 unless ``profile`` may destroy ``case``.

    Narrower than writing on purpose: an assignee is somebody the work was
    handed to, which is a reason to let them work the ticket and not a reason
    to let them erase it.
    """
    if is_org_admin(profile):
        return
    if profile.user_id == case.created_by_id:
        return
    raise PermissionDenied(_DENIED)
