"""Per-credential request allowance for API tokens.

Only credentials that are not a signed-in session are counted (personal tokens
and the org key). Counting uses the default cache, so a limit holds across
workers only when every worker shares one cache (``CACHE_URL``).
"""

import logging
import time
from collections.abc import Callable

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60

_limits: list[Callable] = []


def register_limit(fn: Callable) -> None:
    """Add a function ``(org) -> int | None`` giving a per-minute ceiling for an org.

    The lowest ceiling of the setting and every registered function applies.
    """
    if fn not in _limits:
        _limits.append(fn)


def _now() -> float:
    return time.time()


def token_limit_per_minute(org) -> int | None:
    """The lowest ceiling that applies, else None.

    A registered function that raises is skipped, so a fault in an extension
    cannot take token requests down.
    """
    values = [getattr(settings, "API_TOKEN_RATE_PER_MINUTE", None)]
    for fn in list(_limits):
        try:
            values.append(fn(org))
        except Exception as exc:
            logger.warning(
                "A registered rate limit failed (%s); ignoring it", type(exc).__name__
            )
    values = [v for v in values if v is not None]
    return min(values) if values else None


def hit(credential_key: str, limit: int) -> int | None:
    """Count one request in the current minute.

    Returns the seconds until the minute ends when the credential is over
    ``limit``, else None.
    """
    now = int(_now())
    window = now // WINDOW_SECONDS
    key = f"apirate:{credential_key}:{window}"
    cache.add(key, 0, timeout=WINDOW_SECONDS * 2)
    try:
        count = cache.incr(key)
    except ValueError:
        # The key expired between add and incr.
        cache.set(key, 1, timeout=WINDOW_SECONDS * 2)
        count = 1
    if count > limit:
        return (window + 1) * WINDOW_SECONDS - now
    return None
