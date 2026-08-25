"""First-run installation wizard.

Bootstrap model (spec § 4): the app always boots on SQLite with safe
defaults; the wizard collects configuration in the session and applies it in
one final step. Choosing PostgreSQL writes DATABASE_URL to data/config.env,
migrates the new database, seeds it, and asks for a restart. Nothing here
requires outbound email.
"""

import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import sqlalchemy as sa
from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)
from flask_login import login_user

from app.extensions import db
from app.models import Organization, User
from app.platform.config_store import mark_installed, write_runtime_config
from app.platform.errors import ValidationError
from app.platform.i18n import t
from app.platform.install import migrate_and_seed_postgres, seed_installation
from app.platform.logger import get_logger

bp = Blueprint('setup', __name__, url_prefix='/setup')
log = get_logger()

STEPS = ('environment', 'database', 'admin', 'email', 'organization')

COMMON_TIMEZONES = (
    'UTC', 'America/New_York', 'America/Chicago', 'America/Denver',
    'America/Los_Angeles', 'America/Sao_Paulo', 'Europe/London',
    'Europe/Berlin', 'Europe/Paris', 'Europe/Madrid', 'Africa/Cairo',
    'Asia/Dubai', 'Asia/Karachi', 'Asia/Kolkata', 'Asia/Singapore',
    'Asia/Tokyo', 'Australia/Sydney',
)


# Wizard state (admin password, DB password, SMTP password) is kept
# SERVER-SIDE in a scratch file on the data volume, never in the signed-but-
# unencrypted session cookie. The session holds only an opaque handle.
def _scratch_path(handle: str) -> Path:
    safe = re.sub(r'[^a-f0-9]', '', handle)[:64]
    return Path(current_app.config['DATA_DIR']) / f'.wizard-{safe}.json'


def _state() -> dict:
    handle = session.get('setup_handle')
    if not handle:
        return {}
    path = _scratch_path(handle)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    handle = session.get('setup_handle')
    if not handle:
        handle = secrets.token_hex(16)
        session['setup_handle'] = handle
    path = _scratch_path(handle)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.wizard.')
    with os.fdopen(fd, 'w') as fh:
        json.dump(state, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _clear_state() -> None:
    handle = session.pop('setup_handle', None)
    if handle:
        _scratch_path(handle).unlink(missing_ok=True)


@bp.route('/')
def index():
    return render_template('setup/welcome.html')


@bp.route('/environment', methods=['GET', 'POST'])
def environment():
    state = _state()
    if request.method == 'POST':
        name = request.form.get('name', '').strip() or 'Supremely'
        base_url = request.form.get('base_url', '').strip() or request.url_root
        timezone = request.form.get('timezone', 'UTC').strip() or 'UTC'
        language = request.form.get('language', 'en').strip() or 'en'

        parsed = urlparse(base_url if '//' in base_url else f'http://{base_url}')
        if not parsed.hostname:
            flash(t('setup.invalid_base_url'), 'error')
        else:
            state['environment'] = {
                'name': name, 'base_url': base_url, 'timezone': timezone,
                'language': language, 'base_domain': parsed.hostname,
            }
            _save_state(state)
            return redirect(url_for('setup.database'))

    defaults = state.get('environment', {
        'name': 'Supremely', 'base_url': request.url_root.rstrip('/'),
        'timezone': 'UTC', 'language': 'en',
    })
    return render_template('setup/environment.html', defaults=defaults,
                           timezones=COMMON_TIMEZONES, step='environment')


@bp.route('/database', methods=['GET', 'POST'])
def database():
    state = _state()
    if request.method == 'POST':
        engine = request.form.get('engine', 'sqlite')
        if engine == 'sqlite':
            state['database'] = {'engine': 'sqlite'}
            _save_state(state)
            return redirect(url_for('setup.admin'))

        host = request.form.get('host', 'localhost').strip()
        port = request.form.get('port', '5432').strip() or '5432'
        dbname = request.form.get('dbname', '').strip()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not (host and dbname and username):
            flash(t('setup.postgres_fields_required'), 'error')
        elif not port.isdigit():
            flash(t('setup.postgres_fields_required'), 'error')
        else:
            # URL.create percent-encodes every component, so special
            # characters in the password/username cannot re-point the host or
            # inject libpq connection parameters. Never f-string a DSN.
            url = sa.engine.URL.create(
                'postgresql+psycopg', username=username, password=password,
                host=host, port=int(port), database=dbname,
            ).render_as_string(hide_password=False)
            ok, error = _test_connection(url)
            if ok:
                state['database'] = {'engine': 'postgres', 'url': url}
                _save_state(state)
                flash(t('setup.connection_ok'), 'success')
                return redirect(url_for('setup.admin'))
            flash(t('setup.connection_failed', error=error), 'error')

    return render_template('setup/database.html', step='database',
                           defaults=state.get('database', {}))


def _test_connection(url: str) -> tuple[bool, str]:
    try:
        engine = sa.create_engine(url, connect_args={'connect_timeout': 5})
        with engine.connect() as conn:
            conn.execute(sa.text('SELECT 1'))
        engine.dispose()
        return True, ''
    except Exception as e:      # noqa: BLE001 -- surface any driver error to the operator
        return False, str(e)


@bp.route('/admin', methods=['GET', 'POST'])
def admin():
    state = _state()
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            flash(t('setup.invalid_email'), 'error')
        elif len(password) < User.MIN_PASSWORD_LENGTH:
            flash(t('setup.password_too_short', n=User.MIN_PASSWORD_LENGTH), 'error')
        elif password != confirm:
            flash(t('auth.passwords_do_not_match'), 'error')
        else:
            state['admin'] = {'email': email, 'password': password}
            _save_state(state)
            return redirect(url_for('setup.email'))

    return render_template('setup/admin.html', step='admin',
                           defaults=state.get('admin', {}))


@bp.route('/email', methods=['GET', 'POST'])
def email():
    state = _state()
    if request.method == 'POST':
        if request.form.get('skip'):
            state['email'] = {}
        else:
            state['email'] = {
                'smtp_host': request.form.get('smtp_host', '').strip(),
                'smtp_port': request.form.get('smtp_port', '587').strip(),
                'smtp_username': request.form.get('smtp_username', '').strip(),
                'smtp_password': request.form.get('smtp_password', ''),
                'from_address': request.form.get('from_address', '').strip(),
                'use_tls': 'true' if request.form.get('use_tls', 'on') == 'on' else 'false',
            }
        _save_state(state)
        return redirect(url_for('setup.organization'))

    return render_template('setup/email.html', step='email',
                           defaults=state.get('email', {}))


@bp.route('/organization', methods=['GET', 'POST'])
def organization():
    state = _state()
    if not all(k in state for k in ('environment', 'database', 'admin')):
        flash(t('setup.incomplete'), 'error')
        return redirect(url_for('setup.environment'))

    if request.method == 'POST':
        if request.form.get('skip'):
            state['organization'] = {}
        else:
            name = request.form.get('name', '').strip()
            slug = request.form.get('slug', '').strip().lower()
            probe = Organization(name=name, slug=slug)
            try:
                # Validate format only; existence checks are meaningless on a
                # fresh install and the DB may be the not-yet-active postgres.
                if not name:
                    raise ValidationError('Organization name is required')
                if not re.fullmatch(r'[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?', slug):
                    raise ValidationError('Slug must be 3-63 chars: a-z, 0-9 and hyphens')
                if slug in Organization.RESERVED_SLUGS:
                    raise ValidationError('That slug is reserved')
            except ValidationError as e:
                flash(e.message, 'error')
                return render_template('setup/organization.html', step='organization',
                                       defaults={'name': name, 'slug': slug})
            state['organization'] = {'name': name, 'slug': slug}
        _save_state(state)
        return _apply(state)

    return render_template('setup/organization.html', step='organization',
                           defaults=state.get('organization', {}))


def _apply(state: dict):
    app = current_app
    env = state['environment']
    database = state['database']
    postgres = database.get('engine') == 'postgres'

    # SECRET_KEY is self-managed by config._resolve_secret_key (persisted on
    # the data volume); the wizard no longer touches it.
    config_updates = {
        'BASE_DOMAIN': env['base_domain'],
    }

    if postgres:
        config_updates['DATABASE_URL'] = database['url']
        write_runtime_config(app, config_updates)
        ok, error = migrate_and_seed_postgres(database['url'], state)
        if not ok:
            flash(t('setup.postgres_apply_failed', error=error), 'error')
            return redirect(url_for('setup.database'))
        mark_installed(app)
        _clear_state()
        log.info('installation_complete', database='postgres')
        return render_template('setup/done.html', restart_required=True)

    # SQLite: the running database is already the real one.
    write_runtime_config(app, config_updates)
    admin_user = seed_installation(db.session, state)
    mark_installed(app)
    _clear_state()

    session.clear()
    login_user(admin_user, remember=True)
    log.info('installation_complete', database='sqlite')
    return render_template('setup/done.html', restart_required=False,
                           org=state.get('organization') or None)

