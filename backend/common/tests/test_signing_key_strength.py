"""``SECRET_KEY`` signs every JWT, so its length is a security control.

``SIMPLE_JWT["SIGNING_KEY"]`` is ``SECRET_KEY``, and the algorithm is HS256.
RFC 7518 section 3.2 requires an HMAC key at least as long as the hash function
output, which is 32 bytes for SHA-256. PyJWT does not refuse a shorter key; it
emits a warning, once per call, into a log nobody reads. The local ``.env`` here
carried a 20-byte key for the life of the project and every token this server
issued was signed with it.

``crm/settings.py`` now raises on a short key outside dev and warns inside it.
These tests import the settings module in a subprocess, because the check runs at
import time and reloading it inside the test process would rewrite settings for
everything that runs afterwards.
"""

import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]

# 32 bytes exactly, the floor. Not a real key: it never signs anything, the
# subprocess only imports the module and exits.
KEY_AT_FLOOR = "k" * 32
KEY_BELOW_FLOOR = "k" * 31


def _import_settings(secret_key, env_type):
    """Import ``crm.settings`` in a clean subprocess and report how it went.

    ``load_dotenv()`` does not override variables already in the environment, so
    what is passed here wins over the developer's own ``.env``.

    ``FRONTEND_URL`` and ``DOMAIN_NAME`` are pinned to public values because
    each carries its own production guard (see ``test_frontend_url_guard.py``
    and ``test_domain_name_guard.py``), and both default to a loopback URL.
    Without this, every ``env_type="production"`` case below would fail the
    import for a reason that has nothing to do with the signing key.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("SECRET_KEY", "ENV_TYPE", "FRONTEND_URL", "DOMAIN_NAME")
    }
    env["SECRET_KEY"] = secret_key
    env["ENV_TYPE"] = env_type
    env["FRONTEND_URL"] = "https://app.example.com"
    env["DOMAIN_NAME"] = "https://api.example.com"
    return subprocess.run(
        [sys.executable, "-W", "always", "-c", "import crm.settings"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


class TestAShortSigningKeyIsRefusedOutsideDev:
    def test_a_31_byte_key_fails_the_import(self):
        result = _import_settings(KEY_BELOW_FLOOR, "production")

        assert result.returncode != 0
        assert "31 bytes" in result.stderr
        assert "at least 32" in result.stderr

    def test_the_error_names_the_command_that_produces_a_good_key(self):
        """A refusal that does not say what to do next is a support ticket."""
        result = _import_settings(KEY_BELOW_FLOOR, "production")

        assert "secrets.token_urlsafe" in result.stderr

    def test_a_32_byte_key_is_accepted(self):
        """The other direction: the check has to be able to pass."""
        result = _import_settings(KEY_AT_FLOOR, "production")

        assert result.returncode == 0, result.stderr

    def test_a_long_key_is_accepted(self):
        result = _import_settings("k" * 64, "production")

        assert result.returncode == 0, result.stderr


class TestDevIsWarnedRatherThanBlocked:
    def test_a_short_key_warns_in_dev_and_still_imports(self):
        """Blocking dev would mean every existing checkout stops running."""
        result = _import_settings(KEY_BELOW_FLOOR, "dev")

        assert result.returncode == 0, result.stderr
        assert "31 bytes" in result.stderr

    def test_no_warning_in_dev_once_the_key_is_long_enough(self):
        result = _import_settings(KEY_AT_FLOOR, "dev")

        assert result.returncode == 0, result.stderr
        assert "SECRET_KEY" not in result.stderr


class TestTheKeyIsMeasuredInBytesNotCharacters:
    def test_a_31_character_key_of_multibyte_characters_passes(self):
        """31 characters, 62 bytes. The RFC counts bytes, and so does HMAC.

        Measuring `len(SECRET_KEY)` instead would reject this key, and would
        accept a 32-character key that was somehow shorter in bytes. Python
        strings cannot be shorter in bytes than in characters, so only the
        false rejection is reachable, which is the one this pins.
        """
        result = _import_settings("é" * 31, "production")

        assert result.returncode == 0, result.stderr
