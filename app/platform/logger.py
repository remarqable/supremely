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


def log_refusal(event: str, **fields) -> None:
    """Record a decision the server made to refuse something.

    OWASP asks for every failed access control decision to be logged.
    Recording them in different shapes, or without saying who was refused
    and from where, is most of the way to not recording them at all, so
    every refusal goes through here and carries the same four facts.

    Never called with a password or a session identifier. The path is
    recorded as sent, and a few routes carry a single use token in theirs,
    which the access log records for every request in any case.
    """
    from flask import g, has_request_context, request
    from flask_login import current_user

    context = {}
    if has_request_context():
        context['path'] = request.path
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
