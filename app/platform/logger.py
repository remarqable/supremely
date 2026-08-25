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
