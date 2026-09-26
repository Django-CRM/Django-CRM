"""Who may open an account.

Extracted from ``AccountDetailView.assert_account_access`` so the attachment
download view asks the same question rather than carrying a second copy of the
answer. The detail view still calls it.
"""

from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from accounts.models import Account
from common.permissions import is_org_admin

_DENIED = "You do not have Permission to perform this action"


def has_account_access(profile, user, account):
    """Admins, superusers, the creator, and anyone assigned. Else refused.

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
    if is_org_admin(profile) or user.is_superuser:
        return True
    if profile.user_id == account.created_by_id:
        return True
    return profile.id in {assignee.id for assignee in account.assigned_to.all()}


def visible_accounts_qs(profile, user):
    """Accounts ``profile`` may open, the queryset form of `has_account_access`.

    Scoped to ``profile.org`` here, unlike the predicate, whose callers fetch
    the account with that filter first.
    """
    qs = Account.objects.filter(org=profile.org)
    if is_org_admin(profile) or user.is_superuser:
        return qs
    return qs.filter(Q(created_by=profile.user) | Q(assigned_to=profile)).distinct()


def assert_account_access(profile, user, account):
    """Raise 403 unless ``profile`` may open ``account``."""
    if not has_account_access(profile, user, account):
        raise PermissionDenied(_DENIED)
