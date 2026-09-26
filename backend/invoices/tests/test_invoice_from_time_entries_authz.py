"""Who may bill time, and which time may be billed to which account (D36).

``POST /api/invoices/from-time-entries/`` checked only for an org, so any
member could invoice any account from any entry in the org, a colleague's
included, and then read the invoice as its creator. It also never checked
that an entry's ticket belonged to the account being billed.

The rule now: org admins and Django superusers only, and every entry must be
on a ticket for the chosen account. ``user_client`` is a plain member (role
USER); the ``superuser`` fixture turns that same caller into a superuser
without giving the profile an admin role.
"""

import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from accounts.models import Account
from cases.models import Case, TimeEntry
from invoices.models import Invoice, InvoiceLineItem

URL = "/api/invoices/from-time-entries/"


@pytest.fixture
def account(org_a):
    return Account.objects.create(name="Billed Co", org=org_a)


@pytest.fixture
def other_account(org_a):
    return Account.objects.create(name="Someone Else", org=org_a)


@pytest.fixture
def superuser(regular_user):
    regular_user.is_superuser = True
    regular_user.save(update_fields=["is_superuser"])
    return regular_user


def _entry(org, profile, account):
    case = Case.objects.create(
        name="Billable work", status="New", priority="Normal", org=org, account=account
    )
    return TimeEntry.objects.create(
        org=org,
        case=case,
        profile=profile,
        started_at=timezone.now() - datetime.timedelta(hours=2),
        ended_at=timezone.now() - datetime.timedelta(hours=1),
        billable=True,
        hourly_rate=Decimal("100.00"),
        currency="USD",
    )


def _post(client, account_id, entry_ids):
    return client.post(
        URL, {"account_id": account_id, "entry_ids": entry_ids}, format="json"
    )


def _assert_nothing_written(*entries):
    assert not Invoice.objects.exists()
    assert not InvoiceLineItem.objects.exists()
    for entry in entries:
        entry.refresh_from_db()
        assert entry.invoice_id is None


@pytest.mark.django_db
class TestWhoMayBillTime:
    def test_admin_may_bill(self, admin_client, org_a, account, admin_profile):
        entry = _entry(org_a, admin_profile, account)

        response = _post(admin_client, str(account.id), [str(entry.id)])

        assert response.status_code == 201, response.content
        invoice = Invoice.objects.get(id=response.json()["invoice_id"])
        assert invoice.account == account
        entry.refresh_from_db()
        assert entry.invoice_id == invoice.id

    def test_admin_may_bill_a_members_time(
        self, admin_client, org_a, account, user_profile
    ):
        entry = _entry(org_a, user_profile, account)

        response = _post(admin_client, str(account.id), [str(entry.id)])

        assert response.status_code == 201, response.content

    def test_superuser_without_an_admin_profile_may_bill(
        self, superuser, user_client, org_a, account, user_profile
    ):
        assert user_profile.role != "ADMIN"
        entry = _entry(org_a, user_profile, account)

        response = _post(user_client, str(account.id), [str(entry.id)])

        assert response.status_code == 201, response.content
        entry.refresh_from_db()
        assert entry.invoice_id is not None

    def test_member_is_refused_even_for_their_own_time(
        self, user_client, org_a, account, user_profile
    ):
        entry = _entry(org_a, user_profile, account)

        response = _post(user_client, str(account.id), [str(entry.id)])

        assert response.status_code == 403, response.content
        _assert_nothing_written(entry)

    def test_member_is_refused_a_colleagues_time(
        self, user_client, org_a, account, admin_profile
    ):
        entry = _entry(org_a, admin_profile, account)

        response = _post(user_client, str(account.id), [str(entry.id)])

        assert response.status_code == 403, response.content
        _assert_nothing_written(entry)

    def test_member_is_refused_before_the_body_is_read(self, user_client):
        """A 400 here would tell a member which ids parse, so the role check
        comes first."""
        response = _post(user_client, "not-a-uuid", ["nope"])

        assert response.status_code == 403, response.content


@pytest.mark.django_db
class TestEntriesMustBeOnTheAccountsTickets:
    def test_entry_on_another_accounts_ticket_is_refused(
        self, admin_client, org_a, account, other_account, admin_profile
    ):
        entry = _entry(org_a, admin_profile, other_account)

        response = _post(admin_client, str(account.id), [str(entry.id)])

        assert response.status_code == 400, response.content
        assert "not on a ticket for this account" in response.json()["message"]
        _assert_nothing_written(entry)

    def test_entry_on_a_ticket_with_no_account_is_refused(
        self, admin_client, org_a, account, admin_profile
    ):
        entry = _entry(org_a, admin_profile, None)

        response = _post(admin_client, str(account.id), [str(entry.id)])

        assert response.status_code == 400, response.content
        _assert_nothing_written(entry)

    def test_one_foreign_entry_refuses_the_whole_batch(
        self, admin_client, org_a, account, other_account, admin_profile
    ):
        good = _entry(org_a, admin_profile, account)
        foreign = _entry(org_a, admin_profile, other_account)

        response = _post(admin_client, str(account.id), [str(good.id), str(foreign.id)])

        assert response.status_code == 400, response.content
        _assert_nothing_written(good, foreign)


@pytest.mark.django_db
class TestMalformedIds:
    @pytest.mark.parametrize("account_id", ["not-a-uuid", 123, {"id": "x"}])
    def test_malformed_account_id_is_a_400(
        self, admin_client, org_a, account, admin_profile, account_id
    ):
        entry = _entry(org_a, admin_profile, account)

        response = _post(admin_client, account_id, [str(entry.id)])

        assert response.status_code == 400, response.content
        _assert_nothing_written(entry)

    @pytest.mark.parametrize(
        "entry_ids", [["not-a-uuid"], [123], [["nested"]], {"id": "x"}, 7]
    )
    def test_malformed_entry_ids_are_a_400(self, admin_client, account, entry_ids):
        response = _post(admin_client, str(account.id), entry_ids)

        assert response.status_code == 400, response.content
        assert not Invoice.objects.exists()

    @pytest.mark.parametrize("entry_ids", [[], None, ""])
    def test_missing_entry_ids_are_a_400(self, admin_client, account, entry_ids):
        response = _post(admin_client, str(account.id), entry_ids)

        assert response.status_code == 400, response.content

    def test_missing_account_id_is_a_400(self, admin_client):
        response = _post(admin_client, None, ["00000000-0000-0000-0000-000000000000"])

        assert response.status_code == 400, response.content

    def test_a_repeated_id_is_billed_once(
        self, admin_client, org_a, account, admin_profile
    ):
        entry = _entry(org_a, admin_profile, account)
        ids = [str(entry.id), str(entry.id), str(entry.id).upper()]

        response = _post(admin_client, str(account.id), ids)

        assert response.status_code == 201, response.content
        assert response.json()["line_count"] == 1


@pytest.mark.django_db
class TestAnotherOrgStaysInvisible:
    def test_another_orgs_entry_is_a_404(
        self, admin_client, org_a, org_b, account, profile_b
    ):
        other_account = Account.objects.create(name="Other org", org=org_b)
        entry = _entry(org_b, profile_b, other_account)

        response = _post(admin_client, str(account.id), [str(entry.id)])

        assert response.status_code == 404, response.content
        _assert_nothing_written(entry)

    def test_another_orgs_account_is_a_404(
        self, admin_client, org_a, org_b, account, admin_profile
    ):
        other_account = Account.objects.create(name="Other org", org=org_b)
        entry = _entry(org_a, admin_profile, account)

        response = _post(admin_client, str(other_account.id), [str(entry.id)])

        assert response.status_code == 404, response.content
        _assert_nothing_written(entry)
