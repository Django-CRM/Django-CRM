"""A member may link a contact only to an account they can open (D39).

`CreateContactSerializer.validate_account` refused another org's account and
nothing else, so a member could attach a contact to any account in the org.
Linking one hands the contact to that account's assignees
(`has_contact_access`), so a member could push a person into a company they
cannot see. The contact CSV import resolved `account_name` across the whole
org, so its "No account named ..." row error also said which hidden accounts
existed.

Both now ask `accounts.access`, as tickets do since D38: the API through
`has_account_access`, the import through `visible_accounts_qs`. Every refusal
reads the same as an account that does not exist. On edit, the account the
contact is already linked to is kept, since the caller's form never chose it.
"""

import csv
import io
import uuid
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import Account
from conftest import rls_org
from contacts.models import Contact

CONTACTS_URL = "/api/contacts/"
PREVIEW = "/api/contacts/import/preview/"
COMMIT = "/api/contacts/import/commit/"

pytestmark = pytest.mark.django_db


def _account(org, name, created_by=None):
    account = Account.objects.create(name=name, org=org)
    Account.objects.filter(pk=account.pk).update(created_by=created_by)
    return account


@pytest.fixture(autouse=True)
def _no_mail():
    with patch("contacts.views.send_email_to_assigned_user.delay"):
        yield


@pytest.fixture
def hidden(org_a, admin_user):
    return _account(org_a, "Hidden Holdings", created_by=admin_user)


@pytest.fixture
def assigned(org_a, admin_user, user_profile):
    account = _account(org_a, "Assigned Industries", created_by=admin_user)
    account.assigned_to.add(user_profile)
    return account


@pytest.fixture
def foreign(org_b, user_b):
    with rls_org(org_b):
        return _account(org_b, "Other Org Ltd", created_by=user_b)


@pytest.fixture
def superuser(regular_user):
    """The `user_client` caller, with a plain USER profile, made superuser."""
    regular_user.is_superuser = True
    regular_user.save(update_fields=["is_superuser"])
    return regular_user


@pytest.fixture
def importer(user_profile):
    user_profile.has_sales_access = True
    user_profile.save(update_fields=["has_sales_access"])
    return user_profile


@pytest.fixture
def mine_at_hidden(org_a, regular_user, hidden):
    """The member's own contact, linked by an admin to an account they cannot open."""
    contact = Contact.objects.create(
        first_name="Ada", last_name="Lovelace", org=org_a, account=hidden
    )
    Contact.objects.filter(pk=contact.pk).update(created_by=regular_user)
    hidden.contacts.add(contact)
    return contact


def _create(client, first_name, account_id):
    return client.post(
        CONTACTS_URL,
        {"first_name": first_name, "last_name": "Person", "account": str(account_id)},
        format="json",
    )


def _refusal(response):
    assert response.status_code == 400, response.content
    return response.json()["errors"]["account"]


class TestContactApiAccountLink:
    def test_member_may_link_an_account_they_are_assigned_to(
        self, user_client, assigned
    ):
        response = _create(user_client, "Assigned", assigned.id)
        assert response.status_code == 200, response.content
        contact = Contact.objects.get(first_name="Assigned")
        assert contact.account_id == assigned.id
        assert contact in assigned.contacts.all()

    def test_member_may_link_an_account_they_created(
        self, user_client, org_a, regular_user
    ):
        mine = _account(org_a, "Mine Co", created_by=regular_user)
        response = _create(user_client, "Own", mine.id)
        assert response.status_code == 200, response.content
        assert Contact.objects.get(first_name="Own").account_id == mine.id

    def test_hidden_account_reads_like_an_unknown_id(self, user_client, hidden):
        hidden_refusal = _refusal(_create(user_client, "Hidden", hidden.id))
        unknown_refusal = _refusal(_create(user_client, "Unknown", uuid.uuid4()))
        assert hidden_refusal == unknown_refusal == ["No such account."]
        assert not Contact.objects.filter(first_name__in=["Hidden", "Unknown"])
        assert not hidden.contacts.exists()

    def test_admin_may_link_any_account_in_the_org(self, admin_client, hidden):
        response = _create(admin_client, "Admin", hidden.id)
        assert response.status_code == 200, response.content
        assert Contact.objects.get(first_name="Admin").account_id == hidden.id

    def test_superuser_may_link_any_account_in_the_org(
        self, superuser, user_client, hidden
    ):
        response = _create(user_client, "Superuser", hidden.id)
        assert response.status_code == 200, response.content
        assert Contact.objects.get(first_name="Superuser").account_id == hidden.id

    @pytest.mark.parametrize("caller", ["member", "admin", "superuser"])
    def test_other_org_account_is_refused_for_everyone(
        self, caller, request, user_client, admin_client, foreign
    ):
        if caller == "superuser":
            request.getfixturevalue("superuser")
        client = admin_client if caller == "admin" else user_client
        assert _refusal(_create(client, "Foreign", foreign.id)) == ["No such account."]
        assert not Contact.objects.filter(first_name="Foreign").exists()

    def test_patch_may_not_move_a_contact_to_a_hidden_account(
        self, user_client, org_a, regular_user, hidden
    ):
        contact = Contact.objects.create(first_name="Mover", last_name="P", org=org_a)
        Contact.objects.filter(pk=contact.pk).update(created_by=regular_user)
        response = user_client.patch(
            f"{CONTACTS_URL}{contact.id}/", {"account": str(hidden.id)}, format="json"
        )
        assert _refusal(response) == ["No such account."]
        contact.refresh_from_db()
        assert contact.account_id is None
        assert not hidden.contacts.exists()

    def test_put_may_not_move_a_contact_to_a_hidden_account(
        self, user_client, org_a, regular_user, hidden
    ):
        contact = Contact.objects.create(first_name="Mover", last_name="P", org=org_a)
        Contact.objects.filter(pk=contact.pk).update(created_by=regular_user)
        response = user_client.put(
            f"{CONTACTS_URL}{contact.id}/",
            {"first_name": "Mover", "last_name": "P", "account": str(hidden.id)},
            format="json",
        )
        assert _refusal(response) == ["No such account."]
        contact.refresh_from_db()
        assert contact.account_id is None

    def test_patch_resending_a_stored_hidden_account_keeps_it(
        self, user_client, hidden, mine_at_hidden
    ):
        # What the mobile form does: it sends `account` on every save.
        response = user_client.patch(
            f"{CONTACTS_URL}{mine_at_hidden.id}/",
            {"title": "Countess", "account": str(hidden.id)},
            format="json",
        )
        assert response.status_code == 200, response.content
        mine_at_hidden.refresh_from_db()
        assert mine_at_hidden.title == "Countess"
        assert mine_at_hidden.account_id == hidden.id
        assert mine_at_hidden in hidden.contacts.all()

    def test_patch_without_account_keeps_a_stored_hidden_account(
        self, user_client, hidden, mine_at_hidden
    ):
        response = user_client.patch(
            f"{CONTACTS_URL}{mine_at_hidden.id}/", {"title": "Countess"}, format="json"
        )
        assert response.status_code == 200, response.content
        mine_at_hidden.refresh_from_db()
        assert mine_at_hidden.account_id == hidden.id

    def test_put_resending_a_stored_hidden_account_keeps_it(
        self, user_client, hidden, mine_at_hidden
    ):
        response = user_client.put(
            f"{CONTACTS_URL}{mine_at_hidden.id}/",
            {"first_name": "Ada", "last_name": "King", "account": str(hidden.id)},
            format="json",
        )
        assert response.status_code == 200, response.content
        mine_at_hidden.refresh_from_db()
        assert mine_at_hidden.last_name == "King"
        assert mine_at_hidden.account_id == hidden.id

    def test_picker_offers_exactly_what_the_save_path_accepts(
        self, user_client, assigned, hidden
    ):
        # Both clients build the contact form's account picker from this list.
        body = user_client.get("/api/accounts/?limit=200").json()
        offered = {row["id"] for row in body["active_accounts"]["open_accounts"]}
        assert str(assigned.id) in offered
        assert str(hidden.id) not in offered


def _csv(rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["first_name", "last_name", "account_name"])
    writer.writerows(rows)
    return SimpleUploadedFile(
        "contacts.csv", buf.getvalue().encode("utf-8"), content_type="text/csv"
    )


def _messages(body):
    return {e["row"]: e["message"] for e in body["errors"]}


class TestContactImportAccountVisibility:
    def test_hidden_and_missing_accounts_get_the_same_error(
        self, user_client, importer, hidden
    ):
        response = user_client.post(
            PREVIEW,
            {
                "file": _csv(
                    [
                        ["Hidden", "Row", "Hidden Holdings"],
                        ["Missing", "Row", "Nobody Inc"],
                    ]
                )
            },
            format="multipart",
        )
        assert response.status_code == 200, response.content
        body = response.json()
        assert body["summary"]["valid"] == 0
        messages = _messages(body)
        assert messages[1] == "No account you can open is named 'Hidden Holdings'"
        assert messages[1].replace("Hidden Holdings", "NAME") == messages[2].replace(
            "Nobody Inc", "NAME"
        )
        assert {e["field"] for e in body["errors"]} == {"account_name"}

    def test_visible_account_resolves_case_insensitively(
        self, user_client, importer, assigned
    ):
        response = user_client.post(
            COMMIT,
            {"file": _csv([["Imported", "Row", "assigned INDUSTRIES"]])},
            format="multipart",
        )
        assert response.status_code == 200, response.content
        assert response.json()["created"] == 1
        contact = Contact.objects.get(first_name="Imported")
        assert contact.account_id == assigned.id
        assert contact in assigned.contacts.all()

    def test_commit_refuses_a_hidden_account(self, user_client, importer, hidden):
        response = user_client.post(
            COMMIT,
            {"file": _csv([["Sneaky", "Row", "Hidden Holdings"]])},
            format="multipart",
        )
        assert response.status_code == 400, response.content
        assert response.json()["created"] == 0
        assert not Contact.objects.filter(first_name="Sneaky").exists()
        assert not hidden.contacts.exists()

    def test_admin_resolves_an_account_a_member_cannot(self, admin_client, hidden):
        response = admin_client.post(
            COMMIT,
            {"file": _csv([["ByAdmin", "Row", "Hidden Holdings"]])},
            format="multipart",
        )
        assert response.json()["created"] == 1, response.content
        assert Contact.objects.get(first_name="ByAdmin").account_id == hidden.id

    def test_superuser_resolves_an_account_a_member_cannot(
        self, superuser, user_client, importer, hidden
    ):
        response = user_client.post(
            COMMIT,
            {"file": _csv([["BySuperuser", "Row", "Hidden Holdings"]])},
            format="multipart",
        )
        assert response.json()["created"] == 1, response.content
        assert Contact.objects.get(first_name="BySuperuser").account_id == hidden.id

    def test_other_org_account_is_missing_even_for_an_admin(
        self, admin_client, foreign
    ):
        response = admin_client.post(
            PREVIEW,
            {"file": _csv([["Abroad", "Row", "Other Org Ltd"]])},
            format="multipart",
        )
        assert _messages(response.json()) == {
            1: "No account you can open is named 'Other Org Ltd'"
        }
