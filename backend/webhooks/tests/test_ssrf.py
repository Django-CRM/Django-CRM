"""The SSRF guard: which URLs and addresses a webhook may reach.

Every refusal is pinned by the range it belongs to, including the IPv6 forms
that smuggle an IPv4 address, and the rebinding case where DNS answers public
at the check and private at the connect.
"""

import ipaddress
from unittest import mock

import pytest

from webhooks import ssrf
from webhooks.tests.conftest import PUBLIC_IP, addrinfo

BLOCKED = [
    "127.0.0.1",  # loopback
    "127.8.8.8",
    "10.0.0.1",  # private
    "172.16.0.1",
    "192.168.1.1",
    "169.254.169.254",  # link-local, the cloud metadata service
    "100.64.0.1",  # CGNAT
    "100.127.255.254",
    "224.0.0.1",  # multicast
    "240.0.0.1",  # reserved
    "255.255.255.255",
    "0.0.0.0",  # unspecified
    "192.0.0.8",  # IETF protocol assignments
    "198.18.0.1",  # benchmarking
    "::1",
    "::",
    "fe80::1",  # link-local v6
    "fc00::1",  # unique-local
    "fd12:3456::1",
    "fec0::1",  # site-local
    "ff02::1",  # multicast v6
    "::ffff:127.0.0.1",  # IPv4-mapped
    "::ffff:169.254.169.254",
    "::ffff:10.1.2.3",
    "2002:7f00:1::1",  # 6to4 wrapping 127.0.0.1
    "2002:a9fe:a9fe::1",  # 6to4 wrapping 169.254.169.254
    "64:ff9b::a00:1",  # NAT64 wrapping 10.0.0.1
    "64:ff9b::a9fe:a9fe",  # NAT64 wrapping 169.254.169.254
    "2001:0:4136:e378:8000:63bf:3fff:fdd2",  # Teredo
    "2001:db8::1",  # documentation
]

ALLOWED = [PUBLIC_IP, "8.8.8.8", "2606:4700:4700::1111", "64:ff9b::808:808"]


@pytest.mark.parametrize("address", BLOCKED)
def test_blocked_ranges(address):
    assert ssrf.is_blocked(ipaddress.ip_address(address)) is True


@pytest.mark.parametrize("address", ALLOWED)
def test_public_addresses_pass(address):
    assert ssrf.is_blocked(ipaddress.ip_address(address)) is False


@pytest.mark.parametrize(
    "url, message",
    [
        ("http://hooks.example.com/in", "https://"),
        ("ftp://hooks.example.com/in", "https://"),
        ("https://user:pw@hooks.example.com/in", "username or password"),
        ("https://user@hooks.example.com/in", "username or password"),
        ("https://hooks.example.com:8443/in", "port"),
        ("https://hooks.example.com:22/in", "port"),
        ("https://hooks.example.com:99999/in", "valid URL"),
        ("https:///in", "valid URL"),
        ("https://hooks_example!.com/in", "host name"),
        ("not a url", "https://"),
    ],
)
def test_parse_refuses(url, message, settings):
    settings.DEBUG = False
    with pytest.raises(ssrf.UnsafeDestination, match=message):
        ssrf.parse(url)


def test_parse_keeps_path_and_query(settings):
    settings.DEBUG = False
    assert ssrf.parse("https://Hooks.Example.com/a/b?x=1#frag") == (
        "https",
        "hooks.example.com",
        443,
        "/a/b?x=1",
    )


def test_http_and_port_80_only_in_debug(settings):
    settings.DEBUG = True
    assert ssrf.parse("http://hooks.example.com/in")[2] == 80
    with pytest.raises(ssrf.UnsafeDestination):
        ssrf.parse("http://hooks.example.com:8080/in")
    settings.DEBUG = False
    with pytest.raises(ssrf.UnsafeDestination):
        ssrf.parse("http://hooks.example.com/in")


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "10.0.0.5", "::1"])
def test_a_name_resolving_to_a_private_address_is_refused(address):
    with mock.patch("webhooks.ssrf.socket.getaddrinfo", return_value=addrinfo(address)):
        with pytest.raises(ssrf.UnsafeDestination, match="private or reserved"):
            ssrf.validate_url("https://innocent.example.com/in")


def test_one_private_answer_among_public_ones_is_refused():
    with mock.patch(
        "webhooks.ssrf.socket.getaddrinfo",
        return_value=addrinfo(PUBLIC_IP, "10.0.0.1"),
    ):
        with pytest.raises(ssrf.UnsafeDestination):
            ssrf.validate_url("https://mixed.example.com/in")


def test_an_ip_literal_url_is_checked_too():
    with pytest.raises(ssrf.UnsafeDestination):
        ssrf.validate_url("https://169.254.169.254/latest/meta-data/")
    with pytest.raises(ssrf.UnsafeDestination):
        ssrf.validate_url("https://[::ffff:127.0.0.1]/")


def test_an_unresolvable_host_is_refused():
    import socket

    with mock.patch(
        "webhooks.ssrf.socket.getaddrinfo", side_effect=socket.gaierror("nope")
    ):
        with pytest.raises(ssrf.UnsafeDestination, match="could not be resolved"):
            ssrf.validate_url("https://nowhere.invalid/in")


def test_a_public_name_passes(public_dns):
    ssrf.validate_url("https://hooks.example.com/in")


class _FakePool:
    instances = []

    def __init__(self, host, port, **kwargs):
        self.host, self.port, self.kwargs = host, port, kwargs
        self.urlopen_kwargs = None
        _FakePool.instances.append(self)

    def urlopen(self, method, target, **kwargs):
        self.method, self.target, self.urlopen_kwargs = method, target, kwargs
        return mock.Mock(status=204)

    def close(self):
        pass


@pytest.fixture
def fake_pool():
    _FakePool.instances = []
    with mock.patch("webhooks.ssrf.urllib3.HTTPSConnectionPool", _FakePool):
        yield _FakePool


def test_post_connects_to_the_checked_address_with_the_original_name(
    public_dns, fake_pool
):
    status = ssrf.post(
        "https://hooks.example.com/in?k=1", b"{}", {"X-A": "1"}, timeout=10
    )
    assert status == 204
    pool = fake_pool.instances[0]
    # The socket goes to the IP that passed the check...
    assert pool.host == PUBLIC_IP
    assert pool.port == 443
    # ...while TLS still verifies the certificate for the real name.
    assert pool.kwargs["server_hostname"] == "hooks.example.com"
    assert pool.kwargs["assert_hostname"] == "hooks.example.com"
    assert pool.kwargs["cert_reqs"] == "CERT_REQUIRED"
    assert pool.target == "/in?k=1"
    assert pool.urlopen_kwargs["headers"]["Host"] == "hooks.example.com"
    assert pool.urlopen_kwargs["redirect"] is False
    assert pool.urlopen_kwargs["retries"] is False


def test_dns_rebinding_between_check_and_connect_cannot_redirect_the_socket(
    fake_pool,
):
    """A resolver that answers public first and loopback second. The name is
    resolved exactly once per attempt, and that answer is what is dialled, so
    the second answer is never used."""
    answers = mock.Mock(side_effect=[addrinfo(PUBLIC_IP), addrinfo("127.0.0.1")])
    with mock.patch("webhooks.ssrf.socket.getaddrinfo", answers):
        ssrf.post("https://rebind.example.com/in", b"{}", {}, timeout=10)
    assert answers.call_count == 1
    assert fake_pool.instances[0].host == PUBLIC_IP


def test_a_name_that_turned_private_since_it_was_saved_is_refused_at_send(
    fake_pool,
):
    with mock.patch(
        "webhooks.ssrf.socket.getaddrinfo", return_value=addrinfo("10.0.0.7")
    ):
        with pytest.raises(ssrf.UnsafeDestination):
            ssrf.post("https://rebind.example.com/in", b"{}", {}, timeout=10)
    assert fake_pool.instances == []
