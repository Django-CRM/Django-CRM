"""Who may open an account.

One definition, asked by the detail and mail views (through
``visible_accounts_qs``, in the lookup itself, so a hidden account is the same
404 as a missing one) and by the attachment download (``has_account_access``).
"""

from django.db.models import Q

from accounts.models import Account
from common.permissions import is_org_admin


def has_account_access(profile, account):
    """Org admins (``is_org_admin``, which admits a superuser's profile), the
    creator, and anyone assigned. Else refused.

    One check, because there were four and they disagreed. ``get``, ``put``,
    ``patch`` and comment ``post`` each compared ``request.profile``, a
    Profile, against ``account.created_by``, which is a FK to ``User``. Those
    are never equal, so the creator branch could not fire and creators were
    locked out of their own accounts.

    ``delete()`` and the list filter got the same comparison *right*. That is
    the tell that this was a mistake and not a policy: the same non-admin could
    watch an account sit in their list, be refused permission to open it, and
    still delete it outright.
    """
    if is_org_admin(profile):
        return True
    if profile.user_id == account.created_by_id:
        return True
    return profile.id in {assignee.id for assignee in account.assigned_to.all()}


def may_delete_account(profile, account):
    """Narrower than reading or editing: admins (superusers included) and the
    account's creator. An assignee may open and edit an account but not
    destroy it, which includes merging it away into another account."""
    return is_org_admin(profile) or profile.user_id == account.created_by_id


def visible_accounts_qs(profile):
    """Accounts ``profile`` may open, the queryset form of `has_account_access`.

    Scoped to ``profile.org`` here, unlike the predicate, whose callers fetch
    the account with that filter first.
    """
    qs = Account.objects.filter(org=profile.org)
    if is_org_admin(profile):
        return qs
    return qs.filter(Q(created_by=profile.user) | Q(assigned_to=profile)).distinct()
