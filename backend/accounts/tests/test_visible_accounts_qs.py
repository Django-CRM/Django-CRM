"""`visible_accounts_qs` is `has_account_access` as a queryset.

The ticket form's account picker, the ticket CSV import and the ticket API's
`validate_account` all ask "which accounts may this member open". The first
two ask through the queryset, the last through the predicate, so the two must
give the same answer for every kind of caller or a picker offers an account
the save path refuses (or the reverse).
"""

import pytest

from accounts.access import has_account_access, visible_accounts_qs
from accounts.models import Account
from common.models import Profile
from conftest import rls_org


def _account(org, name, created_by=None):
    account = Account.objects.create(name=name, org=org)
    # `update` rather than `create(created_by=...)`, so a request user left in
    # the thread local cannot restamp it.
    Account.objects.filter(pk=account.pk).update(created_by=created_by)
    account.refresh_from_db()
    return account


@pytest.fixture
def accounts(org_a, admin_user, regular_user, user_profile):
    created = _account(org_a, "Created by member", created_by=regular_user)
    assigned = _account(org_a, "Assigned to member", created_by=admin_user)
    assigned.assigned_to.add(user_profile)
    hidden = _account(org_a, "Hidden from member", created_by=admin_user)
    ownerless = _account(org_a, "Nobody's")
    return [created, assigned, hidden, ownerless]


@pytest.fixture
def superuser(regular_user):
    regular_user.is_superuser = True
    regular_user.save(update_fields=["is_superuser"])
    return regular_user


def _by_predicate(profile, user, accounts):
    return {a.id for a in accounts if has_account_access(profile, user, a)}


def _by_queryset(profile, user):
    return set(visible_accounts_qs(profile, user).values_list("id", flat=True))


@pytest.mark.django_db
class TestVisibleAccountsAgreesWithThePredicate:
    def test_plain_member(self, accounts, user_profile, regular_user):
        expected = _by_predicate(user_profile, regular_user, accounts)
        assert _by_queryset(user_profile, regular_user) == expected
        # Both halves of the rule fire, and both refusals do too.
        assert expected == {accounts[0].id, accounts[1].id}

    def test_admin(self, accounts, admin_profile, admin_user):
        expected = _by_predicate(admin_profile, admin_user, accounts)
        assert _by_queryset(admin_profile, admin_user) == expected
        assert expected == {a.id for a in accounts}

    def test_superuser_with_a_plain_profile(self, accounts, user_profile, superuser):
        assert user_profile.role != "ADMIN"
        expected = _by_predicate(user_profile, superuser, accounts)
        assert _by_queryset(user_profile, superuser) == expected
        assert expected == {a.id for a in accounts}

    def test_member_with_nothing(self, accounts, org_a, user_b):
        # A second org_a member who created nothing and is assigned nothing.
        profile = Profile.objects.create(
            user=user_b, org=org_a, role="USER", is_active=True
        )
        assert _by_predicate(profile, user_b, accounts) == set()
        assert _by_queryset(profile, user_b) == set()

    def test_two_assignees_do_not_duplicate_the_row(
        self, accounts, user_profile, regular_user, admin_profile
    ):
        accounts[1].assigned_to.add(admin_profile)
        rows = list(
            visible_accounts_qs(user_profile, regular_user).values_list("id", flat=True)
        )
        assert len(rows) == len(set(rows))

    def test_never_leaves_the_org(self, accounts, org_b, user_b, admin_profile):
        with rls_org(org_b):
            other = _account(org_b, "Someone else's", created_by=user_b)
        assert other.id not in _by_queryset(admin_profile, admin_profile.user)
