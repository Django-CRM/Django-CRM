"""Validation helpers for managed Redis connection URLs."""

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def normalize_upstash_redis_url(value: str) -> str:
    """Validate an Upstash TCP URL and require verified TLS.

    Upstash's REST endpoint is HTTP-based and cannot be used as a Celery
    broker. Celery requires the Redis TCP protocol, represented by rediss://.
    """
    value = (value or "").strip()
    if not value:
        return ""

    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        port = None

    if (
        parsed.scheme != "rediss"
        or not parsed.hostname
        or not port
        or not parsed.password
    ):
        raise ValueError(
            "UPSTASH_REDIS_URL must be a valid rediss:// TCP URL with a host, "
            "port, and password. Use the TCP connection string from Upstash; "
            "do not use the REST URL or token."
        )

    # Ignore a weaker caller-supplied value: certificate verification is
    # mandatory for the managed production connection.
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "ssl_cert_reqs"
    ]
    query.append(("ssl_cert_reqs", "required"))
    return urlunparse(parsed._replace(query=urlencode(query)))
