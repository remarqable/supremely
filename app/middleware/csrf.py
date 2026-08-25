"""CSRF protection middleware. See blueprint/patterns/core/security.md."""

import secrets

from flask import session, request, abort


def generate_csrf_token() -> str:
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_urlsafe(32)
    return session['_csrf_token']


def validate_csrf_token() -> bool:
    token = session.get('_csrf_token')
    if not token:
        return False

    submitted = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    if not submitted:
        return False

    return secrets.compare_digest(token, submitted)


def init_csrf(app):
    @app.context_processor
    def csrf_context():
        return {'csrf_token': generate_csrf_token()}

    @app.before_request
    def check_csrf():
        if not app.config.get('CSRF_ENABLED', True):
            return
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return
        if request.path == '/health':
            return
        if not validate_csrf_token():
            abort(403, 'CSRF token invalid')
