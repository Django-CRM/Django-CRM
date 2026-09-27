"""``DOMAIN_NAME`` is the base of the API URLs a person copies out of the app.

``common.links.api_url`` builds the task calendar feed URL and the web form
embed snippet from it. Both leave the building: a calendar app subscribes to
the one, a customer's site embeds the other. Its default is
``http://localhost:8000``, so outside dev ``crm/settings.py`` refuses a
loopback or non-absolute value at import, the same way it refuses one for
``FRONTEND_URL``. Subprocess imports for the reason given in
``test_frontend_url_guard.py``.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from common.links import api_url

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _import_settings(domain_name, env_type):
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("SECRET_KEY", "ENV_TYPE", "FRONTEND_URL", "DOMAIN_NAME")
    }
    env["SECRET_KEY"] = "k" * 48
    env["ENV_TYPE"] = env_type
    env["FRONTEND_URL"] = "https://app.example.com"
    env["DOMAIN_NAME"] = domain_name
    return subprocess.run(
        [sys.executable, "-c", "import crm.settings"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "value",
    [
        "http://localhost:8000",  # the default an operator gets by not setting it
        "http://127.0.0.1:8000",
        "http://0.0.0.0:8000",
        "",
        "api.example.com",  # no scheme
        "/api",
    ],
)
def test_a_loopback_or_relative_value_fails_the_import_outside_dev(value):
    result = _import_settings(value, "production")
    assert result.returncode != 0
    assert "DOMAIN_NAME" in result.stderr
    assert "https://api.example.com" in result.stderr


@pytest.mark.parametrize("value", ["https://api.example.com", "http://api.example.com"])
def test_a_public_value_imports_outside_dev(value):
    result = _import_settings(value, "production")
    assert result.returncode == 0, result.stderr


def test_dev_keeps_the_loopback_default():
    result = _import_settings("http://localhost:8000", "dev")
    assert result.returncode == 0, result.stderr


def test_api_url_joins_the_origin_and_path(settings):
    settings.DOMAIN_NAME = "https://api.example.com/"
    assert api_url("/api/x/") == "https://api.example.com/api/x/"
    assert api_url("api/x/") == "https://api.example.com/api/x/"
