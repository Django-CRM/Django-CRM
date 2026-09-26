"""Converting a lead may not hand the converter an account they cannot open.

Conversion files the lead under the org's account with the same name
(case-insensitive) when one exists, and copies the lead's assignees onto it.
A member could create a lead whose company named an account they were never
given, convert it, and come out assigned to that account and so able to open
it and every contact linked to it.

Now a conversion whose matching account the converter cannot open is refused
with a 400 before anything is written: no account, contact or deal, no
assignee change on the account, no status change, none of the other fields
sent in the same request, and no email. Every entry point that converts is
covered: create (POST), full update (PUT), and partial update (PATCH, with
``status`` or ``is_converted``).
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.contenttypes.models import ContentType
from rest_framework.exceptions import ValidationError

from accounts.models import Account
from common.models import Comment
from contacts.models import Contact
from leads.models import Lead
from leads.services import convert_lead_to_account
from opportunity.models import Opportunity

pytestmark = pytest.mark.django_db

LEADS_URL = "/api/leads/"
LEAD_EMAIL = "lee@convert.test"


def _url(lead):
    return f"{LEADS_URL}{lead.id}/"


def _stamp(obj, user):
    """Set `created_by` after the fact; `BaseModel.save` nulls it outside a
    request."""
    type(obj).objects.filter(pk=obj.pk).update(created_by=user)
    obj.refresh_from_db()
    return obj


@pytest.fixture
def hidden_account(org_a, admin_user, admin_profile):
    """An account the member was never given: an admin made it and holds it."""
    account = _stamp(Account.objects.create(name="Hidden Co", org=org_a), admin_user)
    account.assigned_to.add(admin_profile)
    return account


@pytest.fixture
def lead(org_a, regular_user, user_profile, admin_profile):
    """The member's own lead, whose company names the hidden account."""
    lead = _stamp(
        Lead.objects.create(
            first_name="Lee",
            last_name="Ad",
            email=LEAD_EMAIL,
            company_name="hidden co",
            job_title="Analyst",
            status="in process",
            org=org_a,
        ),
        regular_user,
    )
    lead.assigned_to.add(user_profile)
    Comment.objects.create(
        comment="first call went well",
        content_type=ContentType.objects.get_for_model(Lead),
        object_id=lead.id,
        org=org_a,
    )
    return lead


def _send(client, method, url, body):
    """Send the request with every email task stubbed, and hand back the stubs
    so a test can prove none of them was enqueued."""
    with (
        patch("leads.views.lead_views.send_email_to_assigned_user.delay") as leads,
        patch("accounts.tasks.send_email_to_assigned_user.delay") as accounts,
    ):
        response = getattr(client, method)(url, body, format="json")
    return response, [leads, accounts]


def _assert_refused(response, mailers):
    assert response.status_code == 400, response.content
    body = response.json()
    assert body["error"] is True
    message = body["errors"]["status"][0]
    assert "An account named 'Hidden Co' already exists" in message
    assert "Ask an admin to convert this lead or give you access." in message
    for mailer in mailers:
        mailer.assert_not_called()


def _assert_nothing_changed(lead, hidden_account, admin_profile, user_profile):
    lead.refresh_from_db()
    assert lead.status == "in process"
    assert lead.job_title == "Analyst"
    assert lead.company_name == "hidden co"
    assert set(lead.assigned_to.all()) == {user_profile}
    assert set(hidden_account.assigned_to.all()) == {admin_profile}
    assert Account.objects.count() == 1
    assert not Contact.objects.filter(email__iexact=LEAD_EMAIL).exists()
    assert not Opportunity.objects.exists()
    comment = Comment.objects.get(object_id=lead.id)
    assert comment.content_type == ContentType.objects.get_for_model(Lead)


class TestRefusedWithoutAccountAccess:
    def test_patch_status_is_refused_and_keeps_the_other_fields(
        self, user_client, lead, hidden_account, admin_profile, user_profile
    ):
        response, mailers = _send(
            user_client,
            "patch",
            _url(lead),
            {"status": "converted", "job_title": "Director"},
        )
        _assert_refused(response, mailers)
        _assert_nothing_changed(lead, hidden_account, admin_profile, user_profile)

    def test_patch_is_converted_is_refused(
        self, user_client, lead, hidden_account, admin_profile, user_profile
    ):
        response, mailers = _send(
            user_client,
            "patch",
            _url(lead),
            {"is_converted": True, "job_title": "Director"},
        )
        _assert_refused(response, mailers)
        _assert_nothing_changed(lead, hidden_account, admin_profile, user_profile)

    def test_patch_that_renames_the_company_onto_the_account_is_refused(
        self, user_client, lead, hidden_account, admin_profile, user_profile
    ):
        """The name that counts is the one the save would leave, not the one
        on the lead before it. Otherwise a lead named anything else could be
        renamed onto the account in the same request that converts it."""
        Lead.objects.filter(pk=lead.pk).update(company_name="Harmless Ltd")
        response, mailers = _send(
            user_client,
            "patch",
            _url(lead),
            {"status": "converted", "company_name": "HIDDEN CO"},
        )
        _assert_refused(response, mailers)
        lead.refresh_from_db()
        assert lead.company_name == "Harmless Ltd"
        assert lead.status == "in process"
        assert set(hidden_account.assigned_to.all()) == {admin_profile}

    def test_put_is_refused_and_keeps_every_field(
        self, user_client, lead, hidden_account, admin_profile, user_profile
    ):
        response, mailers = _send(
            user_client,
            "put",
            _url(lead),
            {
                "first_name": "Lee",
                "last_name": "Ad",
                "email": LEAD_EMAIL,
                "company_name": "hidden co",
                "job_title": "Director",
                "status": "converted",
                "assigned_to": [str(admin_profile.id)],
            },
        )
        _assert_refused(response, mailers)
        _assert_nothing_changed(lead, hidden_account, admin_profile, user_profile)

    def test_post_that_creates_and_converts_is_refused(
        self, user_client, hidden_account, admin_profile, user_profile
    ):
        response, mailers = _send(
            user_client,
            "post",
            LEADS_URL,
            {
                "first_name": "New",
                "last_name": "Lead",
                "email": "new@convert.test",
                "company_name": "Hidden Co",
                "status": "converted",
                "assigned_to": [str(user_profile.id)],
            },
        )
        _assert_refused(response, mailers)
        assert not Lead.objects.exists()
        assert set(hidden_account.assigned_to.all()) == {admin_profile}
        assert not Contact.objects.exists()
        assert not Opportunity.objects.exists()

    def test_the_member_still_cannot_open_the_account(
        self, user_client, lead, hidden_account
    ):
        _send(user_client, "patch", _url(lead), {"status": "converted"})
        opened = user_client.get(f"/api/accounts/{hidden_account.id}/")
        assert opened.status_code == 403

    def test_the_service_refuses_on_its_own(
        self, lead, hidden_account, admin_profile, user_profile, regular_user
    ):
        """The views ask before they write; the service asks again, so a
        caller that forgets cannot hand the account over."""
        request = SimpleNamespace(profile=user_profile, user=regular_user)
        with pytest.raises(ValidationError):
            convert_lead_to_account(lead, request)
        _assert_nothing_changed(lead, hidden_account, admin_profile, user_profile)


class TestAllowedWithAccountAccess:
    def _convert(self, client, lead):
        response, _ = _send(client, "patch", _url(lead), {"status": "converted"})
        assert response.status_code == 200, response.content
        return response.json()

    def test_an_assigned_member_converts_into_the_account(
        self, user_client, lead, hidden_account, user_profile, admin_profile
    ):
        hidden_account.assigned_to.add(user_profile)
        body = self._convert(user_client, lead)
        assert body["account_id"] == str(hidden_account.id)
        lead.refresh_from_db()
        assert lead.status == "converted"
        assert set(hidden_account.assigned_to.all()) == {admin_profile, user_profile}
        assert Account.objects.count() == 1

    def test_the_accounts_creator_converts_into_it(
        self, user_client, lead, hidden_account, regular_user
    ):
        _stamp(hidden_account, regular_user)
        body = self._convert(user_client, lead)
        assert body["account_id"] == str(hidden_account.id)

    def test_put_by_an_assigned_member_converts(
        self, user_client, lead, hidden_account, user_profile
    ):
        hidden_account.assigned_to.add(user_profile)
        response, _ = _send(
            user_client,
            "put",
            _url(lead),
            {
                "first_name": "Lee",
                "last_name": "Ad",
                "email": LEAD_EMAIL,
                "company_name": "hidden co",
                "status": "converted",
                "assigned_to": [str(user_profile.id)],
            },
        )
        assert response.status_code == 200, response.content
        assert response.json()["account_id"] == str(hidden_account.id)

    def test_an_admin_converts_a_lead_onto_any_account(
        self, admin_client, lead, hidden_account
    ):
        body = self._convert(admin_client, lead)
        assert body["account_id"] == str(hidden_account.id)
        lead.refresh_from_db()
        assert lead.status == "converted"

    def test_an_admin_post_that_creates_and_converts_is_allowed(
        self, admin_client, hidden_account
    ):
        response, _ = _send(
            admin_client,
            "post",
            LEADS_URL,
            {
                "first_name": "New",
                "last_name": "Lead",
                "email": "new@convert.test",
                "company_name": "Hidden Co",
                "status": "converted",
            },
        )
        assert response.status_code == 200, response.content
        assert response.json()["account_id"] == str(hidden_account.id)

    def test_no_matching_account_creates_one_as_before(
        self, user_client, lead, hidden_account, user_profile
    ):
        Lead.objects.filter(pk=lead.pk).update(company_name="Fresh Ltd")
        body = self._convert(user_client, lead)
        account = Account.objects.get(id=body["account_id"])
        assert account.name == "Fresh Ltd"
        assert account != hidden_account
        assert user_profile in account.assigned_to.all()
        assert Contact.objects.filter(email__iexact=LEAD_EMAIL).exists()

    def test_renaming_the_company_away_from_the_account_converts(
        self, user_client, lead, hidden_account, admin_profile
    ):
        """The same pre-save name, in the allowed direction."""
        response, _ = _send(
            user_client,
            "patch",
            _url(lead),
            {"status": "converted", "company_name": "Elsewhere Inc"},
        )
        assert response.status_code == 200, response.content
        account = Account.objects.get(id=response.json()["account_id"])
        assert account.name == "Elsewhere Inc"
        assert set(hidden_account.assigned_to.all()) == {admin_profile}
