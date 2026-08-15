"""A portal token must not be usable against the internal API.

These go through the full Django test client on purpose. The view-level tests
elsewhere use APIRequestFactory and call views directly, which bypasses
MIDDLEWARE entirely. That is exactly how the public-portal 403 outage recorded
in backend/docs/PORTAL_RLS.md survived a green test run for as long as it did.
"""

import pytest
from rest_framework.test import APIClient

from common.portal_auth import mint_portal_token
from contacts.models import Contact


@pytest.fixture
def portal_contact(org_a):
    return Contact.objects.create(
        org=org_a, first_name="Pat", last_name="Smith", email="pat@example.com"
    )


@pytest.fixture
def portal_client(portal_contact):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {mint_portal_token(portal_contact)}")
    return client


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    ["/api/cases/", "/api/contacts/", "/api/leads/", "/api/accounts/", "/api/org/"],
)
def test_portal_token_is_refused_on_internal_endpoints(portal_client, path):
    assert portal_client.get(path).status_code == 403


@pytest.mark.django_db
def test_portal_token_is_refused_on_an_internal_write(portal_client):
    response = portal_client.post("/api/cases/", {"name": "Nope"}, format="json")
    assert response.status_code == 403


@pytest.mark.django_db
def test_internal_client_still_reaches_internal_endpoints(admin_client):
    """The guard has to be able to say yes. Otherwise it is just an outage."""
    assert admin_client.get("/api/cases/").status_code == 200


@pytest.mark.django_db
def test_an_internal_jwt_cannot_read_the_portal(admin_client):
    """The realm boundary in the other direction.

    An agent's own token must not authenticate as a customer, because the portal
    derives "whose case is this" from the credential's contact_id and an
    internal token has none.
    """
    assert admin_client.get("/api/portal/cases/").status_code in (401, 403)


@pytest.mark.django_db
def test_a_personal_access_token_cannot_read_the_portal(admin_profile, org_a):
    """`portal` is in API_RESOURCES as vocabulary, not as a reachable surface.

    Pinning it here so that comment in common/scopes.py stays true: the portal
    views pin their authentication_classes, so a PAT is refused whatever scopes
    it carries, including the unrestricted empty list.
    """
    from common.models import PersonalAccessToken

    # scopes=[] is the unrestricted case, so this is the most privileged PAT
    # that can exist. If any PAT could reach the portal, this one would.
    raw, _pat = PersonalAccessToken.generate(admin_profile, "portal probe")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert client.get("/api/portal/cases/").status_code in (401, 403)


@pytest.mark.django_db
def test_an_org_api_key_cannot_read_the_portal(org_a):
    """The other non-interactive credential, same answer."""
    client = APIClient()
    client.credentials(HTTP_TOKEN=org_a.api_key)
    assert client.get("/api/portal/cases/").status_code in (401, 403)
