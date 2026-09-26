"""The webhook management API: who may use it, what it accepts, what it shows."""

from unittest import mock

import pytest
from django.test import Client

from common.models import PersonalAccessToken, Profile, User
from common.testing import _make_authenticated_client
from webhooks import views
from webhooks.emit import envelope, queue_delivery
from webhooks.models import FAILED, PENDING, WebhookDelivery, WebhookEndpoint
from webhooks.tests.conftest import addrinfo

BASE = "/api/webhooks/"
VALID = {
    "url": "https://hooks.example.com/in",
    "events": ["lead.created", "deal.won"],
    "description": "Zapier",
}


@pytest.fixture(autouse=True)
def dns_and_broker(public_dns):
    with mock.patch("webhooks.emit.deliver_webhook.delay"):
        yield


@pytest.fixture
def superuser_client(org_a):
    user = User.objects.create_user(
        email="root@test.com", password="x", is_superuser=True, is_staff=True
    )
    profile = Profile.objects.create(user=user, org=org_a, role="USER", is_active=True)
    return _make_authenticated_client(user, org_a, profile)


@pytest.fixture
def settled_delivery(endpoint):
    delivery = queue_delivery(
        endpoint, "lead.created", envelope(endpoint.org_id, "lead.created", {})
    )
    WebhookDelivery.objects.filter(pk=delivery.pk).update(
        status=FAILED, next_attempt_at=None
    )
    return delivery


def _every_route(endpoint, delivery):
    return [
        ("get", BASE, None),
        ("post", BASE, VALID),
        ("get", f"{BASE}{endpoint.id}/", None),
        ("patch", f"{BASE}{endpoint.id}/", {"description": "x"}),
        ("delete", f"{BASE}{endpoint.id}/", None),
        ("post", f"{BASE}{endpoint.id}/test/", None),
        ("post", f"{BASE}{endpoint.id}/rotate-secret/", None),
        ("get", f"{BASE}{endpoint.id}/deliveries/", None),
        ("post", f"{BASE}deliveries/{delivery.id}/redeliver/", None),
    ]


class TestWhoMayUseIt:
    def test_a_member_is_refused_on_every_route(
        self, user_client, endpoint, settled_delivery
    ):
        secret = endpoint.secret
        for method, url, body in _every_route(endpoint, settled_delivery):
            response = getattr(user_client, method)(url, body, format="json")
            assert response.status_code == 403, (method, url)
        endpoint.refresh_from_db()
        assert endpoint.secret == secret
        assert WebhookEndpoint.objects.count() == 1

    def test_an_admin_is_allowed_on_every_route(
        self, admin_client, endpoint, settled_delivery
    ):
        expected = [200, 201, 200, 200, 204, 404, 404, 404, 404]
        # Delete runs before the per-endpoint actions, so those 404 after it.
        # Run them against a live endpoint first to prove they are allowed.
        for method, url, body in _every_route(endpoint, settled_delivery)[5:]:
            response = getattr(admin_client, method)(url, body, format="json")
            assert response.status_code in (200, 202), (method, url)
        codes = [
            getattr(admin_client, m)(u, b, format="json").status_code
            for m, u, b in _every_route(endpoint, settled_delivery)
        ]
        assert codes == expected

    def test_a_superuser_counts_as_an_admin(self, superuser_client, endpoint):
        assert superuser_client.get(BASE).status_code == 200
        response = superuser_client.post(BASE, VALID, format="json")
        assert response.status_code == 201

    def test_anonymous_is_refused(self, unauthenticated_client):
        assert unauthenticated_client.get(BASE).status_code in (401, 403)

    def test_an_admins_api_token_is_refused(self, admin_profile, endpoint):
        """A token-made webhook would keep exporting after the token is
        revoked, so `/api/webhooks/` is on the credential deny-list."""
        raw, _ = PersonalAccessToken.generate(profile=admin_profile, name="cli")
        client = Client()
        auth = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}
        assert client.get(BASE, **auth).status_code == 403
        response = client.post(BASE, VALID, content_type="application/json", **auth)
        assert response.status_code == 403
        assert WebhookEndpoint.objects.count() == 1
        # The same token still reaches an ordinary endpoint.
        assert client.get("/api/leads/", **auth).status_code == 200

    def test_permission_class_answers_both_ways(self, admin_profile, user_profile):
        check = views.IsOrgAdminOrSuperuser()
        assert check.has_permission(mock.Mock(profile=admin_profile), None) is True
        assert check.has_permission(mock.Mock(profile=user_profile), None) is False
        assert check.has_permission(mock.Mock(profile=None), None) is False


class TestAnotherOrg:
    def test_another_orgs_ids_answer_404(
        self, org_b_client, endpoint, settled_delivery
    ):
        for method, url, body in _every_route(endpoint, settled_delivery)[2:]:
            response = getattr(org_b_client, method)(url, body, format="json")
            assert response.status_code == 404, (method, url)
        assert WebhookEndpoint.objects.filter(pk=endpoint.pk).exists()

    def test_the_list_holds_only_this_orgs_endpoints(self, org_b_client, endpoint):
        assert org_b_client.get(BASE).json()["endpoints"] == []

    def test_a_malformed_id_is_404(self, admin_client):
        assert admin_client.get(f"{BASE}not-a-uuid/").status_code == 404


class TestTheSecret:
    def test_shown_once_on_create_and_never_in_list_or_detail(self, admin_client):
        created = admin_client.post(BASE, VALID, format="json").json()
        secret = created["secret"]
        assert secret.startswith("whsec_") and len(secret) > 40
        assert created["secret_hint"] == f"whsec_...{secret[-4:]}"
        listed = admin_client.get(BASE)
        detail = admin_client.get(f"{BASE}{created['id']}/")
        patched = admin_client.patch(
            f"{BASE}{created['id']}/", {"description": "y"}, format="json"
        )
        for response in (listed, detail, patched):
            assert secret not in response.content.decode()
            assert "secret" not in (
                response.json()["endpoints"][0]
                if "endpoints" in response.json()
                else response.json()
            )

    def test_a_client_cannot_choose_the_secret(self, admin_client):
        response = admin_client.post(
            BASE, {**VALID, "secret": "whsec_mine"}, format="json"
        )
        assert response.status_code == 400
        assert not WebhookEndpoint.objects.filter(secret="whsec_mine").exists()

    def test_rotate_replaces_it_and_shows_the_new_one_once(
        self, admin_client, endpoint
    ):
        old = endpoint.secret
        response = admin_client.post(f"{BASE}{endpoint.id}/rotate-secret/")
        assert response.status_code == 200
        endpoint.refresh_from_db()
        assert endpoint.secret != old
        assert response.json()["secret"] == endpoint.secret
        assert endpoint.secret not in admin_client.get(BASE).content.decode()


class TestValidation:
    @pytest.mark.parametrize(
        "events",
        [[], ["lead.exploded"], ["ping"], "lead.created", [1], None],
    )
    def test_events_must_come_from_the_catalogue(self, admin_client, events):
        response = admin_client.post(BASE, {**VALID, "events": events}, format="json")
        assert response.status_code == 400
        assert WebhookEndpoint.objects.count() == 0

    def test_events_are_deduplicated_in_catalogue_order(self, admin_client):
        response = admin_client.post(
            BASE,
            {**VALID, "events": ["deal.won", "lead.created", "deal.won"]},
            format="json",
        )
        assert response.json()["events"] == ["lead.created", "deal.won"]

    @pytest.mark.parametrize(
        "url",
        [
            "http://hooks.example.com/in",
            "https://user:pw@hooks.example.com/in",
            "https://hooks.example.com:8443/in",
            "https://127.0.0.1/in",
            "https://169.254.169.254/latest/meta-data/",
        ],
    )
    def test_unsafe_urls_are_refused(self, admin_client, url, settings):
        settings.DEBUG = False
        response = admin_client.post(BASE, {**VALID, "url": url}, format="json")
        assert response.status_code == 400
        assert "url" in response.json()

    def test_a_name_resolving_to_a_private_address_is_refused(self, admin_client):
        with mock.patch(
            "webhooks.ssrf.socket.getaddrinfo", return_value=addrinfo("10.0.0.9")
        ):
            response = admin_client.post(BASE, VALID, format="json")
        assert response.status_code == 400

    def test_changing_the_url_is_checked_again(self, admin_client, endpoint):
        response = admin_client.patch(
            f"{BASE}{endpoint.id}/", {"url": "https://127.0.0.1/"}, format="json"
        )
        assert response.status_code == 400
        endpoint.refresh_from_db()
        assert endpoint.url == "https://hooks.example.com/in"

    def test_format_is_one_of_two(self, admin_client):
        response = admin_client.post(BASE, {**VALID, "format": "xml"}, format="json")
        assert response.status_code == 400

    def test_org_is_taken_from_the_caller_not_the_body(
        self, admin_client, org_a, org_b
    ):
        response = admin_client.post(
            BASE, {**VALID, "org": str(org_b.id)}, format="json"
        )
        assert response.status_code == 201
        assert WebhookEndpoint.objects.get().org_id == org_a.id

    def test_endpoint_count_is_capped(self, admin_client, org_a):
        for _ in range(views.MAX_ENDPOINTS_PER_ORG):
            WebhookEndpoint.objects.create(
                org=org_a, url="https://h.example.com/", events=["lead.created"]
            )
        response = admin_client.post(BASE, VALID, format="json")
        assert response.status_code == 400

    def test_list_carries_the_catalogue(self, admin_client):
        body = admin_client.get(BASE).json()
        modules = [group["module"] for group in body["event_catalogue"]]
        assert modules == [
            "lead",
            "contact",
            "account",
            "deal",
            "ticket",
            "invoice",
            "task",
        ]
        assert body["limit"] == views.MAX_ENDPOINTS_PER_ORG

    def test_turning_back_on_clears_the_reason(self, admin_client, endpoint):
        WebhookEndpoint.objects.filter(pk=endpoint.pk).update(
            is_active=False, disabled_reason="The endpoint answered 410 Gone."
        )
        response = admin_client.patch(
            f"{BASE}{endpoint.id}/", {"is_active": True}, format="json"
        )
        assert response.json()["disabled_reason"] == ""
        assert response.json()["is_active"] is True


class TestActions:
    def test_test_queues_a_ping(self, admin_client, endpoint):
        response = admin_client.post(f"{BASE}{endpoint.id}/test/")
        assert response.status_code == 202
        delivery = WebhookDelivery.objects.get(pk=response.json()["id"])
        assert delivery.event == "ping"
        assert delivery.status == PENDING
        assert delivery.payload["data"] == {"endpoint_id": str(endpoint.id)}

    def test_test_refuses_a_turned_off_endpoint(self, admin_client, endpoint):
        endpoint.is_active = False
        endpoint.save()
        assert admin_client.post(f"{BASE}{endpoint.id}/test/").status_code == 400

    def test_deliveries_are_paginated_and_scoped_to_the_endpoint(
        self, admin_client, endpoint, org_a
    ):
        other = WebhookEndpoint.objects.create(
            org=org_a, url="https://h.example.com/", events=["lead.created"]
        )
        payload = envelope(org_a.id, "lead.created", {})
        for _ in range(3):
            queue_delivery(endpoint, "lead.created", payload)
        queue_delivery(other, "lead.created", payload)
        body = admin_client.get(f"{BASE}{endpoint.id}/deliveries/?limit=2").json()
        assert body["count"] == 3
        assert len(body["results"]) == 2
        assert all(r["endpoint"] == str(endpoint.id) for r in body["results"])

    def test_redeliver_makes_a_new_row_for_the_same_event(
        self, admin_client, settled_delivery
    ):
        response = admin_client.post(
            f"{BASE}deliveries/{settled_delivery.id}/redeliver/"
        )
        assert response.status_code == 202
        copy = WebhookDelivery.objects.get(pk=response.json()["id"])
        assert copy.pk != settled_delivery.pk
        assert str(copy.event_id) == str(settled_delivery.event_id)
        assert copy.payload == settled_delivery.payload
        assert copy.status == PENDING

    def test_redeliver_refuses_a_delivery_still_in_flight(self, admin_client, endpoint):
        pending = queue_delivery(
            endpoint, "lead.created", envelope(endpoint.org_id, "lead.created", {})
        )
        response = admin_client.post(f"{BASE}deliveries/{pending.id}/redeliver/")
        assert response.status_code == 400

    def test_delete_takes_the_log_with_it(self, admin_client, settled_delivery):
        endpoint_id = settled_delivery.endpoint_id
        assert admin_client.delete(f"{BASE}{endpoint_id}/").status_code == 204
        assert not WebhookDelivery.objects.filter(pk=settled_delivery.pk).exists()
