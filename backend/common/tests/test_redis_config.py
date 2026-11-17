from urllib.parse import parse_qs, urlparse

import pytest

from crm.redis_config import normalize_upstash_redis_url


def test_empty_upstash_url_keeps_local_defaults():
    assert normalize_upstash_redis_url("") == ""
    assert normalize_upstash_redis_url("   ") == ""


def test_upstash_tcp_url_requires_verified_tls():
    result = normalize_upstash_redis_url(
        "rediss://default:secret-token@sample.upstash.io:6379"
    )

    parsed = urlparse(result)
    assert parsed.scheme == "rediss"
    assert parsed.hostname == "sample.upstash.io"
    assert parsed.port == 6379
    assert parsed.password == "secret-token"
    assert parse_qs(parsed.query)["ssl_cert_reqs"] == ["required"]


def test_upstash_tls_verification_cannot_be_disabled():
    result = normalize_upstash_redis_url(
        "rediss://default:secret-token@sample.upstash.io:6379"
        "?ssl_cert_reqs=none&socket_timeout=5"
    )

    query = parse_qs(urlparse(result).query)
    assert query["ssl_cert_reqs"] == ["required"]
    assert query["socket_timeout"] == ["5"]


@pytest.mark.parametrize(
    "url",
    [
        "redis://default:secret-token@sample.upstash.io:6379",
        "https://sample.upstash.io",
        "rediss://sample.upstash.io:6379",
        "rediss://default:secret-token@sample.upstash.io:not-a-port",
    ],
)
def test_rejects_non_tls_or_incomplete_urls(url):
    with pytest.raises(ValueError, match="UPSTASH_REDIS_URL"):
        normalize_upstash_redis_url(url)
