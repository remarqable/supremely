"""Authentication: email + password (auth: password).

No flow here depends on outbound email. Recovery is `flask users
reset-password EMAIL` -- see app/controllers/cli.py.
"""

from flask import (Blueprint, flash, g, redirect, render_template, request,
                   session, url_for)
from flask_login import current_user, login_required, login_user, logout_user

from app.models import User
from app.middleware.ratelimit import rate_limit
from app.platform.i18n import t
from app.platform.logger import get_logger

bp = Blueprint('auth', __name__, url_prefix='/auth')
log = get_logger()


def _safe_next(default: str) -> str:
    nxt = request.args.get('next') or request.form.get('next') or ''
    if nxt.startswith('/') and not nxt.startswith('//'):
        return nxt
    return default


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

        if g.org is not None:
            return redirect(_safe_next(url_for('main.index')))
        return redirect(_safe_next(url_for('orgs.launcher')))

    return render_template('auth/login.html')


@bp.route('/logout', methods=['POST'])
def logout():
    if current_user.is_authenticated:
        log.info('user_logged_out', user_id=current_user.id)
    logout_user()
    session.clear()
    flash(t('auth.logged_out'), 'success')
    return redirect(url_for('main.index'))


@bp.route('/password', methods=['GET', 'POST'])
@login_required
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
                current_user.set_password(new)
                current_user.save()
                log.info('password_changed', user_id=current_user.id)
                flash(t('auth.password_changed'), 'success')
                return redirect(url_for('auth.change_password'))
            except Exception as e:
                from app.platform.errors import ValidationError
                if isinstance(e, ValidationError):
                    flash(e.message, 'error')
                else:
                    raise

    return render_template('auth/password.html')
