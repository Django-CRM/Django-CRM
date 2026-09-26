"""An account edit changes what it sends and nothing else.

Two defects meet here.

The phone edited accounts with PUT, and `AccountDetailView.put` clears
`teams` unconditionally. The phone has no teams control, so every edit made
from it unlinked every team on the account (tracker D17). It now edits with
PATCH, which touches a relation only when its key is present, so PATCH has to
do everything PUT did for the keys the phone does send.

The detail view listed every contact on the account to anyone who could open
it. It now lists only the contacts the viewer may open, so an edit form built
from that list never holds the others. The write path therefore replaces only
the contacts the caller can see, and will not link one they cannot.
"""

from unittest.mock import patch

import pytest

from accounts.models import Account
from common.models import CustomFieldDefinition, Tags, Teams
from contacts.models import Contact

pytestmark = pytest.mark.django_db


def _contact(org, created_by, first_name):
    contact = Contact.objects.create(
        first_name=first_name, last_name="Person", email=f"{first_name}@x.test", org=org
    )
    Contact.objects.filter(pk=contact.pk).update(created_by=created_by)
    return contact


@pytest.fixture
def world(org_a, admin_user, regular_user, user_profile):
    """An account the member created but is not assigned to, carrying one
    contact they may open (their own) and one they may not (the admin's)."""
    account = Account.objects.create(name="Difference Engines", org=org_a)
    Account.objects.filter(pk=account.pk).update(created_by=regular_user)
    account.refresh_from_db()
    mine = _contact(org_a, regular_user, "Mine")
    hidden = _contact(org_a, admin_user, "Hidden")
    account.contacts.add(mine, hidden)
    return {"account": account, "mine": mine, "hidden": hidden}


def _url(account):
    return f"/api/accounts/{account.id}/"


def _linked(account):
    return {c.first_name for c in account.contacts.all()}


class TestDetailListsVisibleContacts:
    def test_member_does_not_see_a_contact_they_cannot_open(self, user_client, world):
        response = user_client.get(_url(world["account"]))
        assert response.status_code == 200, response.content
        assert [c["first_name"] for c in response.json()["contacts"]] == ["Mine"]

    def test_member_cannot_open_that_contact_either(self, user_client, world):
        """The filter mirrors the contact endpoint's own rule, not a new one."""
        hidden = user_client.get(f"/api/contacts/{world['hidden'].id}/")
        mine = user_client.get(f"/api/contacts/{world['mine'].id}/")
        assert hidden.status_code == 403
        assert mine.status_code == 200

    def test_admin_sees_every_contact(self, admin_client, world):
        response = admin_client.get(_url(world["account"]))
        assert response.status_code == 200, response.content
        names = {c["first_name"] for c in response.json()["contacts"]}
        assert names == {"Mine", "Hidden"}


class TestWritePreservesHiddenContacts:
    @pytest.mark.parametrize("verb", ["patch", "put"])
    def test_sending_the_visible_list_keeps_the_hidden_contact(
        self, user_client, world, verb
    ):
        account = world["account"]
        response = getattr(user_client, verb)(
            _url(account),
            {"name": account.name, "contacts": [str(world["mine"].id)]},
            format="json",
        )
        assert response.status_code == 200, response.content
        assert _linked(account) == {"Mine", "Hidden"}

    def test_an_empty_list_unlinks_only_what_the_member_can_see(
        self, user_client, world
    ):
        account = world["account"]
        response = user_client.patch(_url(account), {"contacts": []}, format="json")
        assert response.status_code == 200, response.content
        assert _linked(account) == {"Hidden"}

    def test_member_cannot_link_a_contact_they_cannot_open(
        self, user_client, world, org_a, admin_user
    ):
        """Linking it to an account would hand the member a copy through the
        account's nested contacts, so an unreadable id is dropped."""
        account = world["account"]
        stranger = _contact(org_a, admin_user, "Stranger")
        response = user_client.patch(
            _url(account),
            {"contacts": [str(world["mine"].id), str(stranger.id)]},
            format="json",
        )
        assert response.status_code == 200, response.content
        assert _linked(account) == {"Mine", "Hidden"}

    def test_member_can_link_a_contact_they_can_open(
        self, user_client, world, org_a, regular_user
    ):
        account = world["account"]
        other = _contact(org_a, regular_user, "Other")
        response = user_client.patch(
            _url(account),
            {"contacts": [str(world["mine"].id), str(other.id)]},
            format="json",
        )
        assert response.status_code == 200, response.content
        assert _linked(account) == {"Mine", "Other", "Hidden"}

    def test_admin_replaces_the_whole_list(self, admin_client, world):
        account = world["account"]
        response = admin_client.patch(
            _url(account), {"contacts": [str(world["mine"].id)]}, format="json"
        )
        assert response.status_code == 200, response.content
        assert _linked(account) == {"Mine"}

    def test_a_contact_from_another_org_is_never_linked(
        self, admin_client, world, org_b
    ):
        account = world["account"]
        foreign = Contact.objects.create(first_name="Foreign", last_name="X", org=org_b)
        response = admin_client.patch(
            _url(account),
            {"contacts": [str(world["mine"].id), str(foreign.id)]},
            format="json",
        )
        assert response.status_code == 200, response.content
        assert _linked(account) == {"Mine"}


class TestMobileEditOverPatch:
    """The body `Account.toPayload()` builds on the phone."""

    @pytest.fixture
    def seeded(self, org_a, admin_profile, world):
        account = world["account"]
        team = Teams.objects.create(name="Field", description="-", org=org_a)
        old_tag = Tags.objects.create(name="Old", org=org_a)
        new_tag = Tags.objects.create(name="New", org=org_a)
        account.teams.add(team)
        account.tags.add(old_tag)
        CustomFieldDefinition.objects.create(
            org=org_a,
            target_model="Account",
            key="tier",
            label="Tier",
            field_type="text",
            is_active=True,
        )
        return {"team": team, "new_tag": new_tag}

    def _payload(self, world, seeded, admin_profile):
        return {
            "name": "Renamed Engines",
            "email": "hello@engines.test",
            "phone": None,
            "website": None,
            "industry": None,
            "number_of_employees": 12,
            "annual_revenue": "1000.00",
            "currency": "USD",
            "address_line": None,
            "city": "Leeds",
            "state": None,
            "postcode": None,
            "country": None,
            "description": "Edited on a phone",
            "assigned_to": [str(admin_profile.id)],
            "tags": [str(seeded["new_tag"].id)],
            "contacts": [str(world["mine"].id), str(world["hidden"].id)],
            "custom_fields": {"tier": "gold"},
        }

    @patch("accounts.views.send_email_to_assigned_user.delay")
    def test_saves_every_field_and_keeps_the_teams(
        self, _mail, admin_client, world, seeded, admin_profile
    ):
        account = world["account"]
        response = admin_client.patch(
            _url(account), self._payload(world, seeded, admin_profile), format="json"
        )
        assert response.status_code == 200, response.content

        account.refresh_from_db()
        assert account.name == "Renamed Engines"
        assert account.email == "hello@engines.test"
        assert account.number_of_employees == 12
        assert str(account.annual_revenue) == "1000.00"
        assert account.city == "Leeds"
        assert account.description == "Edited on a phone"
        assert account.custom_fields == {"tier": "gold"}
        assert list(account.assigned_to.all()) == [admin_profile]
        assert [t.name for t in account.tags.all()] == ["New"]
        assert _linked(account) == {"Mine", "Hidden"}
        # The key the phone never sends.
        assert list(account.teams.all()) == [seeded["team"]]

    @patch("accounts.views.send_email_to_assigned_user.delay")
    def test_put_with_the_same_body_still_clears_teams(
        self, _mail, admin_client, world, seeded, admin_profile
    ):
        """PUT stays a full replace for every client. This is why the phone
        had to stop using it, and why no client should send a partial PUT."""
        account = world["account"]
        response = admin_client.put(
            _url(account), self._payload(world, seeded, admin_profile), format="json"
        )
        assert response.status_code == 200, response.content
        assert account.teams.count() == 0


class TestPatchAnnouncesNewAssignees:
    """PUT always emailed a newly assigned person; PATCH never did."""

    @patch("accounts.views.send_email_to_assigned_user.delay")
    def test_a_new_assignee_is_emailed(
        self, mail, admin_client, world, admin_profile, user_profile
    ):
        account = world["account"]
        account.assigned_to.add(user_profile)
        response = admin_client.patch(
            _url(account),
            {"assigned_to": [str(user_profile.id), str(admin_profile.id)]},
            format="json",
        )
        assert response.status_code == 200, response.content
        mail.assert_called_once()
        assert mail.call_args[0][0] == [admin_profile.id]

    @patch("accounts.views.send_email_to_assigned_user.delay")
    def test_nobody_is_emailed_when_nobody_is_new(
        self, mail, admin_client, world, user_profile
    ):
        account = world["account"]
        account.assigned_to.add(user_profile)
        for body in ({"assigned_to": [str(user_profile.id)]}, {"city": "York"}):
            response = admin_client.patch(_url(account), body, format="json")
            assert response.status_code == 200, response.content
        mail.assert_not_called()
