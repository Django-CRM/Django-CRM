"""``crm/server_settings.py`` keeps prod uploads on S3, private, and unmixed.

The test suite runs under ``ENV_TYPE=dev``, so this module is never imported by
anything else here. That is precisely why it needs its own test: every defect it
has carried reached production undetected, because nothing in CI loads the file
and the symptoms all appear somewhere other than where the mistake is.

Four have already been live on bottlecrm.io:

* ``DEFAULT_FILE_STORAGE`` was removed in Django 5.1, so on Django 6 it read
  back fine from ``settings`` while ``default_storage`` quietly resolved to
  ``FileSystemStorage`` and wrote to a root-owned ``/media/``. Uploads had never
  once worked in production.
* ``AWS_S3_CUSTOM_DOMAIN`` made ``url()`` return unsigned ``https://<bucket>...``
  links, which resolve for anyone holding them, across tenants, forever.
* boto3's default addressing signs the global S3 host, and S3 answers it with a
  307 to the regional host. Following the redirect changes the Host the
  signature covered, so every download came back 403 SignatureDoesNotMatch.
* django-storages defaults ``AWS_S3_FILE_OVERWRITE`` to ``True``, the opposite
  of ``FileSystemStorage``. None of the ``upload_to`` values in
  ``common/models.py`` carry an org or a unique component, so two orgs uploading
  the same filename shared a key and the second replaced the first's bytes while
  the first's ``Attachment`` row kept pointing at it. RLS cannot see that: the
  row is correctly scoped and untouched, only the object behind the key changed.

Each assertion below is one of those, so a regression fails here rather than in
production.
"""

from __future__ import annotations

import importlib
import os
from unittest import mock

import pytest

BASE_ENV = {
    "AWS_BUCKET_NAME": "test-bucket",
    "AWS_ACCESS_KEY_ID": "test-key-id",
    "AWS_SECRET_ACCESS_KEY": "test-secret-value",
    "AWS_SES_REGION_NAME": "ap-south-1",
    "AWS_SES_REGION_ENDPOINT": "email.ap-south-1.amazonaws.com",
    # Present but empty, which is what disables the Sentry SDK. The init call is
    # patched out below as well, so importing this module reports nothing.
    "SENTRY_DSN": "",
}


def load(**overrides):
    """Import ``crm.server_settings`` fresh under a controlled environment."""
    env = {**BASE_ENV, **overrides}
    with mock.patch.dict(os.environ, env, clear=False):
        with mock.patch("sentry_sdk.init"):
            module = importlib.import_module("crm.server_settings")
            return importlib.reload(module)


def test_default_storage_is_s3_via_storages_dict():
    """STORAGES is what Django reads; DEFAULT_FILE_STORAGE is inert on 5.1+."""
    settings = load()
    assert settings.STORAGES["default"]["BACKEND"] == (
        "storages.backends.s3boto3.S3Boto3Storage"
    )
    # Django does not merge STORAGES with its defaults, so omitting this key
    # makes every static file lookup raise InvalidStorageError.
    assert "staticfiles" in settings.STORAGES


def test_uploads_do_not_overwrite_each_other():
    """Two orgs uploading the same filename must not share one object."""
    settings = load()
    assert settings.AWS_S3_FILE_OVERWRITE is False


def test_urls_are_signed_not_public():
    """No custom domain means url() presigns, so the bucket stays private."""
    settings = load()
    assert not hasattr(settings, "AWS_S3_CUSTOM_DOMAIN")


def test_addressing_style_is_virtual():
    """Anything else signs the global host and 403s after the 307 redirect."""
    settings = load()
    assert settings.AWS_S3_ADDRESSING_STYLE == "virtual"


def test_region_is_set_and_defaults_to_the_ses_region():
    """SigV4 signs the region in, so a wrong guess produces rejected URLs."""
    assert load().AWS_S3_REGION_NAME == "ap-south-1"
    assert load(AWS_S3_REGION_NAME="eu-west-1").AWS_S3_REGION_NAME == "eu-west-1"


def test_uploads_are_prefixed_so_they_are_reachable():
    """Without the prefix, uploads land at the bucket root and 404 when served."""
    settings = load()
    assert settings.AWS_LOCATION == "media"


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_bucket_name_fails_at_startup(blank):
    """Empty is the case that hurts: os.environ[...] alone does not catch it.

    With no guard the module still evaluates end to end, uploads address a
    bucket named "", and the deployment looks configured until a user attaches
    a file. That is how this shipped.
    """
    with pytest.raises(ValueError, match="AWS_BUCKET_NAME is empty"):
        load(AWS_BUCKET_NAME=blank)


def test_missing_bucket_name_fails_at_startup():
    with mock.patch.dict(os.environ, BASE_ENV, clear=False):
        os.environ.pop("AWS_BUCKET_NAME", None)
        with mock.patch("sentry_sdk.init"):
            with pytest.raises(KeyError):
                importlib.reload(importlib.import_module("crm.server_settings"))
