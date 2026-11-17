import os

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

from common.public_tokens import scrub_public_tokens
from common.sentry_scrub import before_send

DEBUG = False

# Tigris is S3-compatible. Keep the existing django-storages backend and
# object-key layout; only the endpoint and credentials change.
AWS_BUCKET_NAME = (
    os.environ.get("AWS_BUCKET_NAME")
    or os.environ.get("AWS_STORAGE_BUCKET_NAME", "")
).strip()
if not AWS_BUCKET_NAME:
    raise ValueError(
        "AWS_BUCKET_NAME is required in production. Set it to the Tigris bucket name "
        "(for this deployment: godan-files-prod)."
    )
AWS_STORAGE_BUCKET_NAME = AWS_BUCKET_NAME

AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
missing_storage_credentials = [
    name
    for name, value in (
        ("AWS_ACCESS_KEY_ID", AWS_ACCESS_KEY_ID),
        ("AWS_SECRET_ACCESS_KEY", AWS_SECRET_ACCESS_KEY),
    )
    if not value
]
if missing_storage_credentials:
    raise ValueError(
        "Tigris storage credentials are missing: " + ", ".join(missing_storage_credentials)
    )

# Tigris documents the global S3 endpoint and the special region "auto".
# AWS_ENDPOINT_URL_S3 / AWS_REGION are accepted as aliases for its SDK examples.
AWS_S3_ENDPOINT_URL = (
    os.environ.get("AWS_S3_ENDPOINT_URL")
    or os.environ.get("AWS_ENDPOINT_URL_S3")
    or "https://t3.storage.dev"
).strip()
AWS_S3_REGION_NAME = (
    os.environ.get("AWS_S3_REGION_NAME") or os.environ.get("AWS_REGION") or "auto"
).strip()
AWS_S3_ADDRESSING_STYLE = os.environ.get("AWS_S3_ADDRESSING_STYLE", "virtual").strip()
AWS_S3_SIGNATURE_VERSION = "s3v4"
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = True
AWS_QUERYSTRING_EXPIRE = int(os.environ.get("AWS_QUERYSTRING_EXPIRE", "3600"))
AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "max-age=86400"}
AWS_S3_FILE_OVERWRITE = False
AWS_S3_SECURE_URLS = True
AWS_IS_GZIPPED = True
AWS_ENABLED = True
DEFAULT_S3_PATH = "media"
AWS_LOCATION = DEFAULT_S3_PATH

# Do not set AWS_S3_CUSTOM_DOMAIN: that would bypass private, signed URLs.
# Keep both STORAGES entries because Django does not merge this dictionary.
STORAGES = {
    "default": {"BACKEND": "storages.backends.s3boto3.S3Boto3Storage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
MEDIA_ROOT = f"/{DEFAULT_S3_PATH}/"
# Informational legacy value only; django-storages builds signed object URLs.
S3_DOMAIN = f"{AWS_BUCKET_NAME}.t3.storage.dev"
MEDIA_URL = f"//{S3_DOMAIN}/{DEFAULT_S3_PATH}/"

# Production must never silently fall back to Django's console email backend.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
if not RESEND_API_KEY:
    raise ValueError(
        "RESEND_API_KEY is required in production. Add the Resend API key to "
        "the Render service environment before deploying."
    )
MAILERS = {
    "default": {"BACKEND": "common.email_backends.resend.ResendEmailBackend"}
}

# Keep production sender configurable; never invent a sender domain that may
# not be verified in the Resend account.
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "").strip()
if not DEFAULT_FROM_EMAIL or "localhost" in DEFAULT_FROM_EMAIL.lower():
    raise ValueError(
        "DEFAULT_FROM_EMAIL must be a sender address on a domain verified in Resend."
    )

# Sentry is optional. Its absence must not prevent an otherwise valid service
# from starting; when configured, retain the existing sensitive-data scrubbers.
SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        send_default_pii=True,
        event_scrubber=EventScrubber(
            denylist=[*DEFAULT_DENYLIST, "x-bottlecrm-relay-secret"],
            send_default_pii=True,
        ),
        before_send=before_send,
        before_send_transaction=scrub_public_tokens,
    )

RAVEN_CONFIG = {"dsn": SENTRY_DSN}

# Cookie domain defaults to the current host. Set SESSION_COOKIE_DOMAIN only
# when the app is served behind a domain that should share cookies.
SESSION_COOKIE_DOMAIN = os.environ.get("SESSION_COOKIE_DOMAIN") or None
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True