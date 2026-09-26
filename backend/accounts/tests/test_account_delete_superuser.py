"""Account delete lets a Django superuser through, as the deal delete does.

`AccountDetailView.delete` carried its own check (admin or creator) and was
the one account rule that ignored `is_superuser`: `has_account_access`, the
deal delete and the invoice rules all admit a superuser.
"""

import pytest

from accounts.models import Account

DENIED = "You do not have Permission to perform this action"


@pytest.fixture
def admins_account(org_a, admin_user):
    return Account.objects.create(name="Not yours", org=org_a, created_by=admin_user)


@pytest.mark.django_db
class TestAccountDelete:
    def test_superuser_without_an_admin_profile_can_delete(
        self, user_client, user_profile, regular_user, admins_account
    ):
        regular_user.is_superuser = True
        regular_user.save(update_fields=["is_superuser"])
        assert user_profile.role != "ADMIN"

        response = user_client.delete(f"/api/accounts/{admins_account.id}/")

        assert response.status_code == 200, response.content
        assert not Account.objects.filter(id=admins_account.id).exists()

    def test_member_who_did_not_create_it_is_refused(
        self, user_client, user_profile, admins_account
    ):
        admins_account.assigned_to.add(user_profile)

        response = user_client.delete(f"/api/accounts/{admins_account.id}/")

        assert response.status_code == 403
        assert response.json() == {"error": True, "errors": DENIED}
        assert Account.objects.filter(id=admins_account.id).exists()

    def test_admin_can_delete(self, admin_client, org_a, regular_user):
        account = Account.objects.create(
            name="Someone else's", org=org_a, created_by=regular_user
        )

        response = admin_client.delete(f"/api/accounts/{account.id}/")

        assert response.status_code == 200, response.content
        assert not Account.objects.filter(id=account.id).exists()

    def test_creator_can_delete(self, user_client, org_a, regular_user):
        account = Account.objects.create(
            name="Mine", org=org_a, created_by=regular_user
        )

        response = user_client.delete(f"/api/accounts/{account.id}/")

        assert response.status_code == 200, response.content
