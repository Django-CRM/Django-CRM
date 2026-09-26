import socket
from unittest import mock

import pytest

from webhooks.models import WebhookEndpoint

PUBLIC_IP = "93.184.216.34"


def addrinfo(*ips):
    """What `socket.getaddrinfo` returns for a host resolving to `ips`."""
    out = []
    for ip in ips:
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        out.append((family, socket.SOCK_STREAM, 6, "", (ip, 443)))
    return out


@pytest.fixture
def public_dns():
    """Every host resolves to one public address. No real DNS in tests."""
    with mock.patch(
        "webhooks.ssrf.socket.getaddrinfo", return_value=addrinfo(PUBLIC_IP)
    ) as patched:
        yield patched


@pytest.fixture
def endpoint(org_a, admin_profile):
    """Created by org A's admin: an endpoint whose creator is not an admin
    is paused before its first send (webhooks/ownership.py)."""
    return WebhookEndpoint.objects.create(
        org=org_a,
        url="https://hooks.example.com/in",
        events=["lead.created", "lead.updated", "lead.deleted"],
        created_by=admin_profile.user,
    )
