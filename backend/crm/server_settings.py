import os

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

DEBUG = False

AWS_STORAGE_BUCKET_NAME = AWS_BUCKET_NAME = os.environ["AWS_BUCKET_NAME"]

# `os.environ[...]` catches the variable being absent but not its being present
# and empty, and empty is the case that hurts. With `AWS_BUCKET_NAME=`, every
# line below still evaluates: S3_DOMAIN becomes ".s3.amazonaws.com", MEDIA_URL
# becomes "//.s3.amazonaws.com/media/", and uploads go to a bucket named "".
# Nothing raises, so the deployment looks configured and fails only when a user
# tries to attach a file. Found exactly that way on bottlecrm.io, where the AWS
# credentials turned out to be an SES-only IAM user and no bucket had ever
# existed. Fail at startup instead, in the same shape as the FRONTEND_URL guard
# in settings.py.
if not AWS_BUCKET_NAME.strip():
    raise ValueError(
        "AWS_BUCKET_NAME is empty. ENV_TYPE=prod stores uploads in S3, so this "
        "must name a real bucket, and the AWS credentials must be allowed to "
        "write to it. Set it, or run with ENV_TYPE=dev to store uploads on "
        "local disk under MEDIA_ROOT."
    )

AWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"]
AWS_SECRET_ACCESS_KEY = os.environ["AWS_SECRET_ACCESS_KEY"]
AWS_SES_REGION_NAME = os.environ["AWS_SES_REGION_NAME"]
AWS_SES_REGION_ENDPOINT = os.environ["AWS_SES_REGION_ENDPOINT"]

# No AWS_S3_CUSTOM_DOMAIN, deliberately.
#
# Setting it makes django-storages return plain `https://<domain>/<key>` URLs
# with no signature, which only resolve if the bucket is public-read. For a
# multi-tenant CRM that means every uploaded attachment is fetchable by anyone
# holding the URL, across tenants, with no authorization check anywhere. Leaving
# it unset makes `storage.url()` return a presigned URL that expires, so the
# bucket can keep Block Public Access fully on.
#
# S3_DOMAIN is kept because other settings and templates refer to it, but it is
# no longer wired into how URLs are generated.
S3_DOMAIN = str(AWS_BUCKET_NAME) + ".s3.amazonaws.com"

# Required, and easy to miss. Presigned URLs are SigV4, which signs the region
# into the signature, so boto3 guessing wrong produces URLs the bucket rejects.
# Defaults to the SES region because a single-region deployment is the common
# case; override when the bucket lives somewhere else.
AWS_S3_REGION_NAME = os.environ.get("AWS_S3_REGION_NAME", AWS_SES_REGION_NAME)

# Required for any bucket outside us-east-1, and the failure is not obvious.
#
# boto3's default addressing builds presigned URLs against the global host,
# `<bucket>.s3.amazonaws.com`, even when the client itself is pointed at the
# regional endpoint. S3 answers that host with a 307 redirect to
# `<bucket>.s3.<region>.amazonaws.com` and preserves the query string. The
# signature covers the Host header, so following the redirect presents a
# signature computed over the old host to the new one, and the request comes
# back 403 SignatureDoesNotMatch. Every download breaks; uploads keep working,
# because those go straight through the regional client and never redirect.
#
# Verified against the real bucket: default style returns 307 then 403, this
# setting returns 200. It is not a workaround for a wrong region, and the error
# does not name the region, which is what makes it slow to diagnose.
AWS_S3_ADDRESSING_STYLE = "virtual"

AWS_S3_OBJECT_PARAMETERS = {
    "CacheControl": "max-age=86400",
}

DEFAULT_S3_PATH = "media"

# STORAGES, not DEFAULT_FILE_STORAGE.
#
# `DEFAULT_FILE_STORAGE` was deprecated in Django 4.2 and REMOVED in 5.1. On a
# modern Django it is an ordinary unused name: the setting is still readable via
# `settings.DEFAULT_FILE_STORAGE`, so it looks configured, while `default_storage`
# quietly resolves from `STORAGES` instead and lands on the built-in
# `FileSystemStorage`. Every upload then tries to write to MEDIA_ROOT below,
# which is `/media/` at the filesystem root, owned by root, and fails with
# `PermissionError: [Errno 13] Permission denied: '/media/attachments'`.
#
# That is not a hypothetical: it was live on bottlecrm.io for days before anyone
# noticed, because the failure is a 500 on upload rather than anything visible at
# startup, and `settings.DEFAULT_FILE_STORAGE` reads back exactly as intended.
#
# Both keys are required. Django does not merge this setting with its defaults,
# so omitting "staticfiles" makes every static file lookup raise
# InvalidStorageError.
STORAGES = {
    "default": {"BACKEND": "storages.backends.s3boto3.S3Boto3Storage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# The key prefix inside the bucket. Without it, django-storages writes to the
# bucket root while MEDIA_URL below points at /media/, so uploads would succeed
# and then 404 when served, which is a worse failure than the one above because
# nothing errors.
AWS_LOCATION = DEFAULT_S3_PATH

# Cross-tenant overwrite. django-storages defaults this to True, which is the
# opposite of what FileSystemStorage does, so moving to S3 changes the semantics
# of every upload path in the project without touching any of them.
#
# None of the three upload_to values in common/models.py carry an org or a
# unique component: `org_logos/`, `CommentFiles`, and `attachments/%Y/%m/` all
# derive the key from the user's filename alone. With overwriting on, one org
# uploading `contract.pdf` lands on the same key as another org's `contract.pdf`
# in the same month, replaces its bytes, and leaves the first org's Attachment
# row pointing at the second org's file. RLS cannot see this: the database row
# is untouched and correctly scoped, and only the object behind the key changed.
#
# Setting it False restores Django's own behaviour, which is to append a random
# suffix until the name is free. That keeps dev on local disk and prod on S3
# behaving alike, and it is also what stops a re-upload from accumulating a
# billable noncurrent version on a versioned bucket.
AWS_S3_FILE_OVERWRITE = False

# Unused while STORAGES routes to S3, which addresses paths by key rather than
# by filesystem location. Kept so that a deployment falling back to
# FileSystemStorage has a defined destination rather than writing relative to
# the working directory.
MEDIA_ROOT = f"/{DEFAULT_S3_PATH}/"
MEDIA_URL = f"//{S3_DOMAIN}/{DEFAULT_S3_PATH}/"
# STATIC_URL = "https://%s/" % (S3_DOMAIN)
# ADMIN_MEDIA_PREFIX = STATIC_URL + "admin/"

AWS_IS_GZIPPED = True
AWS_ENABLED = True
AWS_S3_SECURE_URLS = True

EMAIL_BACKEND = "django_ses.SESBackend"

SESSION_COOKIE_DOMAIN = ".bottlecrm.io"
SESSION_COOKIE_SECURE = True  # Only send session cookie over HTTPS
CSRF_COOKIE_SECURE = True  # Only send CSRF cookie over HTTPS

sentry_sdk.init(
    dsn=os.environ["SENTRY_DSN"],
    integrations=[DjangoIntegration()],
    traces_sample_rate=1.0,
    # If you wish to associate users to errors (assuming you are using
    # django.contrib.auth) you may enable sending PII data.
    send_default_pii=True,
)

RAVEN_CONFIG = {
    "dsn": os.environ["SENTRY_DSN"],
}
