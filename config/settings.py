"""Shared application settings for milestone 01 foundation.

Local SQLite with WAL, immediate write transactions and a bounded busy
timeout. Secure cookie/session defaults are enforced outside DEBUG mode.
No secrets are logged here.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


DEBUG = _env_bool("DJANGO_DEBUG", True)

_default_secret = "dev-insecure-secret-key-change-me"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", _default_secret)
if not DEBUG and SECRET_KEY == _default_secret:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false."
    )

_allowed = os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
ALLOWED_HOSTS = [h.strip() for h in _allowed.split(",") if h.strip()]
if not DEBUG and not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must not be empty in production.")

_csrf_origins = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "")
CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf_origins.split(",") if o.strip()]

# Trusted proxy: only Caddy (Compose network) forwards proto/host.
USE_X_FORWARDED_HOST = _env_bool("DJANGO_USE_X_FORWARDED_HOST", False)

# Application release label baked into the image (e.g. 0.4.0). Empty in dev.
APP_VERSION = os.environ.get("APP_VERSION", "").strip()

# Bounded uploads: spreadsheet/bundle views enforce tighter per-file caps;
# these Django-level caps are the outer fail-closed bound.
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get("DATA_UPLOAD_MAX_MEMORY_SIZE", str(5 * 1024 * 1024)))
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get("FILE_UPLOAD_MAX_MEMORY_SIZE", str(5 * 1024 * 1024)))
DATA_UPLOAD_MAX_NUMBER_FIELDS = int(os.environ.get("DATA_UPLOAD_MAX_NUMBER_FIELDS", "2000"))

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.accounts",
    "apps.roster",
    "apps.inventory",
    "apps.issuing",
    "apps.audit",
    "apps.operations",
    "apps.transfers",
    "apps.reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.middleware.IdleTimeoutMiddleware",
    "apps.accounts.middleware.AccountStatusMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.operations.context_processors.site_notice",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

_db_name = os.environ.get("DJANGO_DB_PATH", str(BASE_DIR / ".local" / "db.sqlite3"))

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": _db_name,
        "OPTIONS": {
            "timeout": 5.0,
            "transaction_mode": "IMMEDIATE",
            "init_command": (
                "PRAGMA journal_mode=WAL; "
                "PRAGMA busy_timeout=5000; "
                "PRAGMA synchronous=NORMAL; "
                "PRAGMA foreign_keys=ON"
            ),
        },
    }
}

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# 30-minute idle expiry. SESSION_SAVE_EVERY_REQUEST stays False so that plain
# session-cookie age does not slide; IdleTimeoutMiddleware owns idle tracking.
SESSION_COOKIE_AGE = 1800
SESSION_IDLE_TIMEOUT = 1800
SESSION_SAVE_EVERY_REQUEST = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
# Poll endpoints must not extend the idle timer. Paths listed here are skipped
# by IdleTimeoutMiddleware when refreshing last-activity.
SESSION_POLL_PATHS = ["/poll/", "/api/poll/", "/api/stock/", "/api/dashboard/"]

CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Lax"

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Container health/readiness probes reach Gunicorn over plain HTTP inside the
# Compose network; exempt those two paths from the HTTPS redirect so probes
# report the real application state instead of a 301.
SECURE_REDIRECT_EXEMPT = [r"^healthz/?$", r"^readyz/?$"]
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = _env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"

# Bounded login throttling (attempts/window/lockout in seconds).
LOGIN_THROTTLE_MAX_ATTEMPTS = int(os.environ.get("LOGIN_THROTTLE_MAX_ATTEMPTS", "5"))
LOGIN_THROTTLE_WINDOW = int(os.environ.get("LOGIN_THROTTLE_WINDOW", "600"))
LOGIN_THROTTLE_LOCKOUT = int(os.environ.get("LOGIN_THROTTLE_LOCKOUT", "600"))

CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
}

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
