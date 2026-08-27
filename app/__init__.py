"""Supremely application factory."""

import contextlib
from pathlib import Path

from flask import Flask, abort, g, redirect, render_template, request

from .config import Config
from .extensions import db, init_sqlite_pragmas, login_manager, migrate
from .platform.logger import get_logger, init_logger

APP_VERSION = '0.1.0'


def create_app(config_class=Config):
    app = Flask(__name__, template_folder='views', static_folder='static')
    app.config.from_object(config_class)
    app.jinja_env.add_extension('jinja2.ext.do')   # {% do list.append(...) %}

    Path(app.config['DATA_DIR']).mkdir(parents=True, exist_ok=True)

    # A real production install must never run on the well-known dev key.
    from .config import _DEV_SECRET
    if (app.config['APP_ENV'] == 'production' and not app.testing
            and app.config['SECRET_KEY'] == _DEV_SECRET):
        raise RuntimeError(
            'SECRET_KEY is unset in production. Set the SECRET_KEY env var, '
            'or give the app a writable DATA_DIR so it can persist one.')

    init_logger(app.config['APP_ENV'])
    log = get_logger()

    # Only honor X-Forwarded-* from the configured number of trusted proxies.
    # Without this, clients spoof X-Forwarded-For to evade rate limiting.
    hops = app.config.get('TRUSTED_PROXIES', 0)
    if hops:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops,
                                x_host=hops)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    with app.app_context():
        init_sqlite_pragmas(app)

    if app.config.get('RUN_MIGRATIONS_ON_STARTUP'):
        from flask_migrate import upgrade
        with app.app_context():
            upgrade()

    @app.before_request
    def _reset_request_state():
        # Registered first on purpose. When an app context outlives a request
        # (tests, scripts driving test_client), Flask reuses it and
        # Flask-Login's per-request user cache on `g` would leak across
        # requests. Clearing it forces re-authentication from the session.
        from flask import g as _g
        _g.pop('_login_user', None)

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

    from .platform.content_types import register_core_types
    register_core_types()
    from .platform.content_library import register_library_types
    register_library_types()

    from .controllers import (
        admin,
        auth,
        cli,
        discussions,
        main,
        manage,
        members,
        newsletter,
        notifications,
        orgs,
        setup,
        site,
    )

    # Imported only for the side effect of registering their job handlers;
    # the bindings are deliberately unused.
    from .platform import newsletter as _newsletter  # noqa: F401
    from .platform import notify as _notify  # noqa: F401
    for module in (main, auth, setup, admin, orgs, manage, members,
                   discussions, notifications, newsletter, site):
        app.register_blueprint(module.bp)

    # Boot-time plugin registration: per-request tenant gating, no restarts.
    from .platform.plugins import check_stranded_pins, load_plugins
    load_plugins(app)
    check_stranded_pins(app)
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
        # /tls-check must answer before the wizard has run, or the very first
        # certificate never issues and the install is unreachable over HTTPS.
        if (request.path.startswith('/static/')
                or request.path in ('/health', '/tls-check')):
            return None
        installed = installation_ready(app)
        if not installed and not request.path.startswith('/setup'):
            return redirect('/setup')
        if installed and request.path.startswith('/setup'):
            abort(404)
        return None


def _init_context(app):
    from .models import InstallationSetting
    from .platform.authz import can, can_view, is_org_member

    @app.context_processor
    def inject_globals():
        installation_name = 'Supremely'
        if app.config.get('SETUP_COMPLETE'):
            # pre-migration states must still render
            with contextlib.suppress(Exception):
                installation_name = InstallationSetting.get_value(
                    'installation.name', 'Supremely') or 'Supremely'
        from flask_login import current_user

        from .models import NavigationItem
        from .platform.content_types import active_types

        def unread_notifications():
            if getattr(g, 'membership', None) is None:
                return 0
            from .models.notification import Notification
            return Notification.unread_count(current_user.id)

        def start_here_page():
            """The community sidebar's Start Here target: the published About
            page, if any. Making this configurable is future Manage work."""
            if getattr(g, 'org', None) is None:
                return None
            from .models import Content
            return Content.published_page('about')

        def _member_view():
            """The shell serves visitors too; rail cards must not leak
            gated content or member data to them."""
            from flask_login import current_user
            return is_org_member() or (current_user.is_authenticated
                                       and current_user.is_platform_admin)

        def latest_announcement():
            """Newest announcement the current viewer may read, for the
            right-rail card."""
            if getattr(g, 'org', None) is None:
                return None
            from .models import Content
            if (not _member_view()
                    and Content.section_visibility('announcement') != 'public'):
                return None
            query = Content.published_query('announcement')
            if not _member_view():
                query = query.filter_by(visibility='public')
            return query.first()

        def upcoming_event():
            """Next readable published event for the right-rail event card."""
            if getattr(g, 'org', None) is None:
                return None
            from .models import Content
            return Content.upcoming_event(public_only=not _member_view())

        def rail_members():
            """(newest members, total active) for the right-rail members
            card. Names and avatars are member data: visitors get only the
            count."""
            if getattr(g, 'org', None) is None:
                return [], 0
            from .models import Membership
            recent = (Membership.recent_users(g.org.id)
                      if _member_view() else [])
            return recent, Membership.active_count(g.org.id)

        def section_readable(type_slug):
            """Whether the current viewer may see a content section (the
            per-type lock on Manage → Content types)."""
            if getattr(g, 'org', None) is None:
                return True
            from .models import Content
            return Content.section_readable_by_current_visitor(type_slug)

        def discussions_area_readable():
            """Whether the current viewer may see the discussions area at
            all (the org-wide discussions_visibility switch)."""
            if getattr(g, 'org', None) is None:
                return True
            from .models.discussion import DiscussionGroup
            return DiscussionGroup.area_readable_by_current_visitor()

        def managing():
            """Manage mode: presentation only — surfaces controls the user
            already has permission for. Never consulted for authorization."""
            from flask import session
            return bool(session.get('manage_mode')
                        and getattr(g, 'membership', None) is not None
                        and (can('content.write') or can('content.moderate')))

        return {
            'installation_name': installation_name,
            'can': can,
            'can_view': can_view,
            'is_org_member': is_org_member,
            'app_version': APP_VERSION,
            'nav_items': NavigationItem.items_for,
            'unread_notifications': unread_notifications,
            'managing': managing,
            'start_here_page': start_here_page,
            'latest_announcement': latest_announcement,
            'upcoming_event': upcoming_event,
            'rail_members': rail_members,
            'discussions_area_readable': discussions_area_readable,
            'section_readable': section_readable,
            'content_types': active_types,
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

    @app.template_filter('month_abbr')
    def month_abbr(value):
        """'2026-05-24' -> 'MAY' (locale-independent, for the event date chip)."""
        import calendar
        try:
            return calendar.month_abbr[int(str(value)[5:7])].upper()
        except (ValueError, IndexError):
            return '?'

    @app.template_filter('timeago')
    def timeago(value):
        """Feed-style relative time; falls back to the date past a week."""
        from .models.base import utcnow
        from .platform.i18n import t
        if value is None:
            return ''
        delta = utcnow() - value
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return t('time.just_now')
        if seconds < 3600:
            return t('time.minutes_ago', n=seconds // 60)
        if seconds < 86400:
            return t('time.hours_ago', n=seconds // 3600)
        if seconds < 7 * 86400:
            return t('time.days_ago', n=seconds // 86400)
        return value.strftime('%Y-%m-%d')


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
