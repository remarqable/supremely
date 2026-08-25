"""Application configuration.

Config layering (see blueprint/patterns/core/deployment.md § Runtime-written
configuration): defaults -> data/config.env (written by the setup wizard) ->
real environment variables. Environment always wins so operators can override.
"""

import os
from pathlib import Path


def _load_runtime_env() -> None:
    """Layer data/config.env under real env vars (env always wins)."""
    from dotenv import dotenv_values

    data_dir = os.environ.get('DATA_DIR', 'data')
    cfg = Path(data_dir) / 'config.env'
    if cfg.exists():
        for key, value in dotenv_values(cfg).items():
            if key not in os.environ and value is not None:
                os.environ[key] = value


_load_runtime_env()

_DATA_DIR = Path(os.environ.get('DATA_DIR', 'data')).resolve()
_DEFAULT_DB = f'sqlite:///{_DATA_DIR / "app.db"}'


class Config:
    APP_ENV = os.environ.get('APP_ENV', 'dev')
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
    DEBUG = APP_ENV == 'dev'
    BASE_DOMAIN = os.environ.get('BASE_DOMAIN', 'localhost')
    DATA_DIR = str(_DATA_DIR)

    DATABASE_URL = os.environ.get('DATABASE_URL', _DEFAULT_DB)
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    IS_SQLITE = DATABASE_URL.startswith('sqlite')
    IS_POSTGRES = DATABASE_URL.startswith('postgresql')

    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 5, 'max_overflow': 10, 'pool_timeout': 30,
        'pool_recycle': 300, 'pool_pre_ping': True,
    } if IS_POSTGRES else {'connect_args': {'timeout': 30}}

    SESSION_COOKIE_SECURE = APP_ENV != 'dev'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 86400 * 14
    # Share the session across org subdomains. Host-only on localhost, where
    # a leading-dot domain cookie is unreliable across browsers.
    _base = BASE_DOMAIN.split(':')[0]
    SESSION_COOKIE_DOMAIN = f'.{_base}' if _base not in ('localhost', '127.0.0.1') else None

    CSRF_ENABLED = True
    RATELIMIT_ENABLED = True
    RUN_MIGRATIONS_ON_STARTUP = False    # migrations belong in deploy, not boot

    # Organizations serve public content: tenant resolution sets g.org for
    # anonymous visitors and content visibility is a model-layer concern.
    # See blueprint/patterns/tenancy.md § Public content.
    PUBLIC_TENANTS = True

    SETUP_COMPLETE = os.environ.get('SETUP_COMPLETE', 'false').lower() == 'true'

    MAX_CONTENT_LENGTH = 20 * 1024 * 1024
    JOBS_POLL_INTERVAL = 2


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('TEST_DATABASE_URL', 'sqlite:///:memory:')
    IS_SQLITE = SQLALCHEMY_DATABASE_URI.startswith('sqlite')
    IS_POSTGRES = SQLALCHEMY_DATABASE_URI.startswith('postgresql')
    SQLALCHEMY_ENGINE_OPTIONS = {}
    SECRET_KEY = 'test-secret'
    CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    RUN_MIGRATIONS_ON_STARTUP = False
    SETUP_COMPLETE = True
    SERVER_NAME = 'example.test'
    BASE_DOMAIN = 'example.test'
    SESSION_COOKIE_DOMAIN = '.example.test'
    APP_ENV = 'test'
    DEBUG = False
    SESSION_COOKIE_SECURE = False
