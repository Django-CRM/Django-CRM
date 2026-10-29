"""Extensions add scope resources and credential paths from `AppConfig.ready`.

A registered resource is one a token can be scoped to, exactly like a built-in
root. A registered credential path is refused to every token and to the org
key, whatever their scopes, exactly like `CREDENTIAL_PATHS`.
"""

import pytest
from rest_framework.test import APIClient

from common import scopes
from common.models import PersonalAccessToken


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    """A fresh registry per test, so nothing registered here outlives it."""
    monkeypatch.setattr(scopes, "_registered_resources", set())
    monkeypatch.setattr(scopes, "_registered_credential_paths", [])


def _client(**headers):
    client = APIClient()
    client.credentials(**headers)
    return client


def _token(profile, scopes_=None):
    raw, _ = PersonalAccessToken.generate(profile=profile, name="t", scopes=scopes_)
    return _client(HTTP_AUTHORIZATION=f"Bearer {raw}")


def test_registered_resource_scopes_a_token(admin_client, admin_profile):
    create = {"name": "widgets", "scopes": ["widgets:read"]}
    with pytest.raises(ValueError):
        scopes.normalize_scope("widgets:read")
    refused = admin_client.post("/api/profile/tokens/", create, format="json")
    assert refused.status_code == 400
    assert scopes.check_request(["widgets:read"], "GET", "/api/widgets/") is not None

    scopes.register_resource("widgets")
    scopes.register_resource("widgets")

    assert scopes.normalize_scope(" Widgets:READ ") == "widgets:read"
    created = admin_client.post("/api/profile/tokens/", create, format="json")
    assert created.status_code == 201
    assert created.json()["scopes"] == ["widgets:read"]
    assert scopes.required_scopes("GET", "/api/widgets/1/") == ["widgets:read"]
    assert scopes.check_request(["widgets:read"], "GET", "/api/widgets/") is None
    assert "widgets:write" in scopes.check_request(
        ["widgets:read"], "POST", "/api/widgets/"
    )

    client = _token(admin_profile, ["widgets:read"])
    # No route answers there in the core: the scope check let it through.
    assert client.get("/api/widgets/").status_code == 404
    assert client.get("/api/leads/").status_code == 403


@pytest.mark.parametrize("name", ["", "Widgets", "*", "a/b", "a:b", "-a", None])
def test_a_resource_must_be_one_root_segment(name):
    with pytest.raises(ValueError):
        scopes.register_resource(name)


def test_registered_credential_path_refuses_every_token(
    admin_client, admin_profile, org_a
):
    unrestricted = _token(admin_profile)
    reader = _token(admin_profile, ["*:read", "tags:read"])
    org_key = _client(HTTP_TOKEN=org_a.api_key)
    for client in (unrestricted, reader, org_key):
        assert client.get("/api/tags/").status_code == 200

    scopes.register_credential_path("/api/tags/")
    scopes.register_credential_path("/api/tags/")
    assert scopes._registered_credential_paths == ["/api/tags/"]

    for client in (unrestricted, reader, org_key):
        response = client.get("/api/tags/")
        assert response.status_code == 403
        assert "cannot read or manage other credentials" in response.json()["detail"]
    # A signed-in session is not a token, so it still reaches it.
    assert admin_client.get("/api/tags/").status_code == 200


@pytest.mark.parametrize("prefix", ["", "/api/", "/tags/", "/api/tags", None])
def test_a_credential_path_must_be_a_prefix_under_api(prefix):
    with pytest.raises(ValueError):
        scopes.register_credential_path(prefix)
