import os
import warnings
from datetime import timedelta
from urllib.parse import urlparse

from corsheaders.defaults import default_headers
from dotenv import load_dotenv

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv()

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get(
    "SECRET_KEY", "django-insecure-dev-key-please-change-in-production"
)

IS_DEV_ENV = os.environ.get("ENV_TYPE", "dev") == "dev"

if not SECRET_KEY or SECRET_KEY.startswith("django-insecure"):
    if not IS_DEV_ENV:
        raise ValueError(
            "SECRET_KEY must be set to a secure value in non-dev environments"
        )

# This key also signs every JWT (see SIMPLE_JWT["SIGNING_KEY"] below). RFC 7518
# section 3.2 requires an HMAC key at least as long as the hash it produces, so
# HS256 needs 32 bytes. PyJWT only warns about a shorter one, on every call, and
# a warning nobody reads is not a control.
#
# Raising here means a deploy fails rather than quietly signing weak tokens.
# Lengthening the key invalidates every access and refresh token already issued,
# so everyone is signed out once when it changes.
JWT_MIN_SIGNING_KEY_BYTES = 32

if len(SECRET_KEY.encode()) < JWT_MIN_SIGNING_KEY_BYTES:
    message = (
        f"SECRET_KEY is {len(SECRET_KEY.encode())} bytes. It signs every JWT, "
        f"and HS256 needs at least {JWT_MIN_SIGNING_KEY_BYTES}. Generate one with "
        '`python -c "import secrets; print(secrets.token_urlsafe(48))"`.'
    )
    if not IS_DEV_ENV:
        raise ValueError(message)
    warnings.warn(message, RuntimeWarning, stacklevel=2)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get("DEBUG", "False").lower() == "true"

# Security: Restrict allowed hosts - set ALLOWED_HOSTS env var in production
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.admin",
    "django.contrib.contenttypes",
    "django.contrib.messages",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    # Required for refresh-token rotation: simplejwt only defines
    # RefreshToken.blacklist()/check_blacklist() when this app is installed, so
    # without it BLACKLIST_AFTER_ROTATION below is silently a no-op.
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_ses",
    "drf_spectacular",
    "common",
    "accounts",
    "cases",
    "contacts",
    "leads",
    "opportunity",
    "tasks",
    "invoices",
    "orders",
    "business_hours",
    "macros",
    # "teams",  # Merged into common app
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",  # CSRF protection
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "crum.CurrentRequestUserMiddleware",
    "common.middleware.get_company.GetProfileAndOrg",
    "common.middleware.rls_context.RequireOrgContext",  # RLS: Enforce org context + set PostgreSQL session variable
]

ROOT_URLCONF = "crm.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # No project-level template directory. Every template lives in its
        # owning app's templates/ dir and is found by APP_DIRS, so it ships
        # inside the wheel as package data. A BASE_DIR entry here would resolve
        # to site-packages/templates on a pip install, where nothing is
        # installed, and the magic-link login emails would fail to render.
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "common.context_processors.common.app_name",
                # "django_settings_export.settings_export",
            ],
        },
    },
]

WSGI_APPLICATION = "crm.wsgi.application"

# Database
# https://docs.djangoproject.com/en/1.10/ref/settings/#databases

# Connection pooling (psycopg 3 + psycopg_pool, wired by Django's postgresql
# backend). Off by default so an existing deployment does not silently change
# its connection behaviour on upgrade; set DB_POOL_ENABLED=true to turn it on.
#
# Two things make this more than a performance knob here:
#
# 1. Every ASGI request gets its own thread-sensitive executor and therefore
#    its own connection, with no ceiling. That is how two separate incidents
#    exhausted PostgreSQL. `max_size` is the ceiling that was missing.
# 2. RLS context lives in a SESSION-scoped GUC, so a reused connection carries
#    the previous tenant's org id unless something clears it. `reset` below is
#    that something, and pooling MUST NOT be enabled without it. See
#    common/rls/pool.py for the full argument.
#
# Sizing: the pool is per PROCESS, not per host. Real peak connections are
# roughly DB_POOL_MAX_SIZE x (uvicorn workers + celery prefork children), and
# docker-compose.yml runs `celery -A crm worker` with no --concurrency, so each
# worker forks one child per CPU. Multiply before comparing to PostgreSQL's
# max_connections (default 100).
DB_POOL_ENABLED = os.environ.get("DB_POOL_ENABLED", "False").lower() == "true"
DB_POOL_MIN_SIZE = int(os.environ.get("DB_POOL_MIN_SIZE", "2"))
DB_POOL_MAX_SIZE = int(os.environ.get("DB_POOL_MAX_SIZE", "10"))

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DBNAME", "crm_db"),
        "USER": os.environ.get("DBUSER", "postgres"),
        "PASSWORD": os.environ.get("DBPASSWORD", "postgres"),
        "HOST": os.environ.get("DBHOST", "localhost"),
        "PORT": os.environ.get("DBPORT", "5432"),
        # Django raises ImproperlyConfigured if this is non-zero alongside a
        # pool ("Pooling doesn't support persistent connections"). Keep it 0.
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": DB_POOL_ENABLED,
    }
}

if DB_POOL_ENABLED:
    # Imported lazily and locally: common/rls/pool.py deliberately imports no
    # Django, but keeping the import inside the branch means a non-pooled
    # deployment never touches it at all.
    from common.rls.pool import reset_rls_context

    DATABASES["default"]["OPTIONS"] = {
        "pool": {
            "min_size": DB_POOL_MIN_SIZE,
            "max_size": DB_POOL_MAX_SIZE,
            # Load-bearing for tenant isolation, not a tidy-up. Removing this
            # key turns pooling into a cross-tenant data leak.
            "reset": reset_rls_context,
        }
    }


# Password validation
# https://docs.djangoproject.com/en/1.10/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
# https://docs.djangoproject.com/en/1.10/topics/i18n/


# The fallback day, not the app's day. Each org carries its own `timezone` and
# `GetProfileAndOrg` activates it per request, so this is what gets used only
# where there is no org yet: the auth endpoints, and management commands.
#
# It was "Asia/Kolkata", which made one deployment's home timezone the day
# boundary for every tenant on it. Orgs that existed before `Org.timezone` keep
# that day, assigned to them by `common/migrations/0037_org_timezone.py`, so no
# live tenant's boundaries move.
TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)

AUTH_USER_MODEL = "common.User"

STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")
STATIC_URL = "/static/"
# Only when it exists. A source checkout keeps backend/static/ as a place to
# drop files; an installed copy resolves BASE_DIR to site-packages, where there
# is no such directory, and an unconditional entry raises staticfiles.W004 on
# every startup check. A permanent warning is where the next real one hides.
STATICFILES_DIRS = [
    path for path in [os.path.join(BASE_DIR, "static")] if os.path.isdir(path)
]

ENV_TYPE = os.environ.get("ENV_TYPE", "dev")
if ENV_TYPE == "dev":
    MEDIA_ROOT = os.path.join(BASE_DIR, "media")
    MEDIA_URL = "/media/"
elif ENV_TYPE == "prod":
    # A star import is the point here: server_settings.py is an operator-supplied
    # override file, so its names are not knowable from this side. The old
    # suppression said F401 (unused import), which is not the code a star import
    # raises, so it suppressed nothing.
    from .server_settings import *  # noqa: F403

DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@localhost")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@localhost")

# AWS SES settings (loaded when EMAIL_BACKEND is django_ses.SESBackend)
if "django_ses" in EMAIL_BACKEND:
    AWS_SES_REGION_NAME = os.environ.get("AWS_SES_REGION_NAME", "ap-south-1")
    AWS_SES_REGION_ENDPOINT = os.environ.get(
        "AWS_SES_REGION_ENDPOINT", f"email.{AWS_SES_REGION_NAME}.amazonaws.com"
    )
    # Uses AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from env if set;
    # otherwise falls back to IAM role credentials.


# celery Tasks
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get(
    "CELERY_RESULT_BACKEND", "redis://localhost:6379/0"
)


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "require_debug_false": {
            "()": "django.utils.log.RequireDebugFalse",
        },
        "require_debug_true": {
            "()": "django.utils.log.RequireDebugTrue",
        },
    },
    "formatters": {
        "django.server": {
            "()": "django.utils.log.ServerFormatter",
            "format": "[%(server_time)s] %(message)s",
        },
        "security": {
            "format": "%(asctime)s | %(levelname)s | %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "level": "INFO",
            "filters": ["require_debug_true"],
            "class": "logging.StreamHandler",
        },
        "console_debug_false": {
            "level": "ERROR",
            "filters": ["require_debug_false"],
            "class": "logging.StreamHandler",
        },
        "django.server": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "django.server",
        },
        "mail_admins": {
            "level": "ERROR",
            "filters": ["require_debug_false"],
            "class": "django.utils.log.AdminEmailHandler",
        },
        "logfile": {
            "class": "logging.FileHandler",
            "filename": "server.log",
        },
        "security_audit": {
            "class": "logging.FileHandler",
            "filename": "security_audit.log",
            "formatter": "security",
        },
    },
    "loggers": {
        "django": {
            "handlers": [
                "console",
                "console_debug_false",
                "logfile",
            ],
            "level": "INFO",
        },
        "django.server": {
            "handlers": ["django.server"],
            "level": "INFO",
            "propagate": False,
        },
        "security.audit": {
            "handlers": ["security_audit", "console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

APPLICATION_NAME = "bottlecrm"

SETTINGS_EXPORT = ["APPLICATION_NAME"]

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "rest_framework.views.exception_handler",
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "common.pat_auth.PATAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "common.external_auth.APIKeyAuthentication",
        # "rest_framework.authentication.SessionAuthentication",
        # "rest_framework.authentication.BasicAuthentication",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 10,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}


SPECTACULAR_SETTINGS = {
    "TITLE": "BottleCRM API",
    "DESCRIPTION": "Open source CRM application",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "PREPROCESSING_HOOKS": ["common.custom_openapi.preprocessing_filter_spec"],
    "ENUM_NAME_OVERRIDES": {
        # Role enums
        "ProfileRoleEnum": "common.utils.ROLES",
        "BoardMemberRoleEnum": "tasks.models.BoardMember.ROLE_CHOICES",
        # Priority enums
        "TaskPriorityEnum": "tasks.models.Task.PRIORITY_CHOICES",
        "CasePriorityEnum": "common.utils.PRIORITY_CHOICE",
        "BoardTaskPriorityEnum": "tasks.models.BoardTask.PRIORITY_CHOICES",
        # Status enums
        "TaskStatusEnum": "tasks.models.Task.STATUS_CHOICES",
        "CaseStatusEnum": "common.utils.STATUS_CHOICE",
        "SolutionStatusEnum": "cases.models.Solution.STATUS_CHOICES",
        "DocumentStatusEnum": "common.models.Document.DOCUMENT_STATUS_CHOICE",
        "InvoiceStatusEnum": "invoices.models.Invoice.INVOICE_STATUS",
        "ContactFormStatusEnum": "common.models.ContactFormSubmission.STATUS_CHOICES",
        "LeadStatusEnum": "common.utils.LEAD_STATUS",
    },
}

# JWT_SETTINGS = {
#     'bearerFormat': ('Bearer', 'jwt', 'Jwt')
# }

SWAGGER_SETTINGS = {
    "DEFAULT_INFO": "crm.urls.info",
    "SECURITY_DEFINITIONS": {
        "Bearer": {
            "type": "apiKey",
            "name": "Authorization",
            "in": "header",
            "description": "Enter 'Bearer <token>'",
        },
    },
}

CORS_ALLOW_HEADERS = default_headers + ("org",)
# Security: CORS configuration via environment variables
CORS_ORIGIN_ALLOW_ALL = os.environ.get("CORS_ALLOW_ALL", "False").lower() == "true"
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]
# Security: CSRF trusted origins via environment variable
_csrf_origins = os.environ.get("CSRF_TRUSTED_ORIGINS", "")
CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf_origins.split(",") if o.strip()]

# Security: HSTS with 1 year duration (recommended minimum)
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# `SecurityMiddleware` emits Strict-Transport-Security only when
# `request.is_secure()` is true. Behind a TLS-terminating proxy (nginx, an ALB)
# the request reaches Django over plain HTTP, so `is_secure()` is false and the
# three HSTS settings above were inert in exactly the deployment they were
# written for. This is what lets Django read the proxy's verdict instead.
#
# It is opt-in rather than on by default because the header is
# attacker-controllable on any path that does not pass through the proxy: a
# request sent straight to gunicorn carrying `X-Forwarded-Proto: https` makes
# `request.is_secure()` lie. Set TRUST_PROXY_SSL_HEADER=True only when the proxy
# is the sole ingress AND it overwrites the header rather than appending to a
# client-supplied one. The http-to-https redirect belongs on that same proxy,
# which is why SECURE_SSL_REDIRECT is deliberately left unset here.
if os.environ.get("TRUST_PROXY_SSL_HEADER", "False").lower() == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
# STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage'

SIMPLE_JWT = {
    # Security: Reduced token lifetimes
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    # Security: Enable token rotation to invalidate old refresh tokens
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": False,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "VERIFYING_KEY": None,
    "AUDIENCE": None,
    "ISSUER": None,
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}
# it is needed in custome middlewere to get the user from the token
JWT_ALGO = "HS256"


DOMAIN_NAME = os.environ.get("DOMAIN_NAME", "http://localhost:8000")

# The organization API key (`Token: <org.api_key>` header) is one non-expiring
# key per tenant that resolves to an arbitrary active ADMIN. Even now that it is
# read-only and barred from credential endpoints (see common/scopes.py), it
# still reads every record in the org and cannot be revoked per-integration.
# Personal access tokens replace it and are scoped, per-user and revocable.
#
# Left on by default so an upgrade breaks nothing. Set DJANGO_ORG_API_KEY_AUTH
# to "false" once every integration has moved to a personal access token.
ORG_API_KEY_AUTH_ENABLED = os.environ.get(
    "DJANGO_ORG_API_KEY_AUTH", "true"
).strip().lower() not in ("false", "0", "no", "off")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")

# Every link this system puts in an email is built from this one value, via
# `common.links.frontend_url`: the magic-link sign-in URL, the customer's
# invoice and estimate portal links, the CSAT survey, and the internal
# "assigned to you" notifications. Left at the dev default in production it
# mails customers a link to a port on their own machine, and mails a sign-in
# token to one, and neither failure shows up anywhere on the server.
#
# Checked here rather than at send time because there is no good answer at send
# time: the email has already been composed and the alternative is dropping it
# silently. A deploy that fails to start is the loud version of the same news.
if not IS_DEV_ENV:
    _frontend = urlparse(FRONTEND_URL)
    if _frontend.scheme not in ("http", "https") or not _frontend.netloc:
        raise ValueError(
            f"FRONTEND_URL is {FRONTEND_URL!r}, which is not an absolute URL. "
            "Every emailed link is built from it, and a value without a scheme "
            "and host produces a relative path, which is not a link at all "
            "inside an email. Set it to the web app's public base URL, for "
            "example https://app.example.com."
        )
    if _frontend.hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        raise ValueError(
            f"FRONTEND_URL is {FRONTEND_URL!r}, which points at this machine. "
            "It is emailed to customers (invoice and estimate portal links, "
            "the CSAT survey) and carries the magic-link sign-in URL. Set it to "
            "the web app's public base URL, for example https://app.example.com."
        )

# Google OAuth Configuration
#
# There is no GOOGLE_REDIRECT_URI here. The redirect URI used in the token
# exchange is the one the frontend sends in the request body on each call
# (`common/views/auth_views.py`), so a setting of that name was read into
# Django and never referenced again. Same for SWAGGER_ROOT_URL, which named
# nothing: the schema is served from whatever host serves the app.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
