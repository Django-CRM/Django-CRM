"""``FRONTEND_URL`` is the base of every link this system emails.

``common.links.frontend_url`` builds all of them from it: the customer's invoice
and estimate portal links, the CSAT survey, the internal "assigned to you"
notifications, and, directly in ``common/tasks.py``, the magic-link sign-in URL.
It defaults to ``http://localhost:5181`` so a checkout works with no ``.env``,
which means a deployment that never sets it mails customers a link to a port on
their own machine and mails a sign-in token to one. Nothing on the server
reports it: the email sends, the link is simply dead on arrival.

``crm/settings.py`` now refuses a loopback or non-absolute value outside dev. Dev
is left alone, because there the default is the correct answer.

These tests import the settings module in a subprocess, because the check runs at
import time and reloading it inside the test process would rewrite settings for
everything that runs afterwards. Same reason, same shape, as
``test_signing_key_strength.py``.
"""

import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]

# Long enough to clear the separate SECRET_KEY floor, so a failure here is
# always about FRONTEND_URL. Not a real key: the subprocess imports the module
# and exits without signing anything.
GOOD_SECRET_KEY = "k" * 48


def _import_settings(frontend_url, env_type):
    """Import ``crm.settings`` in a clean subprocess and report how it went.

    ``load_dotenv()`` does not override variables already in the environment, so
    what is passed here wins over the developer's own ``.env``.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("SECRET_KEY", "ENV_TYPE", "FRONTEND_URL")
    }
    env["SECRET_KEY"] = GOOD_SECRET_KEY
    env["ENV_TYPE"] = env_type
    env["FRONTEND_URL"] = frontend_url
    return subprocess.run(
        [sys.executable, "-c", "import crm.settings"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


class TestALoopbackFrontendUrlIsRefusedOutsideDev:
    def test_the_dev_default_fails_the_import(self):
        """The exact value an operator gets by not setting the variable."""
        result = _import_settings("http://localhost:5181", "production")

        assert result.returncode != 0
        assert "FRONTEND_URL" in result.stderr

    def test_the_error_says_what_to_set_it_to(self):
        """A refusal that does not say what to do next is a support ticket."""
        result = _import_settings("http://localhost:5181", "production")

        assert "https://app.example.com" in result.stderr

    def test_the_error_says_the_link_reaches_customers(self):
        """The operator needs to know this is outward-facing, not internal."""
        result = _import_settings("http://localhost:5181", "production")

        assert "customers" in result.stderr

    def test_the_loopback_address_fails_too(self):
        result = _import_settings("http://127.0.0.1:5181", "production")

        assert result.returncode != 0

    def test_the_wildcard_bind_address_fails(self):
        """`0.0.0.0` is what a Docker compose file tends to carry."""
        result = _import_settings("http://0.0.0.0:5181", "production")

        assert result.returncode != 0

    def test_a_public_https_url_is_accepted(self):
        """The other direction: the check has to be able to pass."""
        result = _import_settings("https://app.example.com", "production")

        assert result.returncode == 0, result.stderr

    def test_a_trailing_slash_is_accepted(self):
        """`frontend_url` strips it, so it must not be refused here."""
        result = _import_settings("https://app.example.com/", "production")

        assert result.returncode == 0, result.stderr

    def test_a_public_http_url_is_accepted(self):
        """Plain HTTP is not this check's business.

        An internal deployment behind its own TLS terminator is a real setup,
        and refusing it here would be a second, unasked-for policy riding along
        with this one.
        """
        result = _import_settings("http://app.example.com", "production")

        assert result.returncode == 0, result.stderr

    def test_a_host_that_merely_contains_localhost_is_accepted(self):
        """`hostname` is compared whole, not searched for a substring."""
        result = _import_settings("https://localhost.example.com", "production")

        assert result.returncode == 0, result.stderr


class TestANonAbsoluteFrontendUrlIsRefusedOutsideDev:
    def test_a_bare_host_fails_the_import(self):
        """No scheme means `frontend_url` emits `app.example.com/portal/...`.

        In an email body that is not a link, which is exactly how the CSAT
        survey shipped broken before `common/links.py` existed.
        """
        result = _import_settings("app.example.com", "production")

        assert result.returncode != 0
        assert "absolute" in result.stderr

    def test_an_empty_value_fails_the_import(self):
        """`backend/.env` set the old `DOMAIN_NAME` to exactly this."""
        result = _import_settings("", "production")

        assert result.returncode != 0

    def test_a_non_http_scheme_fails_the_import(self):
        result = _import_settings("ftp://app.example.com", "production")

        assert result.returncode != 0


class TestDevIsLeftAlone:
    def test_the_dev_default_imports_cleanly_in_dev(self):
        """In dev the loopback URL is the correct answer, not a warning."""
        result = _import_settings("http://localhost:5181", "dev")

        assert result.returncode == 0, result.stderr
        assert "FRONTEND_URL" not in result.stderr

    def test_an_empty_value_imports_in_dev(self):
        """Blocking dev would stop every existing checkout from running."""
        result = _import_settings("", "dev")

        assert result.returncode == 0, result.stderr
