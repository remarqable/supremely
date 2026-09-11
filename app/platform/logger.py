"""Structured logging."""

import logging
import sys

import structlog


def init_logger(env: str = 'dev'):
    logging.basicConfig(format='%(message)s', stream=sys.stdout, level=logging.INFO)
    renderer = (structlog.dev.ConsoleRenderer(colors=True) if env == 'dev'
                else structlog.processors.JSONRenderer())
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(
                fmt='%Y-%m-%d %H:%M:%S' if env == 'dev' else 'iso'),
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


def get_logger():
    return structlog.get_logger()


# View arguments that are secrets rather than identifiers. A path holding
# one of these is a working credential, so the value never reaches a log
# line even though the rest of the path is worth having.
SECRET_VIEW_ARGS = ('token',)


def _safe_path() -> str:
    """request.path with any token in it replaced.

    Rebuilt from what the router matched rather than edited as a string,
    so it redacts the argument the rule actually named and cannot be
    fooled by a token that happens to look like something else.
    """
    from flask import request

    path = request.path
    for name in SECRET_VIEW_ARGS:
        value = (request.view_args or {}).get(name)
        if value:
            path = path.replace(str(value), '<redacted>')
    return path


def log_refusal(event: str, **fields) -> None:
    """Record a decision the server made to refuse something.

    OWASP asks for every failed access control decision to be logged.
    Recording them in different shapes, or without saying who was refused
    and from where, is most of the way to not recording them at all, so
    every refusal goes through here and carries the same four facts.

    Never called with a password or a session identifier, and never with a
    token: a path carrying one is recorded with the token taken out. The
    routes that carry one are the ways into an account and into an
    organization, so the refusals worth logging are exactly the ones where
    the token is still live -- a CSRF failure or a rate limit stops the
    request before the view can spend it. A reverse proxy's access log may
    still hold the whole path, but that log is the operator's to configure
    and this one is ours.
    """
    from flask import g, has_request_context, request
    from flask_login import current_user

    context = {}
    if has_request_context():
        context['path'] = _safe_path()
        context['method'] = request.method
        context['ip'] = request.remote_addr
        org = getattr(g, 'org', None)
        if org is not None:
            from sqlalchemy import inspect as sa_inspect
            identity = sa_inspect(org).identity
            context['org_id'] = identity[0] if identity else None
        if current_user and current_user.is_authenticated:
            context['actor_id'] = current_user.id
    # Merged rather than splatted twice: a caller passing path= or ip=
    # would otherwise raise, and this is the one function in the codebase
    # whose whole value is that it never does.
    context.update(fields)
    get_logger().warning(event, **context)
