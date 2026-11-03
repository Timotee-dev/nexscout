"""
NexScout settings.

Everything secret or machine-specific lives in .env (see .env.example).
SQLite by default; set DATABASE_URL-style vars to move to PostgreSQL later.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me-in-env")
DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "scout",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "scout.context_processors.nav",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database ---------------------------------------------------------------
# SQLite locally. To switch to PostgreSQL later: set DB_ENGINE=postgres plus
# DB_NAME / DB_USER / DB_PASSWORD / DB_HOST / DB_PORT and `pip install psycopg[binary]`.
if os.getenv("DB_ENGINE", "sqlite").lower() in {"postgres", "postgresql"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME", "nexscout"),
            "USER": os.getenv("DB_USER", "postgres"),
            "PASSWORD": os.getenv("DB_PASSWORD", ""),
            "HOST": os.getenv("DB_HOST", "localhost"),
            "PORT": os.getenv("DB_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            "OPTIONS": {"timeout": 20},
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/admin/login/"

# Own cookie names so NexScout and NexReach can run side by side on 127.0.0.1
# without logging each other out or breaking each other's CSRF tokens.
SESSION_COOKIE_NAME = "nexscout_sessionid"
CSRF_COOKIE_NAME = "nexscout_csrftoken"

# --- NexScout ---------------------------------------------------------------
# Claude is called through the Claude Code CLI in headless mode (your Pro login).
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
CLAUDE_CLI = os.getenv("CLAUDE_CLI", "claude")
CLAUDE_EXTRA_ARGS = os.getenv("CLAUDE_EXTRA_ARGS", "")
CLAUDE_TIMEOUT_SECONDS = int(os.getenv("CLAUDE_TIMEOUT_SECONDS", "180"))

# Identifies NexScout to job boards. Keep a real contact address here.
SCOUT_CONTACT_EMAIL = os.getenv("SCOUT_CONTACT_EMAIL", "arifalotimothy@gmail.com")
SCOUT_USER_AGENT = os.getenv(
    "SCOUT_USER_AGENT",
    f"NexScout/1.0 (personal job search tool; contact: {SCOUT_CONTACT_EMAIL})",
)
HTTP_TIMEOUT_SECONDS = int(os.getenv("HTTP_TIMEOUT_SECONDS", "25"))

# Require login for the dashboard (recommended if you ever expose it beyond localhost).
SCOUT_REQUIRE_LOGIN = env_bool("SCOUT_REQUIRE_LOGIN", False)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "loggers": {"scout": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")}},
}

# --- Sending applications (Gmail) -------------------------------------------
# Use a Gmail App Password (Google Account -> Security -> 2-Step Verification -> App passwords),
# never your normal password. Leave blank and NexScout simply won't send anything.
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "").replace(" ", "")
EMAIL_TIMEOUT = 30
SENDER_NAME = os.getenv("SENDER_NAME", "Timothy Arifalo")
DEFAULT_FROM_EMAIL = f"{SENDER_NAME} <{EMAIL_HOST_USER}>" if EMAIL_HOST_USER else "nexscout@localhost"
# Your CV, attached to every application email.
CV_PATH = os.getenv("CV_PATH") or str(BASE_DIR / "cv.pdf")
