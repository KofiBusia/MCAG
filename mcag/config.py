"""Application configuration.

All secrets and deployment settings come from environment variables.
Nothing sensitive is hard-coded here.
"""
import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        # Development fallback only. Production must set DATABASE_URL (PostgreSQL).
        url = "sqlite:///" + os.path.join(os.path.abspath(os.path.dirname(__file__)), "..", "mcag_dev.sqlite3")
    # Render/Heroku style URLs use postgres:// which SQLAlchemy no longer accepts.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


class Config:
    APP_NAME = os.environ.get("APP_NAME", "MCAG Loan Management System")
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5000")

    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    FLASK_ENV = os.environ.get("FLASK_ENV", "development")
    DEBUG = _bool("FLASK_DEBUG")

    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = (
        {"pool_pre_ping": True, "pool_recycle": 300}
        if _database_url().startswith("postgresql")
        else {}
    )

    # Sessions / cookies
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE")
    SESSION_COOKIE_HTTPONLY = _bool("SESSION_COOKIE_HTTPONLY", "true")
    SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
    PERMANENT_SESSION_LIFETIME = timedelta(
        minutes=int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30"))
    )
    REMEMBER_COOKIE_HTTPONLY = True
    WTF_CSRF_TIME_LIMIT = None

    # Authentication policy
    MAX_LOGIN_ATTEMPTS = int(os.environ.get("MAX_LOGIN_ATTEMPTS", "5"))
    ACCOUNT_LOCK_MINUTES = int(os.environ.get("ACCOUNT_LOCK_MINUTES", "30"))
    PASSWORD_RESET_TOKEN_EXPIRY_MINUTES = int(
        os.environ.get("PASSWORD_RESET_TOKEN_EXPIRY_MINUTES", "30")
    )
    PASSWORD_MIN_LENGTH = int(os.environ.get("PASSWORD_MIN_LENGTH", "10"))

    # Uploads
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "uploads")
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_SIZE_MB", "10")) * 1024 * 1024
    ALLOWED_UPLOAD_EXTENSIONS = {
        "pdf", "png", "jpg", "jpeg", "gif", "doc", "docx", "xls", "xlsx", "csv"
    }

    # Locale
    DEFAULT_TIMEZONE = os.environ.get("DEFAULT_TIMEZONE", "Africa/Accra")
    DEFAULT_CURRENCY = os.environ.get("DEFAULT_CURRENCY", "GHS")
    CURRENCY_SYMBOL = "GH¢"

    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

    # Platform bootstrap (used only by the create-platform-admin CLI command)
    PLATFORM_ADMIN_EMAIL = os.environ.get("PLATFORM_ADMIN_EMAIL", "")
    PLATFORM_ADMIN_PASSWORD = os.environ.get("PLATFORM_ADMIN_PASSWORD", "")

    # Mail / SMS (optional integrations)
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT") or 587)
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_USE_TLS = _bool("MAIL_USE_TLS", "true")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "")
    SMS_PROVIDER = os.environ.get("SMS_PROVIDER", "")
    SMS_API_KEY = os.environ.get("SMS_API_KEY", "")
    SMS_SENDER_ID = os.environ.get("SMS_SENDER_ID", "")
    SENTRY_DSN = os.environ.get("SENTRY_DSN", "")


class ProductionConfig(Config):
    """Secure production settings (selected when FLASK_ENV=production)."""
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-key-not-for-production"
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL", "sqlite://")
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    MAX_LOGIN_ATTEMPTS = 3


def get_config():
    env = os.environ.get("FLASK_ENV", "development")
    if env == "production":
        return ProductionConfig
    if env == "testing":
        return TestingConfig
    return Config
