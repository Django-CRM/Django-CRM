"""The liveness probe answers without a tenant.

`RequireOrgContext` 403s any resolvable path with no org context, and
`/healthz/` has no org to carry: it is fetched by load balancers, uptime
monitors and container orchestrators, none of which hold a JWT. While it was
gated, every health check read the service as down.

Paired with a request that must still be refused, so the exemption is pinned as
narrow rather than as "the middleware stopped enforcing".
"""

import pytest


@pytest.mark.django_db
class TestHealthzNoOrgContext:
    def test_probe_is_reachable_unauthenticated(self, unauthenticated_client):
        response = unauthenticated_client.get("/healthz/")
        assert response.status_code == 200

    def test_other_paths_still_require_org_context(self, unauthenticated_client):
        # Same client, same absence of a token. Only /healthz/ is exempt.
        response = unauthenticated_client.get("/api/accounts/")
        assert response.status_code == 403
