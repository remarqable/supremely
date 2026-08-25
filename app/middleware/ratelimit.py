"""In-memory, per-worker rate limiting. See blueprint/patterns/core/security.md."""

import time
from functools import wraps

from flask import request, current_app

from app.platform.errors import RateLimitError

# Storage: {key: (count, window_start)}
_rate_limits = {}


def rate_limit(limit: int = 100, window: int = 60):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_app.config.get('RATELIMIT_ENABLED', True):
                return f(*args, **kwargs)

            key = f'{f.__name__}:{_get_client_ip()}'
            if not _check_limit(key, limit, window):
                raise RateLimitError()
            return f(*args, **kwargs)
        return decorated
    return decorator


def _get_client_ip() -> str:
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or '127.0.0.1'


def _check_limit(key: str, limit: int, window: int) -> bool:
    now = time.time()
    data = _rate_limits.get(key)

    if data is None:
        _rate_limits[key] = (1, now)
        return True

    count, window_start = data
    if now - window_start > window:
        _rate_limits[key] = (1, now)
        return True
    if count >= limit:
        return False

    _rate_limits[key] = (count + 1, window_start)
    return True
