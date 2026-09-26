"""Who may open a deal.

Extracted from ``OpportunityDetailView.assert_deal_access`` so the attachment
download view asks the same question rather than carrying a second copy of the
answer. The detail view still calls it.
"""

from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from common.permissions import is_org_admin
from opportunity.models import Opportunity

_DENIED = "You do not have Permission to perform this action"


def has_deal_access(profile, user, opportunity):
    """Admins, the creator, and anyone assigned. Everyone else is refused.

    Four copies of this check used to live inline in ``get``, ``put``,
    ``patch`` and ``post``, and all four compared ``request.profile``, a
    Profile, to ``opportunity.created_by``, which is a FK to ``User``. Those
    types are never equal, so the creator half was dead: a non-admin who
    created a deal and did not also assign it to themselves was refused their
    own record. ``delete()`` got the same comparison right, which is how you
    could tell it was a mistake rather than a policy.
    """
    if is_org_admin(profile) or user.is_superuser:
        return True
    if profile.user_id == opportunity.created_by_id:
        return True
    return profile.id in {assignee.id for assignee in opportunity.assigned_to.all()}


def visible_deals_qs(profile, user):
    """Deals ``profile`` may open, the queryset form of `has_deal_access`."""
    qs = Opportunity.objects.filter(org=profile.org)
    if is_org_admin(profile) or user.is_superuser:
        return qs
    return qs.filter(Q(created_by=profile.user) | Q(assigned_to=profile)).distinct()


def assert_deal_access(profile, user, opportunity):
    """Raise 403 unless ``profile`` may open ``opportunity``.

    Raising beats returning a Response: a returned Response from a helper gets
    wrapped in ``Response(...)`` by the caller and renders as a 500.
    """
    if not has_deal_access(profile, user, opportunity):
        raise PermissionDenied(_DENIED)
