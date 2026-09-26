"""D37: the contact read rule ignored Django ``is_superuser``.

``has_contact_access`` and ``visible_contacts_qs`` let an org admin through
and nobody else by role, while the account, deal, lead and invoice rules all
admit a superuser too. They now read ``profile.user.is_superuser``. The org
filter stays: a superuser sees every contact in their own org, never another
org's.

The contact list, the global search and the contact pickers on the lead,
account, deal, ticket and task lists each carried an inline copy of the rule
that knew neither superusers nor account assignment. They now call
``visible_contacts_qs``, so these tests pin them too.
"""

import pytest
from rest_framework import status

from accounts.models import Account
from common.models import Profile
from contacts.access import has_contact_access, visible_contacts_qs
from contacts.models import Contact

pytestmark = pytest.mark.django_db


@pytest.fixture
def others_contact(org_a, admin_user):
    """A contact the ``user_client`` caller neither created nor is assigned to."""
    return Contact.objects.create(
        first_name="Grace", last_name="Hopper", org=org_a, created_by=admin_user
    )


@pytest.fixture
def foreign_contact(org_b, user_b):
    """A contact in the other tenant."""
    return Contact.objects.create(
        first_name="Gracie", last_name="Elsewhere", org=org_b, created_by=user_b
    )


@pytest.fixture
def superuser(regular_user):
    """The ``user_client`` caller, with a plain USER profile, made superuser."""
    regular_user.is_superuser = True
    regular_user.save(update_fields=["is_superuser"])
    return regular_user


def _fresh(profile):
    """Reload so ``profile.user`` reflects the flag as the database holds it."""
    return Profile.objects.select_related("user", "org").get(pk=profile.pk)


class TestPredicate:
    def test_plain_member_is_refused(self, user_profile, others_contact):
        assert has_contact_access(_fresh(user_profile), others_contact) is False

    def test_superuser_without_an_admin_profile_is_admitted(
        self, superuser, user_profile, others_contact
    ):
        profile = _fresh(user_profile)
        assert profile.role != "ADMIN"
        assert has_contact_access(profile, others_contact) is True


class TestQueryset:
    def test_plain_member_does_not_see_it(self, user_profile, others_contact):
        assert not visible_contacts_qs(_fresh(user_profile)).filter(
            pk=others_contact.pk
        )

    def test_superuser_sees_every_contact_in_their_org(
        self, superuser, user_profile, others_contact
    ):
        assert visible_contacts_qs(_fresh(user_profile)).filter(pk=others_contact.pk)

    def test_superuser_does_not_see_another_orgs_contact(
        self, superuser, user_profile, others_contact, foreign_contact
    ):
        ids = set(
            visible_contacts_qs(_fresh(user_profile)).values_list("id", flat=True)
        )
        assert others_contact.id in ids
        assert foreign_contact.id not in ids


class TestDetail:
    def test_plain_member_is_refused(self, user_client, others_contact):
        response = user_client.get(f"/api/contacts/{others_contact.pk}/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_superuser_may_open_it(self, superuser, user_client, others_contact):
        response = user_client.get(f"/api/contacts/{others_contact.pk}/")
        assert response.status_code == status.HTTP_200_OK, response.content

    def test_superuser_may_not_open_another_orgs_contact(
        self, superuser, user_client, foreign_contact
    ):
        response = user_client.get(f"/api/contacts/{foreign_contact.pk}/")
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestList:
    def _ids(self, client):
        response = client.get("/api/contacts/")
        assert response.status_code == status.HTTP_200_OK
        return {row["id"] for row in response.data["results"]}

    def test_plain_member_does_not_see_it(self, user_client, others_contact):
        assert str(others_contact.id) not in self._ids(user_client)

    def test_superuser_sees_it_but_not_another_orgs(
        self, superuser, user_client, others_contact, foreign_contact
    ):
        ids = self._ids(user_client)
        assert str(others_contact.id) in ids
        assert str(foreign_contact.id) not in ids

    def test_member_sees_a_contact_at_an_account_they_are_assigned(
        self, org_a, user_profile, user_client, others_contact
    ):
        # The detail page already opened this one; the list used to hide it.
        account = Account.objects.create(name="Navy", org=org_a)
        account.assigned_to.add(user_profile)
        account.contacts.add(others_contact)
        response = user_client.get(f"/api/contacts/{others_contact.pk}/")
        assert response.status_code == status.HTTP_200_OK
        assert str(others_contact.id) in self._ids(user_client)


class TestDelete:
    def test_plain_member_is_refused(self, user_client, others_contact):
        response = user_client.delete(f"/api/contacts/{others_contact.pk}/")
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Contact.objects.filter(pk=others_contact.pk).exists()

    def test_superuser_may_delete_it(self, superuser, user_client, others_contact):
        response = user_client.delete(f"/api/contacts/{others_contact.pk}/")
        assert response.status_code == status.HTTP_200_OK, response.content
        assert not Contact.objects.filter(pk=others_contact.pk).exists()


class TestSearch:
    def _contact_ids(self, client):
        response = client.get("/api/search/", {"q": "Grac"})
        assert response.status_code == status.HTTP_200_OK
        return {r["id"] for r in response.data["results"] if r["type"] == "contact"}

    def test_plain_member_does_not_find_it(self, user_client, others_contact):
        assert str(others_contact.id) not in self._contact_ids(user_client)

    def test_superuser_finds_it_but_not_another_orgs(
        self, superuser, user_client, others_contact, foreign_contact
    ):
        ids = self._contact_ids(user_client)
        assert str(others_contact.id) in ids
        assert str(foreign_contact.id) not in ids


class TestPickers:
    """Each list endpoint's contact catalogue offers what its save path accepts."""

    @pytest.mark.parametrize(
        "path, key",
        [
            ("/api/tasks/", "contacts_list"),
            ("/api/cases/", "contacts_list"),
            ("/api/accounts/", "contacts"),
            ("/api/leads/", "contacts"),
            ("/api/opportunities/", "contacts_list"),
        ],
    )
    def test_superuser_is_offered_it_and_a_member_is_not(
        self, path, key, regular_user, user_client, others_contact, foreign_contact
    ):
        def offered():
            response = user_client.get(path)
            assert response.status_code == status.HTTP_200_OK, response.content
            return {str(row["id"]) for row in response.data[key]}

        assert str(others_contact.id) not in offered()

        regular_user.is_superuser = True
        regular_user.save(update_fields=["is_superuser"])
        ids = offered()
        assert str(others_contact.id) in ids
        assert str(foreign_contact.id) not in ids
