"""Portal sign-in: enumeration safety, rate limiting, and token lifecycle."""

from datetime import timedelta

import pytest
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone
from rest_framework.test import APIClient

from common.models import PortalLoginToken
from contacts.models import Contact


@pytest.fixture
def contact(org_a):
    return Contact.objects.create(
        org=org_a, first_name="Pat", last_name="Smith", email="pat@example.com"
    )


def _request_url(org):
    return f"/api/portal/login/{org.id}/request/"


def _verify_url(org):
    return f"/api/portal/login/{org.id}/verify/"


@pytest.mark.django_db
class TestLoginRequest:
    def test_known_email_mints_a_token(self, contact, org_a):
        response = APIClient().post(
            _request_url(org_a), {"email": "pat@example.com"}, format="json"
        )
        assert response.status_code == 200
        assert PortalLoginToken.objects.filter(contact=contact).count() == 1

    def test_email_match_is_case_insensitive(self, contact, org_a):
        APIClient().post(
            _request_url(org_a), {"email": "PAT@Example.COM"}, format="json"
        )
        assert PortalLoginToken.objects.count() == 1

    def test_every_outcome_looks_identical(self, contact, org_a, org_b):
        """No enumeration of contacts, and none of orgs either."""
        known = APIClient().post(
            _request_url(org_a), {"email": "pat@example.com"}, format="json"
        )
        unknown_email = APIClient().post(
            _request_url(org_a), {"email": "nobody@example.com"}, format="json"
        )
        wrong_org = APIClient().post(
            _request_url(org_b), {"email": "pat@example.com"}, format="json"
        )
        malformed = APIClient().post(
            _request_url(org_a), {"email": "not-an-email"}, format="json"
        )

        statuses = {
            known.status_code,
            unknown_email.status_code,
            wrong_org.status_code,
            malformed.status_code,
        }
        bodies = {
            known.json()["message"],
            unknown_email.json()["message"],
            wrong_org.json()["message"],
            malformed.json()["message"],
        }
        assert statuses == {200}
        assert len(bodies) == 1
        # Only the genuine one actually minted anything.
        assert PortalLoginToken.objects.count() == 1

    def test_contact_in_another_org_is_not_reachable(self, contact, org_b):
        """The same address in two orgs is two unrelated relationships."""
        APIClient().post(
            _request_url(org_b), {"email": "pat@example.com"}, format="json"
        )
        assert PortalLoginToken.objects.count() == 0

    def test_inactive_contact_gets_no_token(self, contact, org_a):
        contact.is_active = False
        contact.save()
        APIClient().post(
            _request_url(org_a), {"email": "pat@example.com"}, format="json"
        )
        assert PortalLoginToken.objects.count() == 0

    def test_contact_without_an_email_cannot_be_resolved(self, org_a):
        Contact.objects.create(org=org_a, first_name="No", last_name="Email")
        APIClient().post(_request_url(org_a), {"email": ""}, format="json")
        assert PortalLoginToken.objects.count() == 0

    def test_rate_limited_after_five_in_an_hour(self, contact, org_a):
        for _ in range(5):
            APIClient().post(
                _request_url(org_a), {"email": "pat@example.com"}, format="json"
            )
        assert PortalLoginToken.objects.count() == 5
        APIClient().post(
            _request_url(org_a), {"email": "pat@example.com"}, format="json"
        )
        assert PortalLoginToken.objects.count() == 5

    def test_the_row_stores_only_a_hash_of_the_code(self, contact, org_a):
        """The email carries the code. The row must not be able to reproduce it."""
        APIClient().post(
            _request_url(org_a), {"email": "pat@example.com"}, format="json"
        )
        token = PortalLoginToken.objects.get()
        assert token.code_hash
        assert not token.code_hash.isdigit()
        assert check_password("000000", token.code_hash) is False

    def test_new_request_retires_the_previous_token(self, contact, org_a):
        APIClient().post(
            _request_url(org_a), {"email": "pat@example.com"}, format="json"
        )
        first = PortalLoginToken.objects.get()
        APIClient().post(
            _request_url(org_a), {"email": "pat@example.com"}, format="json"
        )
        first.refresh_from_db()
        assert first.is_used is True


@pytest.mark.django_db
class TestLoginVerify:
    """Six emailed digits are the whole credential for ten minutes, so the
    attempt counter is the only thing standing between them and an attacker.
    That is why this is tested harder than the amount of code suggests."""

    def _mint(self, contact, org, code="123456", **kwargs):
        defaults = {
            "org": org,
            "contact": contact,
            "code_hash": make_password(code),
            "expires_at": timezone.now() + timedelta(minutes=10),
        }
        defaults.update(kwargs)
        return PortalLoginToken.objects.create(**defaults)

    def _verify(self, org, code="123456", email="pat@example.com"):
        return APIClient().post(
            _verify_url(org), {"email": email, "code": code}, format="json"
        )

    def test_correct_code_returns_an_access_token(self, contact, org_a):
        self._mint(contact, org_a)
        response = self._verify(org_a)
        assert response.status_code == 200
        body = response.json()
        assert body["access_token"]
        assert body["contact"]["email"] == "pat@example.com"

    def test_a_code_is_single_use(self, contact, org_a):
        self._mint(contact, org_a)
        assert self._verify(org_a).status_code == 200
        assert self._verify(org_a).status_code == 400

    def test_expired_code_is_refused(self, contact, org_a):
        self._mint(contact, org_a, expires_at=timezone.now() - timedelta(minutes=1))
        assert self._verify(org_a).status_code == 400

    def test_a_code_cannot_be_redeemed_at_another_org(self, contact, org_a, org_b):
        """The same address in two orgs is two unrelated relationships."""
        self._mint(contact, org_a)
        assert self._verify(org_b).status_code == 400

    def test_a_code_for_a_contact_with_none_outstanding_is_refused(
        self, contact, org_a
    ):
        assert self._verify(org_a).status_code == 400

    def test_an_unknown_email_is_refused(self, contact, org_a):
        self._mint(contact, org_a)
        assert self._verify(org_a, email="nobody@example.com").status_code == 400

    def test_contact_deactivated_after_minting_cannot_redeem(self, contact, org_a):
        self._mint(contact, org_a)
        contact.is_active = False
        contact.save()
        assert self._verify(org_a).status_code == 400

    def test_a_request_with_no_credential_is_refused(self, org_a):
        assert (
            APIClient().post(_verify_url(org_a), {}, format="json").status_code == 400
        )

    def test_wrong_code_is_refused_and_counted(self, contact, org_a):
        token_obj = self._mint(contact, org_a)
        assert self._verify(org_a, code="000000").status_code == 400
        token_obj.refresh_from_db()
        assert token_obj.attempts == 1

    def test_the_row_is_burned_after_five_wrong_guesses(self, contact, org_a):
        """Six digits are guessable if guesses are free."""
        token_obj = self._mint(contact, org_a)
        for _ in range(5):
            self._verify(org_a, code="000000")
        token_obj.refresh_from_db()
        assert token_obj.is_used is True

        # The correct code no longer helps: the row is spent.
        assert self._verify(org_a).status_code == 400
