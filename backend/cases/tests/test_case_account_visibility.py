"""A member may link a ticket only to an account they can open (D38).

`validate_account` refused another org's account and nothing else, so a member
could attach a ticket to any account in the org, including one the account
detail view would answer 403 for. The ticket CSV import went further: it
resolved `account_name` across the whole org, so its "No account named ..."
row error said which hidden accounts existed.

Both now ask `accounts.access`: the API through `has_account_access`, the
import and the ticket form's picker through `visible_accounts_qs`. Every
refusal reads the same as an account that does not exist.
"""

import csv
import io
import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import Account
from cases.models import Case
from conftest import rls_org

CASES_URL = "/api/cases/"
PREVIEW = "/api/cases/import/preview/"
COMMIT = "/api/cases/import/commit/"


def _account(org, name, created_by=None):
    account = Account.objects.create(name=name, org=org)
    Account.objects.filter(pk=account.pk).update(created_by=created_by)
    return account


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


def _create(client, name, account_id):
    return client.post(
        CASES_URL,
        {
            "name": name,
            "status": "New",
            "priority": "Normal",
            "account": str(account_id),
        },
        format="json",
    )


def _refusal(response):
    assert response.status_code == 400, response.content
    return response.json()["errors"]["account"]


@pytest.mark.django_db
class TestTicketApiAccountLink:
    def test_member_may_link_an_account_they_are_assigned_to(
        self, user_client, assigned
    ):
        response = _create(user_client, "Assigned link", assigned.id)
        assert response.status_code == 200, response.content
        assert Case.objects.get(name="Assigned link").account_id == assigned.id

    def test_member_may_link_an_account_they_created(
        self, user_client, org_a, regular_user
    ):
        mine = _account(org_a, "Mine Co", created_by=regular_user)
        response = _create(user_client, "Own link", mine.id)
        assert response.status_code == 200, response.content
        assert Case.objects.get(name="Own link").account_id == mine.id

    def test_hidden_account_reads_like_an_unknown_id(self, user_client, hidden):
        hidden_refusal = _refusal(_create(user_client, "Hidden link", hidden.id))
        unknown_refusal = _refusal(_create(user_client, "Unknown link", uuid.uuid4()))
        assert hidden_refusal == unknown_refusal == ["No such account."]
        assert not Case.objects.filter(
            name__in=["Hidden link", "Unknown link"]
        ).exists()

    def test_admin_may_link_any_account_in_the_org(self, admin_client, hidden):
        response = _create(admin_client, "Admin link", hidden.id)
        assert response.status_code == 200, response.content
        assert Case.objects.get(name="Admin link").account_id == hidden.id

    def test_superuser_may_link_any_account_in_the_org(
        self, superuser, user_client, hidden
    ):
        response = _create(user_client, "Superuser link", hidden.id)
        assert response.status_code == 200, response.content
        assert Case.objects.get(name="Superuser link").account_id == hidden.id

    @pytest.mark.parametrize("caller", ["member", "admin", "superuser"])
    def test_other_org_account_is_refused_for_everyone(
        self, caller, request, user_client, admin_client, foreign
    ):
        if caller == "superuser":
            request.getfixturevalue("superuser")
        client = admin_client if caller == "admin" else user_client
        assert _refusal(_create(client, "Foreign link", foreign.id)) == [
            "No such account."
        ]
        assert not Case.objects.filter(name="Foreign link").exists()

    def test_account_cannot_be_changed_after_creation(
        self, user_client, org_a, regular_user, user_profile, assigned, hidden
    ):
        case = Case.objects.create(
            name="Existing", status="New", priority="Normal", org=org_a
        )
        Case.objects.filter(pk=case.pk).update(created_by=regular_user)
        case.assigned_to.add(user_profile)
        for target in (assigned, hidden):
            response = user_client.patch(
                f"{CASES_URL}{case.id}/", {"account": str(target.id)}, format="json"
            )
            assert response.status_code == 200, response.content
            case.refresh_from_db()
            assert case.account_id is None

    def test_picker_offers_exactly_what_the_save_path_accepts(
        self, user_client, assigned, hidden
    ):
        offered = {
            row["id"] for row in user_client.get(CASES_URL).json()["accounts_list"]
        }
        assert str(assigned.id) in offered
        assert str(hidden.id) not in offered

    def test_picker_offers_a_superuser_every_account(
        self, superuser, user_client, assigned, hidden
    ):
        offered = {
            row["id"] for row in user_client.get(CASES_URL).json()["accounts_list"]
        }
        assert {str(assigned.id), str(hidden.id)} <= offered


def _csv(rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["name", "status", "priority", "account_name"])
    writer.writerows(rows)
    return SimpleUploadedFile(
        "tickets.csv", buf.getvalue().encode("utf-8"), content_type="text/csv"
    )


def _messages(body):
    return {e["row"]: e["message"] for e in body["errors"]}


@pytest.mark.django_db
class TestTicketImportAccountVisibility:
    def test_hidden_and_missing_accounts_get_the_same_error(
        self, user_client, importer, hidden
    ):
        response = user_client.post(
            PREVIEW,
            {
                "file": _csv(
                    [
                        ["Hidden", "New", "High", "Hidden Holdings"],
                        ["Missing", "New", "High", "Nobody Inc"],
                    ]
                )
            },
            format="multipart",
        )
        assert response.status_code == 200, response.content
        body = response.json()
        assert body["summary"]["valid"] == 0
        messages = _messages(body)
        assert messages[1].replace("Hidden Holdings", "NAME") == messages[2].replace(
            "Nobody Inc", "NAME"
        )
        assert {e["field"] for e in body["errors"]} == {"account_name"}

    def test_visible_account_resolves_case_insensitively(
        self, user_client, importer, assigned
    ):
        response = user_client.post(
            COMMIT,
            {"file": _csv([["Imported", "New", "High", "assigned INDUSTRIES"]])},
            format="multipart",
        )
        assert response.status_code == 200, response.content
        assert response.json()["created"] == 1
        assert Case.objects.get(name="Imported").account_id == assigned.id

    def test_commit_refuses_a_hidden_account(self, user_client, importer, hidden):
        response = user_client.post(
            COMMIT,
            {"file": _csv([["Sneaky", "New", "High", "Hidden Holdings"]])},
            format="multipart",
        )
        assert response.json()["created"] == 0
        assert not Case.objects.filter(name="Sneaky").exists()

    def test_admin_resolves_an_account_a_member_cannot(self, admin_client, hidden):
        response = admin_client.post(
            COMMIT,
            {"file": _csv([["By admin", "New", "High", "Hidden Holdings"]])},
            format="multipart",
        )
        assert response.json()["created"] == 1, response.content
        assert Case.objects.get(name="By admin").account_id == hidden.id

    def test_superuser_resolves_an_account_a_member_cannot(
        self, superuser, user_client, importer, hidden
    ):
        response = user_client.post(
            COMMIT,
            {"file": _csv([["By superuser", "New", "High", "Hidden Holdings"]])},
            format="multipart",
        )
        assert response.json()["created"] == 1, response.content
        assert Case.objects.get(name="By superuser").account_id == hidden.id
