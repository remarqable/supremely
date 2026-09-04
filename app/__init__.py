"""Supremely application factory."""

import contextlib
from pathlib import Path

from flask import Flask, abort, g, redirect, request

from .config import Config
from .extensions import db, init_sqlite_pragmas, login_manager, migrate
from .platform.logger import get_logger, init_logger

# One source of truth for the release, kept in step with
# pyproject.toml and the top entry of CHANGELOG.md (see
# tests/test_platform/test_version.py). Docker images are tagged
# with it, and :latest always points at the newest release.
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
        # The content memos below are the same hazard with a worse blast
        # radius: `g` is application-context scoped, so a memo left behind
        # would serve one organization's rows on another's page.
        from flask import g as _g
        _g.pop('_login_user', None)
        _g.pop('_content_feeds', None)
        _g.pop('_content_counts', None)
        # Template resolution is memoized for the same reason and with the
        # same hazard: a held app context would otherwise carry one
        # request's answers into the next.
        _g.pop('_template_exists', None)

    # Before CSRF and tenant resolution, so a blocked request answers 404
    # rather than 403 or 410 and never resolves a tenant.
    from .platform.plugins import block_private_mounts
    block_private_mounts(app)

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
    from .models.base import DERIVED_SLUG_MAX
    from .platform.attribution import powered_by_url
    from .platform.authz import (
        can,
        can_view,
        is_member_or_platform_admin,
        is_org_member,
    )
    from .platform.devices import device_type, is_mobile
    from .platform.redirects import current_target
    from .platform.theming import shell_layout

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
        from .platform.content_types import active_types, community_types

        def unread_notifications():
            if getattr(g, 'membership', None) is None:
                return 0
            from .models.notification import Notification
            return Notification.unread_count(current_user.id)

        def _member_view():
            """The shell serves visitors too; rail cards must not leak
            gated content or member data to them."""
            return is_member_or_platform_admin()

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

        def _memo_key(type_slug: str) -> tuple:
            """Cache identity for a content question.

            The organization and the viewer are both part of the question:
            the same type returns different rows for a member than for a
            visitor, and must never return one organization's rows to
            another. The memo is cleared per request as well (see
            _reset_request_state); this key is the second lock on the same
            door, because a cache that outlives its request is the one way
            to defeat the global tenant filter.
            """
            return (g.org.id, _member_view(), type_slug)

        def latest_content(type_slug: str, limit: int | None = None) -> list:
            """Published items of a content type, newest first.

            The theme contract's data verb: a theme names what it wants and
            the application decides how to fetch it, so a front page can
            render a grid of articles, episodes or team members without any
            code that knows that theme exists. Lazy (nothing runs until a
            template asks) and memoized per request, so two sections asking
            the same question cost one query.
            """
            if getattr(g, 'org', None) is None or not type_slug:
                return []
            cache = g.setdefault('_content_feeds', {})
            key = (*_memo_key(type_slug), limit)
            if key not in cache:
                from .models import Content
                cache[key] = Content.feed(type_slug, limit)
            return cache[key]

        def content_count(type_slug: str) -> int:
            """How many published items of a type the visitor may see."""
            if getattr(g, 'org', None) is None or not type_slug:
                return 0
            cache = g.setdefault('_content_counts', {})
            key = _memo_key(type_slug)
            if key not in cache:
                from .models import Content
                cache[key] = Content.feed_count(type_slug)
            return cache[key]

        def discussions_area_readable():
            """Whether the current viewer may see the discussions area at
            all (the org-wide discussions_visibility switch)."""
            if getattr(g, 'org', None) is None:
                return True
            from .models.discussion import DiscussionGroup
            return DiscussionGroup.area_readable_by_current_visitor()

        return {
            'installation_name': installation_name,
            'can': can,
            'current_target': current_target,
            'can_view': can_view,
            'is_org_member': is_org_member,
            'is_member_or_platform_admin': is_member_or_platform_admin,
            'app_version': APP_VERSION,
            'nav_items': NavigationItem.items_for,
            'unread_notifications': unread_notifications,
            'latest_announcement': latest_announcement,
            'upcoming_event': upcoming_event,
            'rail_members': rail_members,
            'discussions_area_readable': discussions_area_readable,
            'section_readable': section_readable,
            'content_types': active_types,
            'community_types': community_types,
            'latest_content': latest_content,
            'content_count': content_count,
            'powered_by_url': powered_by_url,
            'derived_slug_max': DERIVED_SLUG_MAX,
            # Values, not the functions: `{% if is_mobile %}` on a
            # callable is always true, and the first mobile template written
            # would have hit that silently.
            'is_mobile': is_mobile(),
            'device_type': device_type(),
            'community_layout': shell_layout(),
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
    from .platform.analytics import analytics_csp_sources
    from .platform.theming import PREVIEW_ENDPOINT

    @app.after_request
    def add_security_headers(response):
        # An HTML page can render differently per device (see
        # app/platform/devices.py), so anything caching it has to key on
        # that. Only HTML branches, so assets and JSON keep a clean cache.
        if response.mimetype == 'text/html':
            response.vary.add('User-Agent')
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        # Nothing may frame this application, with one exception: the theme
        # preview, which Manage -> Theme shows in an iframe on the same
        # origin. Read off the endpoint rather than a flag a view sets,
        # so no other response can opt itself in and nothing can leak.
        frameable = (request.endpoint == PREVIEW_ENDPOINT
                     and response.status_code == 200)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # The org's configured analytics tracker (app/platform/analytics.py)
        # widens script-src/connect-src to exactly its hosts, on the org site
        # only — the consoles and every unconfigured tenant keep the strict
        # baseline.
        script_hosts, connect_hosts = ([], []) if request.blueprint in (
            'manage', 'admin') else analytics_csp_sources()
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            f"script-src 'self' 'unsafe-eval'"
            f"{''.join(' ' + host for host in script_hosts)}; "
            + (f"connect-src 'self' {' '.join(connect_hosts)}; "
               if connect_hosts else '')
            + "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
            "object-src 'none'; base-uri 'self'; "
            f"frame-ancestors {"'self'" if frameable else "'none'"}"
        )
        if not app.debug and not app.testing:
            response.headers['Strict-Transport-Security'] = \
                'max-age=31536000; includeSubDomains'
        return response


def _register_error_handlers(app):
    from .platform.errors import AppError
    from .platform.theming import render_error

    # Error pages render through the theme chain on an organization's own
    # site, and unthemed everywhere else (the console, the installer, and any
    # request that resolved no organization). See theming.render_error.

    @app.errorhandler(AppError)
    def handle_app_error(error):
        if error.http_status >= 500:
            db.session.rollback()
        return render_error(error.http_status, error.message), error.http_status

    @app.errorhandler(403)
    def forbidden(error):
        return render_error(403), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_error(404), 404

    @app.errorhandler(410)
    def gone(error):
        return render_error(410), 410

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        return render_error(500), 500
