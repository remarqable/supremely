"""Authentication: email + password (auth: password).

No flow here depends on outbound email. Recovery is `flask users
reset-password EMAIL` -- see app/controllers/cli.py.
"""

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from app.middleware.ratelimit import rate_limit
from app.models import User
from app.platform.errors import ValidationError
from app.platform.i18n import t
from app.platform.logger import get_logger
from app.platform.redirects import safe_next

bp = Blueprint('auth', __name__, url_prefix='/auth')
log = get_logger()


def _safe_next(default: str) -> str:
    return safe_next(request.args.get('next') or request.form.get('next'),
                     default)


def _signups_enabled() -> bool:
    from app.models import InstallationSetting
    return InstallationSetting.get_bool(
        'installation.allow_organization_signups', False)


@bp.route('/register', methods=['GET', 'POST'])
@rate_limit(limit=10, window=300)
def register():
    """Public signup (spec Phase 8): register -> create organization ->
    owner -> enter. Only when the installation allows organization signups."""
    if not _signups_enabled():
        abort(404)
    if current_user.is_authenticated:
        return redirect(url_for('orgs.create'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        name = request.form.get('name', '').strip()
        password = request.form.get('password', '')

        if User.get_by_email(email) is not None:
            flash(t('auth.register_exists'), 'error')
            return render_template('auth/register.html'), 400
        try:
            user = User.create(email=email, name=name or email.split('@')[0],
                               password=password)
        except ValidationError as e:
            flash(e.message, 'error')
            return render_template('auth/register.html'), 400

        session.clear()
        login_user(user, remember=True)
        log.info('user_registered', user_id=user.id)
        return redirect(url_for('orgs.create'))

    return render_template('auth/register.html')


@bp.route('/login', methods=['GET', 'POST'])
@rate_limit(limit=10, window=60)
def login():
    if current_user.is_authenticated:
        return redirect(_safe_next(url_for('orgs.launcher')))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        user = User.get_by_email(email)

        # One generic failure message: distinguishing "no such user" from
        # "wrong password" is a user-enumeration oracle.
        if user is None or not user.is_active or not user.check_password(password):
            log.info('login_failed', email=email)
            flash(t('auth.invalid_credentials'), 'error')
            return render_template('auth/login.html'), 401

        session.clear()                  # regenerate: prevents session fixation
        login_user(user, remember=True)
        log.info('user_logged_in', user_id=user.id)

        # /auth is an installation path, so resolve the host's org explicitly.
        from app.platform.tenant import org_for_request_host
        org = org_for_request_host()
        if org is not None and org.is_active:
            from app.models import Membership
            membership = Membership.get(user.id, org.id)
            if membership is not None and membership.is_active:
                # Members land in the community, not on the marketing site.
                return redirect(_safe_next(url_for('orgs.dashboard')))
            return redirect(_safe_next(url_for('main.index')))
        return redirect(_safe_next(url_for('orgs.launcher')))

    return render_template('auth/login.html',
                           signups_enabled=_signups_enabled())


@bp.route('/logout', methods=['POST'])
def logout():
    if current_user.is_authenticated:
        log.info('user_logged_out', user_id=current_user.id)
    logout_user()
    # Keep the marker logout_user() set to delete the remember cookie.
    remember = session.get('_remember')
    session.clear()
    if remember is not None:
        session['_remember'] = remember
    flash(t('auth.logged_out'), 'success')
    return redirect(url_for('main.index'))


@bp.route('/password', methods=['GET', 'POST'])
@login_required
@rate_limit(limit=10, window=300)
def change_password():
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        new = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')

        if not current_user.check_password(current):
            flash(t('auth.current_password_wrong'), 'error')
        elif new != confirm:
            flash(t('auth.passwords_do_not_match'), 'error')
        else:
            try:
                user = current_user._get_current_object()
                user.set_password(new)
                user.save()
                # The stamp in the session id is derived from the password,
                # so every session and remember cookie is now stale --
                # including this one. Re-issue the current one only.
                login_user(user, remember=True)
                log.info('password_changed', user_id=user.id)
                flash(t('auth.password_changed'), 'success')
                return redirect(url_for('auth.change_password'))
            except Exception as e:
                from app.platform.errors import ValidationError
                if isinstance(e, ValidationError):
                    flash(e.message, 'error')
                else:
                    raise

    return render_template('auth/password.html')
