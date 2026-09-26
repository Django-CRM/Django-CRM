"""Who may open a deal.

One definition, asked by the detail, line-item, move and attachment views
rather than carried inline by each: four inline copies is how the creator
branch came to be dead in all four of them.

A deal the caller may not open answers exactly as an id that does not exist
(owner decision for 1.11.0). So the views look a deal up through
`visible_deals_qs` in one step, never an org-wide fetch followed by a check:
that shape answered 403 for a hidden deal and 404 for a missing one, and the
difference confirmed the id was real.
"""

from django.db.models import Q

from common.permissions import is_org_admin
from opportunity.models import Opportunity


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


def get_visible_deal(profile, user, pk):
    """The deal ``pk`` if ``profile`` may open it, else ``None``.

    ``None`` for a hidden deal and for a missing one alike, so the caller
    cannot answer the two differently.
    """
    return visible_deals_qs(profile, user).filter(pk=pk).first()
