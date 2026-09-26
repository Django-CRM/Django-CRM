"""Who may see a lead.

One rule, asked two ways: ``visible_leads_qs`` narrows a queryset (the lead list,
the board, the pipeline lead counts, and the detail, move, comment and
attachment lookups, so a hidden lead is the same 404 as a missing one), and
``has_lead_access`` answers for one lead (the attachment download). Both live
here so the answers cannot drift apart.
"""

from django.db.models import Q

from common.permissions import is_org_admin
from leads.models import Lead


def has_lead_access(profile, lead):
    """Org admins (``is_org_admin``, which admits a superuser's profile) see
    every lead in the org; everyone else sees their own.

    The same rule as ``visible_leads_qs``, for one lead. Without it, a lead the
    list deliberately withholds is still readable by id.

    ``Lead.created_by`` is a **User** FK, so the creator half compares
    ``profile.user_id``. Two hand-rolled copies of this check once built a set
    of Profile ids and appended a User id to it, which meant the creator's own
    id was never in the set and the check denied the person it existed to
    admit.
    """
    if is_org_admin(profile):
        return True
    if profile.user_id == lead.created_by_id:
        return True
    return profile.id in {assignee.id for assignee in lead.assigned_to.all()}


def may_delete_lead(profile, lead):
    """Narrower than reading or editing: admins (superusers included) and the
    lead's creator. An assignee may open and edit a lead but not destroy it,
    which includes merging it away into another lead."""
    return is_org_admin(profile) or profile.user_id == lead.created_by_id


def visible_leads_qs(profile):
    """Leads ``profile`` may open, the queryset form of `has_lead_access`.

    The lead list, the board and the pipeline ``lead_count`` all start from
    this. Status and ``is_active`` are left to each caller.

    The assignee half is a subquery rather than a join on the M2M, so a lead
    with several assignees comes back once without ``distinct()``, and the
    queryset can sit inside a filtered ``Count`` annotation as it is.
    """
    qs = Lead.objects.filter(org=profile.org)
    if is_org_admin(profile):
        return qs
    return qs.filter(
        Q(created_by=profile.user)
        | Q(pk__in=Lead.objects.filter(assigned_to=profile).values("pk"))
    )
