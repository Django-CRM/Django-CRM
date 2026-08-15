"""The portal credential, and the two directions it must not travel."""

import pytest
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory

from common.portal_auth import (
    IsPortalContact,
    PortalContactAuthentication,
    mint_portal_token,
    peek_portal_claims,
)
from common.serializer import OrgAwareRefreshToken
from contacts.models import Contact


@pytest.fixture
def portal_contact(org_a):
    return Contact.objects.create(
        org=org_a, first_name="Pat", last_name="Smith", email="pat@example.com"
    )


def _request(raw=None):
    request = APIRequestFactory().get("/api/portal/cases/")
    if raw is not None:
        request.META["HTTP_AUTHORIZATION"] = f"Bearer {raw}"
    return request


@pytest.mark.django_db
def test_minted_token_carries_the_realm_and_the_pair(portal_contact, org_a):
    claims = peek_portal_claims(mint_portal_token(portal_contact))
    assert claims["typ"] == "portal"
    assert claims["org_id"] == str(org_a.id)
    assert claims["contact_id"] == str(portal_contact.id)


@pytest.mark.django_db
def test_principal_is_not_an_authenticated_user(portal_contact):
    """The fail-safe.

    If a portal token ever reaches an internal view, DRF's own IsAuthenticated
    has to deny it without anyone having remembered to add a check.
    """
    request = _request(mint_portal_token(portal_contact))
    principal, _ = PortalContactAuthentication().authenticate(request)
    assert principal.is_authenticated is False
    assert principal.contact == portal_contact
    assert request.portal_contact == portal_contact


@pytest.mark.django_db
def test_internal_token_is_refused_by_portal_auth(admin_user, org_a, admin_profile):
    token = OrgAwareRefreshToken.for_user_and_org(admin_user, org_a, admin_profile)
    with pytest.raises(AuthenticationFailed):
        PortalContactAuthentication().authenticate(_request(token.access_token))


@pytest.mark.django_db
def test_inactive_contact_is_refused(portal_contact):
    raw = mint_portal_token(portal_contact)
    portal_contact.is_active = False
    portal_contact.save()
    with pytest.raises(AuthenticationFailed):
        PortalContactAuthentication().authenticate(_request(raw))


@pytest.mark.django_db
def test_no_credential_is_not_an_error(portal_contact):
    """No header means "not my business", so the next class gets a turn."""
    assert PortalContactAuthentication().authenticate(_request()) is None


@pytest.mark.django_db
def test_peek_returns_none_for_an_internal_token(admin_user, org_a, admin_profile):
    token = OrgAwareRefreshToken.for_user_and_org(admin_user, org_a, admin_profile)
    assert peek_portal_claims(str(token.access_token)) is None


def test_peek_returns_none_for_rubbish():
    assert peek_portal_claims("not-a-jwt") is None


@pytest.mark.django_db
def test_permission_returns_true_and_false(portal_contact, org_a, org_b):
    """Both directions, per the repo rule about gates that cannot say no."""
    permission = IsPortalContact()

    allowed = _request()
    allowed.portal_contact = portal_contact
    allowed.org = org_a
    assert permission.has_permission(allowed, None) is True

    missing = _request()
    missing.portal_contact = None
    missing.org = org_a
    assert permission.has_permission(missing, None) is False

    wrong_org = _request()
    wrong_org.portal_contact = portal_contact
    wrong_org.org = org_b
    assert permission.has_permission(wrong_org, None) is False
