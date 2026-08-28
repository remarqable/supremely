"""In-memory, per-worker rate limiting. See blueprint/patterns/core/security.md.

Counts only the methods that change something. A GET and a POST usually share
one view here (`/auth/login` renders the form and accepts it), so counting GET
meant reloading the login page ten times locked everyone out of signing in.

The store is a plain dict in the worker process, so a deployment running N
workers allows N times the configured limit installation-wide, and a restart
forgets everything. That is a deliberate trade for having no Redis dependency;
the numbers below should be read as per worker, not per installation.
"""

import time
from functools import wraps

from flask import current_app, request

from app.platform.errors import RateLimitError
from app.platform.logger import get_logger

log = get_logger()

# Storage: {key: (count, window_start, window)}. The window is kept per entry
# so the sweep can retire an entry without knowing which route wrote it.
_rate_limits = {}

# Nothing evicts on a timer, so the dict is swept once it is large enough to be
# worth the walk. Unauthenticated endpoints are keyed by client IP, which an
# attacker can vary freely, so an unbounded dict is a slow memory leak.
_SWEEP_AT = 10_000

# The sweep walks the whole dict, so it must not run per request: an
# attacker who holds the dict above the threshold would otherwise make
# every request, including everyone else's, pay that walk.
_SWEEP_EVERY = 30.0
_last_sweep = 0.0

# A sweep only retires entries whose window has passed, so it cannot bound
# a flood of live ones. This cap does, at the cost of forgetting the
# oldest counters first.
_MAX_ENTRIES = 100_000

COUNTED_METHODS = ('POST', 'PUT', 'PATCH', 'DELETE')

_warned_about_proxy = False


def rate_limit(limit: int = 100, window: int = 60,
               methods: tuple[str, ...] = COUNTED_METHODS):
    """Limit `limit` requests per `window` seconds per client IP.

    Only `methods` are counted. Pass methods=('GET',) for a route that is
    genuinely expensive to serve rather than expensive to get wrong.
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_app.config.get('RATELIMIT_ENABLED', True):
                return f(*args, **kwargs)
            # Flask answers HEAD with the GET view, so a route that opts
            # into GET has to count HEAD or the limit is four bytes away.
            method = 'GET' if request.method == 'HEAD' else request.method
            if method not in methods:
                return f(*args, **kwargs)

            key = f'{f.__name__}:{_get_client_ip()}'
            if not _check_limit(key, limit, window):
                raise RateLimitError()
            return f(*args, **kwargs)
        return decorated
    return decorator


def _get_client_ip() -> str:
    # request.remote_addr is already the real client IP when TRUSTED_PROXIES
    # is set (ProxyFix rewrites it). Trusting X-Forwarded-For here directly
    # would let any client spoof the header and get an unlimited keyspace.
    if not current_app.config.get('TRUSTED_PROXIES'):
        _warn_if_proxied()
    return request.remote_addr or '127.0.0.1'


def _warn_if_proxied() -> None:
    """A proxy in front with TRUSTED_PROXIES unset makes every client share
    one bucket, so a single visitor can lock out the whole installation."""
    global _warned_about_proxy
    if _warned_about_proxy or 'X-Forwarded-For' not in request.headers:
        return
    _warned_about_proxy = True
    log.warning('rate_limit_proxy_unconfigured',
                detail='X-Forwarded-For seen but TRUSTED_PROXIES is 0, so every '
                       'client counts against one bucket. Set TRUSTED_PROXIES '
                       'to the number of proxies in front of the app.')


def _maybe_sweep(now: float) -> None:
    global _last_sweep
    if len(_rate_limits) > _SWEEP_AT and now - _last_sweep > _SWEEP_EVERY:
        _sweep(now)
        _last_sweep = now


# An identity is whatever was typed into a form, and it becomes a dict key
# held for the length of the window. Werkzeug accepts half a megabyte of
# form data and the entry cap counts entries rather than their size, so an
# unbounded key is a way to fill a worker's memory from outside. Nothing
# longer than an address column can be a real one.
_MAX_IDENTITY = 255


def _failure_key(identity: str) -> str:
    return f'failure:{identity[:_MAX_IDENTITY]}'


def too_many_failures(identity: str, limit: int, window: int) -> bool:
    """Whether this identity has already spent its budget of failures.

    Counted per identity rather than per address, because the per address
    limit bounds one attacker and does nothing about the same account being
    guessed at from many.
    """
    data = _rate_limits.get(_failure_key(identity))
    if data is None:
        return False
    count, start, length = data
    return count >= limit and time.time() - start <= length


def record_failure(identity: str, window: int) -> None:
    now = time.time()
    _maybe_sweep(now)
    key = _failure_key(identity)
    data = _rate_limits.get(key)
    if data is None or now - data[1] > data[2]:
        _rate_limits[key] = (1, now, window)
        return
    _rate_limits[key] = (data[0] + 1, data[1], data[2])


def clear_failures(identity: str) -> None:
    """Called on success, so an ordinary run of typos costs nothing once
    the person remembers."""
    _rate_limits.pop(_failure_key(identity), None)


def _check_limit(key: str, limit: int, window: int) -> bool:
    now = time.time()
    _maybe_sweep(now)
    data = _rate_limits.get(key)

    if data is None:
        _rate_limits[key] = (1, now, window)
        return True

    count, window_start, _ = data
    if now - window_start > window:
        _rate_limits[key] = (1, now, window)
        return True
    if count >= limit:
        return False

    _rate_limits[key] = (count + 1, window_start, window)
    return True


def _sweep(now: float) -> None:
    for key in [key for key, (_, start, window) in _rate_limits.items()
                if now - start > window]:
        _rate_limits.pop(key, None)
    # Expiry alone cannot bound a flood of still-live entries. Dicts keep
    # insertion order, so the oldest counters go first.
    excess = len(_rate_limits) - _MAX_ENTRIES
    for key in list(_rate_limits)[:excess] if excess > 0 else []:
        _rate_limits.pop(key, None)
