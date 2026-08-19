"""Regression tests for the settings boundary exposed by ``manage.py``."""

import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def test_manage_py_honours_explicit_settings_module():
    env = os.environ.copy()
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": "crm.test_settings",
            "SECRET_KEY": "test-only-key-with-at-least-32-bytes",
            "ADMIN_EMAIL": "admin@test.invalid",
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "shell",
            "-c",
            "from django.conf import settings; "
            "print(settings.DATABASES['default']['ENGINE'])",
        ],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("django.db.backends.sqlite3")
