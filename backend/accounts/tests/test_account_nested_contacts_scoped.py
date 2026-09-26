"""The account's nested `contacts` lists only what the viewer may open.

`account_obj.contacts` (the list the phone reads) used to carry every contact
on the account, in full, to anyone who could open the account, while the
detail view's top-level `contacts` beside it was already narrowed. Its comment
said it had to stay whole because the phone sent it back through PUT; the
phone edits with PATCH now, and every contact write keeps the contacts the
caller cannot see, so the reason is gone.
"""

import pytest

from accounts.models import Account
from accounts.serializer import AccountSerializer
from contacts.models import Contact

pytestmark = pytest.mark.django_db


def _contact(org, created_by, first_name):
    contact = Contact.objects.create(
        first_name=first_name, last_name="Person", email=f"{first_name}@n.test", org=org
    )
    Contact.objects.filter(pk=contact.pk).update(created_by=created_by)
    return contact


@pytest.fixture
def account(org_a, admin_user, regular_user):
    account = Account.objects.create(name="Nested Ltd", org=org_a)
    Account.objects.filter(pk=account.pk).update(created_by=regular_user)
    account.refresh_from_db()
    account.contacts.add(
        _contact(org_a, regular_user, "Mine"), _contact(org_a, admin_user, "Hidden")
    )
    return account


def _nested(body):
    return {c["first_name"] for c in body["account_obj"]["contacts"]}


class TestDetailNesting:
    def test_member_sees_only_the_contacts_they_can_open(self, user_client, account):
        response = user_client.get(f"/api/accounts/{account.id}/")
        assert response.status_code == 200, response.content
        assert _nested(response.json()) == {"Mine"}

    def test_admin_sees_every_contact(self, admin_client, account):
        response = admin_client.get(f"/api/accounts/{account.id}/")
        assert response.status_code == 200, response.content
        assert _nested(response.json()) == {"Mine", "Hidden"}

    def test_comment_response_is_narrowed_too(self, user_client, account):
        """`post` on the detail route answers with the same `account_obj`."""
        response = user_client.post(
            f"/api/accounts/{account.id}/", {"comment": "hi"}, format="json"
        )
        assert response.status_code == 200, response.content
        assert _nested(response.json()) == {"Mine"}


class TestSerializerFailsClosed:
    def test_no_viewer_means_no_contacts(self, account):
        assert "contacts" not in AccountSerializer(account).data

    def test_member_viewer(self, account, user_profile):
        data = AccountSerializer(account, context={"profile": user_profile}).data
        assert {c["first_name"] for c in data["contacts"]} == {"Mine"}

    def test_admin_viewer(self, account, admin_profile):
        data = AccountSerializer(account, context={"profile": admin_profile}).data
        assert {c["first_name"] for c in data["contacts"]} == {"Mine", "Hidden"}
