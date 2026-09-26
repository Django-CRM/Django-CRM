"""Who may see a lead.

One rule, asked two ways: ``visible_leads_qs`` narrows a queryset (the lead list,
the board, and the pipeline lead counts), and ``has_lead_access`` answers for
one lead (the detail view and the attachment download). Both live here so the
answers cannot drift apart.
"""

from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from common.permissions import is_org_admin
from leads.models import Lead

_DENIED = "You do not have Permission to perform this action"


def has_lead_access(profile, user, lead):
    """Admins and superusers see every lead in the org; everyone else sees
    their own.

    The same rule as ``visible_leads_qs``, for one lead. Without it, a lead the
    list deliberately withholds is still readable by id.

    ``Lead.created_by`` is a **User** FK, so the creator half compares
    ``profile.user_id``. Two hand-rolled copies of this check once built a set
    of Profile ids and appended a User id to it, which meant the creator's own
    id was never in the set and the check denied the person it existed to
    admit.
    """
    if is_org_admin(profile) or user.is_superuser:
        return True
    if profile.user_id == lead.created_by_id:
        return True
    return profile.id in {assignee.id for assignee in lead.assigned_to.all()}


def visible_leads_qs(profile, user):
    """Leads ``profile`` may open, the queryset form of `has_lead_access`.

    The lead list, the board and the pipeline ``lead_count`` all start from
    this. Status and ``is_active`` are left to each caller.

    The assignee half is a subquery rather than a join on the M2M, so a lead
    with several assignees comes back once without ``distinct()``, and the
    queryset can sit inside a filtered ``Count`` annotation as it is.
    """
    qs = Lead.objects.filter(org=profile.org)
    if is_org_admin(profile) or user.is_superuser:
        return qs
    return qs.filter(
        Q(created_by=profile.user)
        | Q(pk__in=Lead.objects.filter(assigned_to=profile).values("pk"))
    )


def assert_lead_access(profile, user, lead):
    """Raise 403 unless ``profile`` may open ``lead``.

    It raises rather than returning a Response: ``get_context_data`` returns
    the dict that ``get()`` passes to ``Response(...)``, so a Response returned
    from in there was wrapped in a second one and rendered as a 500 instead of
    the intended 403.
    """
    if not has_lead_access(profile, user, lead):
        raise PermissionDenied(_DENIED)
