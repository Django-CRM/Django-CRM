"""Two gaps on `/api/accounts/`.

1. `has_account_access` ignored `user.is_superuser`, while the deal, lead and
   invoice rules (`has_deal_access`, `has_lead_access`, `has_object_access`)
   all let a Django superuser through. It now takes `user` the way
   `has_deal_access` does, and every caller passes `request.user`.
2. The `leads` catalogue on the list endpoint sent full `LeadSerializer` rows
   (email, phone, address, comments, attachments) for a picker no client
   reads beyond a label. It now sends picker fields, from `visible_leads_qs`.
"""

from unittest import mock

import pytest

from accounts.access import has_account_access
from accounts.models import Account
from leads.models import Lead


@pytest.fixture
def admins_account(org_a, admin_user):
    account = Account.objects.create(name="Not yours", org=org_a)
    Account.objects.filter(pk=account.pk).update(created_by=admin_user)
    return account


@pytest.fixture
def superuser(regular_user):
    """The `user_client` caller, with a plain USER profile, made superuser."""
    regular_user.is_superuser = True
    regular_user.save(update_fields=["is_superuser"])
    return regular_user


@pytest.mark.django_db
class TestSuperuserAccountAccess:
    def test_predicate_refuses_a_plain_member(
        self, user_profile, regular_user, admins_account
    ):
        assert has_account_access(user_profile, regular_user, admins_account) is False

    def test_predicate_admits_a_superuser_without_an_admin_profile(
        self, user_profile, superuser, admins_account
    ):
        assert user_profile.role != "ADMIN"
        assert has_account_access(user_profile, superuser, admins_account) is True

    def test_plain_member_is_refused_the_detail(self, user_client, admins_account):
        assert user_client.get(f"/api/accounts/{admins_account.id}/").status_code == 403

    def test_superuser_may_open_the_detail(
        self, superuser, user_client, admins_account
    ):
        response = user_client.get(f"/api/accounts/{admins_account.id}/")
        assert response.status_code == 200, response.content

    def test_superuser_sees_the_account_in_the_list(
        self, superuser, user_client, admins_account
    ):
        body = user_client.get("/api/accounts/").json()
        ids = {row["id"] for row in body["active_accounts"]["open_accounts"]}
        assert str(admins_account.id) in ids

    def test_plain_member_does_not_see_it_in_the_list(
        self, user_client, admins_account
    ):
        body = user_client.get("/api/accounts/").json()
        ids = {row["id"] for row in body["active_accounts"]["open_accounts"]}
        assert str(admins_account.id) not in ids

    def test_superuser_may_send_mail_from_it(
        self, superuser, user_client, admins_account
    ):
        with mock.patch("accounts.views.send_email.delay"):
            response = user_client.post(
                f"/api/accounts/{admins_account.id}/create_mail/",
                {
                    "from_email": "sales@example.com",
                    "message_subject": "Hello",
                    "message_body": "Body",
                },
            )
        assert response.status_code == 200, response.content


@pytest.mark.django_db
class TestLeadCatalogue:
    @pytest.fixture
    def lead(self, org_a, admin_user):
        lead = Lead.objects.create(
            title="Big deal",
            first_name="Lee",
            last_name="Prospect",
            email="lee@private.example",
            phone="555-0100",
            address_line="1 Secret Lane",
            org=org_a,
            status="assigned",
        )
        Lead.objects.filter(pk=lead.pk).update(created_by=admin_user)
        return lead

    def test_rows_carry_picker_fields_only(self, admin_client, lead):
        (row,) = admin_client.get("/api/accounts/").json()["leads"]
        assert row == {
            "id": str(lead.id),
            "title": "Big deal",
            "first_name": "Lee",
            "last_name": "Prospect",
        }

    def test_plain_member_does_not_get_a_lead_they_cannot_open(self, user_client, lead):
        assert user_client.get(f"/api/leads/{lead.id}/").status_code == 403
        assert user_client.get("/api/accounts/").json()["leads"] == []

    def test_superuser_gets_the_lead_their_detail_route_serves(
        self, superuser, user_client, lead
    ):
        assert user_client.get(f"/api/leads/{lead.id}/").status_code == 200
        ids = {row["id"] for row in user_client.get("/api/accounts/").json()["leads"]}
        assert str(lead.id) in ids
