"""
Test settings for Django CRM backend.

Uses SQLite in-memory database instead of PostgreSQL for fast, isolated tests.
RLS (Row-Level Security) is PostgreSQL-only and is skipped on SQLite.
"""

from crm.settings import *  # noqa: F401, F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Disable Celery broker/result backend in tests (no Redis needed)
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

# The default PBKDF2 hasher costs ~190ms per hash. Fixtures create users on
# nearly every test, so that dominates the run. MD5 is fine here: it is never
# used outside crm.test_settings, and no test asserts on the hash algorithm.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Uploads land in a throwaway directory rather than in `backend/media/`.
# Without this every attachment test leaves its file behind: the tree had grown
# to 51 MB of `update_YXw1f7b.txt` and friends by the time anyone looked, and
# the size-limit test below writes a 25 MB file on every run.
#
# `mkdtemp` rather than `TemporaryDirectory` because nothing here would call
# `cleanup()`; the OS reclaims it, and a test that needs to read back a file it
# just wrote still can.
import tempfile  # noqa: E402

MEDIA_ROOT = tempfile.mkdtemp(prefix="bottlecrm-test-media-")

# This API's public origin (`common.links.api_url`), pinned so the calendar feed
# URL and web form embed snippets do not depend on the developer's own `.env`.
# Deliberately not `testserver`: a test that sees this host knows the URL came
# from the setting and not from the incoming request.
DOMAIN_NAME = "https://api.example.com"
