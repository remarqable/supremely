"""Supremely application factory."""

from pathlib import Path

from flask import Flask, request, redirect, abort, g, render_template

from .config import Config
from .extensions import db, migrate, login_manager, init_sqlite_pragmas
from .platform.logger import init_logger, get_logger

APP_VERSION = '0.1.0'


def create_app(config_class=Config):
    app = Flask(__name__, template_folder='views', static_folder='static')
    app.config.from_object(config_class)

    Path(app.config['DATA_DIR']).mkdir(parents=True, exist_ok=True)

    init_logger(app.config['APP_ENV'])
    log = get_logger()

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    with app.app_context():
        init_sqlite_pragmas(app)

    if app.config.get('RUN_MIGRATIONS_ON_STARTUP'):
        from flask_migrate import upgrade
        with app.app_context():
            upgrade()

    from .middleware.csrf import init_csrf
    init_csrf(app)

    _init_setup_gate(app)

    # Resolves g.org and installs the tenant filter. Must come before
    # blueprints so g.org exists in every view.
    from .platform.tenant import init_tenant
    init_tenant(app)

    from .platform.i18n import init_i18n
    init_i18n(app)

    from .platform.theming import init_theming
    init_theming(app)

    from .controllers import main, auth, setup, admin, orgs, cli, manage, site
    for module in (main, auth, setup, admin, orgs, manage, site):
        app.register_blueprint(module.bp)
    for cli_bp in cli.CLI_BLUEPRINTS:
        app.register_blueprint(cli_bp)

    _init_context(app)
    _init_security_headers(app)
    _register_error_handlers(app)

    log.info('app_started', env=app.config['APP_ENV'], version=APP_VERSION)
    return app


def _init_setup_gate(app):
    """Uninitialized installations serve only the setup wizard; initialized
    ones never serve it again (reset with `flask setup reset`)."""
    from .platform.config_store import installation_ready

    @app.before_request
    def enforce_setup():
        if request.path.startswith('/static/') or request.path == '/health':
            return
        installed = installation_ready(app)
        if not installed and not request.path.startswith('/setup'):
            return redirect('/setup')
        if installed and request.path.startswith('/setup'):
            abort(404)


def _init_context(app):
    from .platform.authz import can, is_org_member
    from .models import InstallationSetting

    @app.context_processor
    def inject_globals():
        installation_name = 'Supremely'
        if app.config.get('SETUP_COMPLETE'):
            try:
                installation_name = InstallationSetting.get_value(
                    'installation.name', 'Supremely') or 'Supremely'
            except Exception:       # pre-migration states must still render
                pass
        from .models import NavigationItem
        return {
            'installation_name': installation_name,
            'can': can,
            'is_org_member': is_org_member,
            'app_version': APP_VERSION,
            'nav_items': NavigationItem.items_for,
        }

    @app.template_filter('localdate')
    def localdate(value, fmt='%Y-%m-%d'):
        if value is None:
            return ''
        return value.strftime(fmt)

    @app.template_filter('localdatetime')
    def localdatetime(value, fmt='%Y-%m-%d %H:%M'):
        if value is None:
            return ''
        return value.strftime(fmt)


def _init_security_headers(app):
    @app.after_request
    def add_security_headers(response):
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        if not app.debug and not app.testing:
            response.headers['Strict-Transport-Security'] = \
                'max-age=31536000; includeSubDomains'
        return response


def _register_error_handlers(app):
    from .platform.errors import AppError

    @app.errorhandler(AppError)
    def handle_app_error(error):
        if error.http_status >= 500:
            db.session.rollback()
        return render_template('errors/error.html', code=error.http_status,
                               message=error.message), error.http_status

    @app.errorhandler(403)
    def forbidden(error):
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template('errors/404.html'), 404

    @app.errorhandler(410)
    def gone(error):
        return render_template('errors/410.html'), 410

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        return render_template('errors/500.html'), 500
