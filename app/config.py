"""Application configuration.

Config layering (see blueprint/patterns/core/deployment.md § Runtime-written
configuration): defaults -> data/config.env (written by the setup wizard) ->
real environment variables. Environment always wins so operators can override.
"""

import os
from pathlib import Path


def _load_runtime_env() -> None:
    """Layer data/config.env under real env vars (env always wins).

    An env var present but EMPTY (e.g. compose's `${SECRET_KEY:-}`) is treated
    as unset, so a volume-written value can still fill it in.
    """
    from dotenv import dotenv_values

    data_dir = os.environ.get('DATA_DIR', 'data')
    cfg = Path(data_dir) / 'config.env'
    if cfg.exists():
        for key, value in dotenv_values(cfg).items():
            if not os.environ.get(key) and value is not None:
                os.environ[key] = value


_load_runtime_env()

_DATA_DIR = Path(os.environ.get('DATA_DIR', 'data')).resolve()
_DEFAULT_DB = f'sqlite:///{_DATA_DIR / "app.db"}'

_DEV_SECRET = 'dev-secret-change-in-production'


def _resolve_secret_key() -> str:
    """Resolve SECRET_KEY: a non-empty env var wins; otherwise reuse (or
    generate once and persist) a key on the data volume so sessions survive
    restarts. In dev the well-known fallback is fine; in production a missing
    key is a hard error rather than a silent downgrade.

    See blueprint/patterns/core/deployment.md § Runtime-written configuration
    and core/security.md (SECRET_KEY must be set in production).
    """
    from secrets import token_hex

    env_value = os.environ.get('SECRET_KEY')
    if env_value:
        return env_value

    # Persist a generated key so every worker and every restart share it.
    import tempfile
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    key_file = _DATA_DIR / 'secret_key'
    try:
        if key_file.exists():
            existing = key_file.read_text(encoding='utf-8').strip()
            if existing:
                return existing
        generated = token_hex(32)
        fd, tmp = tempfile.mkstemp(dir=_DATA_DIR, prefix='.secret_key.')
        with os.fdopen(fd, 'w') as fh:
            fh.write(generated)
        os.chmod(tmp, 0o600)
        os.replace(tmp, key_file)
        return generated
    except OSError:
        # Read-only volume or similar: fall back to the dev key rather than
        # crashing. Non-production only -- production is guarded below.
        return _DEV_SECRET


class Config:
    APP_ENV = os.environ.get('APP_ENV', 'dev')
    SECRET_KEY = _resolve_secret_key()
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

    # Flask-Login's remember-me cookie is a long-lived credential; it must
    # carry the same protections as the session cookie (it does NOT by
    # default). See blueprint/patterns/core/auth.md § Session Management.
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_DOMAIN = SESSION_COOKIE_DOMAIN
    REMEMBER_COOKIE_DURATION = PERMANENT_SESSION_LIFETIME

    # Number of trusted reverse proxies in front of the app. 0 = trust no
    # X-Forwarded-* header (safe default for direct/localhost). Set to the
    # real hop count (usually 1) when behind Caddy/nginx so rate limiting and
    # scheme detection use the true client IP. See core/security.md.
    TRUSTED_PROXIES = int(os.environ.get('TRUSTED_PROXIES', '0'))

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
    # Empty (CI's sqlite matrix leg passes TEST_DATABASE_URL='') falls back
    # to in-memory SQLite rather than an invalid empty URI.
    SQLALCHEMY_DATABASE_URI = (os.environ.get('TEST_DATABASE_URL')
                               or 'sqlite:///:memory:')
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
