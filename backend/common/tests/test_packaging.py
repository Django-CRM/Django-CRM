"""The built wheel ships every app the settings install, and every template.

``pip install django-crm`` gets whatever ``[tool.hatch.build.targets.wheel]``
lists in ``pyproject.toml`` and nothing else. That list is hand-maintained, and
a source checkout never exercises it: the editable install used in development
is a single ``.pth`` file adding all of ``backend/`` to ``sys.path``, so every
app imports whether or not it was ever packaged.

Both halves of that gap were live. ``business_hours`` and ``macros`` were in
``INSTALLED_APPS`` and absent from the wheel, so an installed copy died at
startup with ``ModuleNotFoundError``. The magic-link login emails lived in a
project-level ``templates/`` directory reached through ``TEMPLATES[0]["DIRS"]``
via ``BASE_DIR``, which resolves to ``site-packages`` once installed, so they
would not have rendered either.

Derived from the app registry rather than a second hand-written list here,
because a list of "apps to check" fails exactly the way the list it checks
does. See ``test_org_index_coverage.py`` for the same reasoning.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings
from django.template import TemplateDoesNotExist
from django.template.loader import get_template

# The project package. Holds settings/urls/celery/wsgi, so it is required at
# runtime, but it is not an app and never appears in INSTALLED_APPS.
PROJECT_PACKAGE = "crm"

# Templates that used to sit in the project-level templates/ directory. Each is
# rendered by name from application code, so the name is the contract: moving a
# file between app template directories is fine, dropping it out of a packaged
# app is not.
MOVED_TEMPLATES = [
    "assigned_to/account_assigned.html",
    "assigned_to/cases_assigned.html",
    "assigned_to/contact_assigned.html",
    "assigned_to/leads_assigned.html",
    "assigned_to/opportunity_assigned.html",
    "healthz.html",
    "magic_link_code_email.html",
    "magic_link_email.html",
    "opportunity/goal_milestone.html",
    "opportunity/stale_deals_alert.html",
    "root_email_template_new.html",
]


def _backend_root() -> Path:
    return Path(settings.BASE_DIR)


def _first_party_apps() -> set[str]:
    """Installed apps whose source lives in this repo, not in site-packages."""
    root = _backend_root()
    return {
        config.label
        for config in apps.get_app_configs()
        if Path(config.path).resolve().parent == root.resolve()
    }


def _build_config() -> dict:
    with open(_backend_root() / "pyproject.toml", "rb") as handle:
        return tomllib.load(handle)["tool"]["hatch"]["build"]["targets"]


def test_wheel_packages_match_installed_apps():
    """Every first-party app is packaged, and nothing packaged has been deleted."""
    packaged = set(_build_config()["wheel"]["packages"])
    expected = _first_party_apps() | {PROJECT_PACKAGE}

    assert packaged == expected, (
        "pyproject.toml [tool.hatch.build.targets.wheel].packages has drifted "
        f"from INSTALLED_APPS. Missing from the wheel: "
        f"{sorted(expected - packaged)}. Packaged but not installed: "
        f"{sorted(packaged - expected)}."
    )


def test_sdist_includes_every_wheel_package():
    """The sdist carries at least what the wheel does, as a trailing-slash path."""
    targets = _build_config()
    included = set(targets["sdist"]["include"])
    missing = [
        f"{name}/"
        for name in targets["wheel"]["packages"]
        if f"{name}/" not in included
    ]

    assert not missing, (
        f"pyproject.toml [tool.hatch.build.targets.sdist].include is missing {missing}. "
        "An sdist that omits an installed app builds a wheel that cannot start."
    )


@pytest.mark.parametrize("name", MOVED_TEMPLATES)
def test_template_resolves_through_an_app_directory(name):
    """Resolvable with TEMPLATES[0]["DIRS"] empty, so it ships as package data."""
    assert settings.TEMPLATES[0]["DIRS"] == [], (
        "A project-level template directory resolves to site-packages/templates "
        "on an installed copy, where nothing is written. Keep templates in the "
        "owning app's templates/ directory so APP_DIRS finds them."
    )
    try:
        get_template(name)
    except TemplateDoesNotExist:  # pragma: no cover - the assertion is the report
        pytest.fail(
            f"{name} is not reachable through any installed app's templates/ "
            "directory, so it will be absent from the wheel."
        )
