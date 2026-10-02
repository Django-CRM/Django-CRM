"""Per-client and shared rate limits on the anonymous sign-in request endpoints.

The per-recipient cap (five an hour per email or contact) is tested beside each
view. These cover the layers in `common.throttles` that stop a caller from
getting a fresh bucket by changing the address they ask for.
"""

from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from common.models import MagicLinkToken, PortalLoginToken
from common.request_meta import RELAY_CLIENT_IP_HEADER, RELAY_SECRET_HEADER
from common.throttles import (
    FirstRefusalThrottleMixin,
    MagicLinkGlobalThrottle,
    MagicLinkIPThrottle,
    PortalLoginIPThrottle,
    PortalLoginOrgThrottle,
)
from common.views.auth_views import MagicLinkRequestView
from common.views.portal_auth_views import PortalLoginRequestView
from contacts.models import Contact

MAGIC = "/api/auth/magic-link/request/"
SECRET = "s" * 40


@pytest.fixture(autouse=True)
def _fresh_throttle():
    cache.clear()
    yield
    cache.clear()


def _rate(monkeypatch, throttle, rate):
    monkeypatch.setattr(throttle, "THROTTLE_RATES", {throttle.scope: rate})


def _magic(n, ip):
    with patch("common.tasks.send_magic_link_email.delay") as send:
        response = APIClient().post(
            MAGIC, {"email": f"poc-{n}@example.com"}, format="json", REMOTE_ADDR=ip
        )
    return response.status_code, send.call_count


def _portal(org, email, ip):
    with patch("common.tasks.send_portal_login_email.delay") as send:
        response = APIClient().post(
            f"/api/portal/login/{org.id}/request/",
            {"email": email},
            format="json",
            REMOTE_ADDR=ip,
        )
    return response.status_code, send.call_count


class TestWiring:
    def test_views_are_throttled_per_client_first(self):
        assert MagicLinkRequestView.throttle_classes == [
            MagicLinkIPThrottle,
            MagicLinkGlobalThrottle,
        ]
        assert PortalLoginRequestView.throttle_classes == [
            PortalLoginIPThrottle,
            PortalLoginOrgThrottle,
        ]
        assert issubclass(MagicLinkRequestView, FirstRefusalThrottleMixin)
        assert issubclass(PortalLoginRequestView, FirstRefusalThrottleMixin)


class TestMagicLink:
    def test_new_addresses_do_not_buy_a_new_bucket(self, monkeypatch):
        """The report's PoC: one client, a different email each time."""
        _rate(monkeypatch, MagicLinkIPThrottle, "2/hour")
        results = [_magic(n, "198.51.100.1") for n in range(3)]
        assert results == [(200, 1), (200, 1), (429, 0)]
        assert MagicLinkToken.objects.count() == 2
        # Another client is unaffected.
        assert _magic(9, "198.51.100.2") == (200, 1)

    def test_global_cap_holds_across_clients(self, monkeypatch):
        _rate(monkeypatch, MagicLinkGlobalThrottle, "2/hour")
        results = [_magic(n, f"198.51.100.{n + 1}") for n in range(3)]
        assert results == [(200, 1), (200, 1), (429, 0)]

    def test_a_refused_client_does_not_spend_the_global_budget(self, monkeypatch):
        """Without FirstRefusalThrottleMixin DRF records every request the
        per-IP limit refused in the global bucket too, and one address could
        lock out everyone."""
        _rate(monkeypatch, MagicLinkIPThrottle, "1/hour")
        _rate(monkeypatch, MagicLinkGlobalThrottle, "3/hour")
        flood = [_magic(n, "198.51.100.1")[0] for n in range(5)]
        assert flood == [200, 429, 429, 429, 429]
        assert _magic(10, "198.51.100.2") == (200, 1)
        assert _magic(11, "198.51.100.3") == (200, 1)
        # The global cap itself still binds.
        assert _magic(12, "198.51.100.4") == (429, 0)

    @override_settings(RELAY_SECRET=SECRET)
    def test_relayed_visitors_get_their_own_buckets(self, monkeypatch):
        """The web sign-in page calls from the SvelteKit server's address, so a
        signed visitor address is what keeps all web sign-ins from sharing one
        bucket. An unsigned one is ignored."""
        _rate(monkeypatch, MagicLinkIPThrottle, "1/hour")

        def relayed(n, visitor, secret=SECRET):
            with patch("common.tasks.send_magic_link_email.delay"):
                return (
                    APIClient()
                    .post(
                        MAGIC,
                        {"email": f"web-{n}@example.com"},
                        format="json",
                        REMOTE_ADDR="10.0.0.5",
                        **{
                            RELAY_SECRET_HEADER: secret,
                            RELAY_CLIENT_IP_HEADER: visitor,
                        },
                    )
                    .status_code
                )

        assert relayed(1, "203.0.113.1") == 200
        assert relayed(2, "203.0.113.2") == 200
        assert relayed(3, "203.0.113.1") == 429
        # A forged visitor header without the secret lands in the relay's own
        # bucket, which is still fresh, and then shares it.
        assert relayed(4, "203.0.113.9", secret="wrong" * 10) == 200
        assert relayed(5, "203.0.113.10", secret="wrong" * 10) == 429


class TestPortalLogin:
    @pytest.fixture
    def contacts(self, org_a, org_b):
        return [
            Contact.objects.create(
                org=org,
                first_name="Pat",
                last_name="Smith",
                email=f"pat{n}@example.com",
            )
            for org in (org_a, org_b)
            for n in range(4)
        ]

    def test_per_client_limit_spans_addresses_and_orgs(
        self, monkeypatch, contacts, org_a, org_b
    ):
        _rate(monkeypatch, PortalLoginIPThrottle, "2/hour")
        ip = "198.51.100.1"
        assert _portal(org_a, "pat0@example.com", ip) == (200, 1)
        assert _portal(org_a, "pat1@example.com", ip) == (200, 1)
        # Neither a new address nor another org's id buys a new bucket.
        assert _portal(org_a, "pat2@example.com", ip) == (429, 0)
        assert _portal(org_b, "pat0@example.com", ip) == (429, 0)
        assert _portal(org_a, "pat2@example.com", "198.51.100.2") == (200, 1)
        assert PortalLoginToken.objects.count() == 3

    def test_per_org_limit_holds_across_clients(
        self, monkeypatch, contacts, org_a, org_b
    ):
        _rate(monkeypatch, PortalLoginOrgThrottle, "2/hour")
        assert _portal(org_a, "pat0@example.com", "198.51.100.1") == (200, 1)
        assert _portal(org_a, "pat1@example.com", "198.51.100.2") == (200, 1)
        assert _portal(org_a, "pat2@example.com", "198.51.100.3") == (429, 0)
        # Another org's customers are not locked out.
        assert _portal(org_b, "pat0@example.com", "198.51.100.4") == (200, 1)

    def test_throttled_answer_does_not_depend_on_the_address(
        self, monkeypatch, contacts, org_a
    ):
        _rate(monkeypatch, PortalLoginIPThrottle, "1/hour")
        ip = "198.51.100.1"
        assert _portal(org_a, "pat0@example.com", ip) == (200, 1)
        assert _portal(org_a, "pat1@example.com", ip) == (429, 0)
        assert _portal(org_a, "nobody@example.com", ip) == (429, 0)
