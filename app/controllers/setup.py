"""First-run installation wizard.

Three steps: environment, administrator, first organization.

The wizard does not choose a database engine. Configuration resolves that
before the app boots (app/config.py) and the schema is migrated before the
first request, so every step here writes to the database already in use.
Outbound email is configured later in Administration -> Settings; nothing in
this flow requires it.
"""

import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    request,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue
from flask_login import login_user

from app.extensions import db
from app.models import Organization, User
from app.models.common_passwords import COMMON_PASSWORDS
from app.platform.config_store import mark_installed, write_runtime_config
from app.platform.devices import render_device_template
from app.platform.errors import ValidationError
from app.platform.i18n import t
from app.platform.install import seed_installation
from app.platform.logger import get_logger

bp = Blueprint('setup', __name__, url_prefix='/setup')
log = get_logger()

# The identity itself is a domain rule and lives on the model; the wizard
# only decides that this is the account it creates.
ADMIN_USERNAME = User.INSTALL_ADMIN_USERNAME

# Starting points, not constraints: both fields stay editable.
DEFAULT_ORG = {'name': 'Our community', 'slug': 'our-community'}

COMMON_TIMEZONES = (
    'UTC', 'America/New_York', 'America/Chicago', 'America/Denver',
    'America/Los_Angeles', 'America/Sao_Paulo', 'Europe/London',
    'Europe/Berlin', 'Europe/Paris', 'Europe/Madrid', 'Africa/Cairo',
    'Asia/Dubai', 'Asia/Karachi', 'Asia/Kolkata', 'Asia/Singapore',
    'Asia/Tokyo', 'Australia/Sydney',
)


# Wizard state (the administrator password) is kept SERVER-SIDE in a scratch
# file on the data volume, never in the signed-but-unencrypted session cookie.
# The session holds only an opaque handle.
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
def index() -> ResponseReturnValue:
    return render_device_template('setup/welcome.html')


@bp.route('/environment', methods=['GET', 'POST'])
def environment() -> ResponseReturnValue:
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
            return redirect(url_for('setup.admin'))

    defaults = state.get('environment', {
        'name': 'Supremely', 'base_url': request.url_root.rstrip('/'),
        'timezone': 'UTC', 'language': 'en',
    })
    return render_device_template('setup/environment.html', defaults=defaults,
                           timezones=COMMON_TIMEZONES, step='environment')


@bp.route('/admin', methods=['GET', 'POST'])
def admin() -> ResponseReturnValue:
    state = _state()
    if request.method == 'POST':
        # The username is not read from the form: it is fixed for every
        # installation, so a tampered field cannot change it.
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if len(password) < User.MIN_PASSWORD_LENGTH:
            flash(t('setup.password_too_short', n=User.MIN_PASSWORD_LENGTH), 'error')
        elif password.lower() in COMMON_PASSWORDS:
            # Judged here rather than at the end of the wizard, where the
            # refusal would land two steps later as an error page with no
            # way back to the field.
            flash(t('setup.password_too_common'), 'error')
        elif password != confirm:
            flash(t('auth.passwords_do_not_match'), 'error')
        else:
            state['admin'] = {'email': ADMIN_USERNAME, 'password': password}
            _save_state(state)
            return redirect(url_for('setup.organization'))

    return render_device_template('setup/admin.html', step='admin',
                           username=ADMIN_USERNAME)


@bp.route('/organization', methods=['GET', 'POST'])
def organization() -> ResponseReturnValue:
    state = _state()
    if not all(k in state for k in ('environment', 'admin')):
        flash(t('setup.incomplete'), 'error')
        return redirect(url_for('setup.environment'))

    if request.method == 'POST':
        if request.form.get('skip'):
            state['organization'] = {}
        else:
            name = request.form.get('name', '').strip()
            slug = request.form.get('slug', '').strip().lower()
            try:
                # The model owns these rules. Re-stating them here let a name
                # longer than the column through, which is silent on SQLite
                # and aborts the install mid-write on PostgreSQL.
                Organization(name=name, slug=slug).validate()
            except ValidationError as e:
                flash(e.message, 'error')
                return render_device_template('setup/organization.html', step='organization',
                                       defaults={'name': name, 'slug': slug})
            state['organization'] = {'name': name, 'slug': slug}
        _save_state(state)
        return _apply(state)

    return render_device_template('setup/organization.html', step='organization',
                           defaults=state.get('organization') or DEFAULT_ORG)


def _apply(state: dict) -> ResponseReturnValue:
    """Seed the database the app is already running on.

    The wizard does not choose an engine. Configuration resolves the database
    before the app boots (see app/config.py): SQLite on the data volume by
    default, or whatever DATABASE_URL points at when an operator sets one. By
    the time this runs the schema is already migrated, so there is a single
    path here regardless of engine.
    """
    app = current_app
    env = state['environment']

    # SECRET_KEY is self-managed by config._resolve_secret_key (persisted on
    # the data volume); the wizard no longer touches it.
    write_runtime_config(app, {'BASE_DOMAIN': env['base_domain']})

    admin_user = seed_installation(db.session, state)
    mark_installed(app)
    _clear_state()

    session.clear()
    login_user(admin_user, remember=True)
    log.info('installation_complete',
             database='postgres' if app.config['IS_POSTGRES'] else 'sqlite')
    return render_device_template('setup/done.html')
