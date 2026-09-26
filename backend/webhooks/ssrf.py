"""Sending a request to an admin-supplied URL without letting it reach inside.

A webhook URL is chosen by an org admin, and the server then makes requests to
it. Left alone, that is a way to make the server call its own metadata service
(169.254.169.254), a database on the private network, or localhost. So:

* only `https` (plus `http` when `DEBUG` is on), on port 443 (plus 80 in
  `DEBUG`), with no credentials in the URL;
* the host is resolved and EVERY address it resolves to must be public, which
  refuses loopback, private, link-local, CGNAT, multicast, reserved and
  unspecified ranges, unique-local and site-local IPv6, and IPv6 forms that
  embed an IPv4 address (mapped, 6to4, NAT64) whose IPv4 is any of those;
* the check runs when the URL is saved and again before every attempt;
* the request is sent to the exact address that passed the check. Resolving
  once to validate and letting the HTTP client resolve again to connect is the
  DNS rebinding hole: a hostile resolver answers "public" the first time and
  "127.0.0.1" the second. Here the connection is opened to the validated IP,
  with the original name used only for the Host header, SNI and certificate
  verification, so there is no second lookup to lie in.

Redirects are never followed, since a 3xx to an internal address would undo
all of the above.
"""

import ipaddress
import re
import socket
from urllib.parse import urlsplit

import certifi
import urllib3
from django.conf import settings

DEFAULT_PORTS = {"https": 443, "http": 80}
_HOSTNAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class UnsafeDestination(Exception):
    """The URL may not be requested. The message is safe to show an admin."""


def _allowed_schemes():
    return ("https", "http") if settings.DEBUG else ("https",)


def _allowed_ports():
    return {443, 80} if settings.DEBUG else {443}


def parse(url):
    """Split `url` into (scheme, host, port, request target), or refuse it."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise UnsafeDestination("Enter a valid URL.") from None
    scheme = parts.scheme.lower()
    if scheme not in _allowed_schemes():
        raise UnsafeDestination("The URL must start with https://.")
    if parts.username is not None or parts.password is not None:
        raise UnsafeDestination("The URL must not contain a username or password.")
    host = parts.hostname
    if not host:
        raise UnsafeDestination("Enter a valid URL.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise UnsafeDestination("Enter a valid host name.") from None
        if not _HOSTNAME.fullmatch(host):
            raise UnsafeDestination("Enter a valid host name.")
    port = port or DEFAULT_PORTS[scheme]
    if port not in _allowed_ports():
        raise UnsafeDestination("Only the standard HTTPS port (443) is allowed.")
    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"
    return scheme, host, port, target


def is_blocked(ip):
    """Whether `ip` (an `ipaddress` object) is anywhere a webhook must not reach."""
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return is_blocked(ip.ipv4_mapped)
        if ip.sixtofour is not None and is_blocked(ip.sixtofour):
            return True
        if ip in _NAT64:
            return is_blocked(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if ip.teredo is not None or ip.is_site_local:
            return True
    elif ip in _CGNAT:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


def resolve(host, port):
    """Every address `host` resolves to, all of them checked, or refuse."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if is_blocked(literal):
            raise UnsafeDestination(
                "The URL points at a private or reserved network address."
            )
        return [literal]
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise UnsafeDestination("The host name could not be resolved.") from None
    addresses = []
    for info in infos:
        # A scoped IPv6 literal carries "%zone"; the zone is not part of the
        # address and would make ip_address() raise.
        ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if ip not in addresses:
            addresses.append(ip)
    if not addresses:
        raise UnsafeDestination("The host name could not be resolved.")
    if any(is_blocked(ip) for ip in addresses):
        raise UnsafeDestination(
            "The URL points at a private or reserved network address."
        )
    return addresses


def validate_url(url):
    """Refuse `url` unless it is a public HTTPS destination. Returns nothing."""
    _, host, port, _ = parse(url)
    resolve(host, port)


def post(url, body, headers, timeout):
    """POST `body` to `url` through the checks above; return the HTTP status.

    Raises `UnsafeDestination` for a refused URL and urllib3's own exceptions
    for a network failure. The response body is never read.
    """
    scheme, host, port, target = parse(url)
    address = str(resolve(host, port)[0])
    if scheme == "https":
        pool = urllib3.HTTPSConnectionPool(
            address,
            port,
            server_hostname=host,
            assert_hostname=host,
            cert_reqs="CERT_REQUIRED",
            ca_certs=certifi.where(),
            maxsize=1,
            retries=False,
        )
    else:
        pool = urllib3.HTTPConnectionPool(address, port, maxsize=1, retries=False)
    host_header = f"[{host}]" if ":" in host else host
    if port != DEFAULT_PORTS[scheme]:
        host_header = f"{host_header}:{port}"
    try:
        response = pool.urlopen(
            "POST",
            target,
            body=body,
            headers={**headers, "Host": host_header},
            redirect=False,
            retries=False,
            assert_same_host=False,
            preload_content=False,
            timeout=urllib3.Timeout(total=timeout),
        )
        status = response.status
        response.close()
        return status
    finally:
        pool.close()
