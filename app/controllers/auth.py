"""Authentication: email + password (auth: password).

No flow here depends on outbound email. Recovery is `flask users
reset-password EMAIL` -- see app/controllers/cli.py.
"""

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from app.middleware.ratelimit import (
    clear_failures,
    rate_limit,
    record_failure,
    too_many_failures,
)
from app.models import User
from app.models.user import verify_credentials
from app.platform.devices import render_device_template
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
    return InstallationSetting.organization_signups_enabled()


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
            return render_device_template('auth/register.html'), 400
        try:
            user = User.create(email=email, name=name or email.split('@')[0],
                               password=password)
        except ValidationError as e:
            flash(e.message, 'error')
            return render_device_template('auth/register.html'), 400

        session.clear()
        login_user(user, remember=True)
        log.info('user_registered', user_id=user.id)
        return redirect(url_for('orgs.create'))

    return render_device_template('auth/register.html')


# Per address limits bound one attacker; this bounds one account being
# guessed at from anywhere. Both together is what OWASP asks for, and it
# says to count against the account rather than the caller, since counting
# the caller is what an attacker escapes by using more of them.
#
# A hundred is the ceiling NIST sets on consecutive failures for one
# account. Anything lower protects a little more and costs a lot more,
# because a budget is also what someone spends to hold an account shut:
# nobody reaches a hundred by fumbling, and an attacker must spend all
# hundred, repeatedly, to keep it shut.
#
# The counter is consecutive in the sense that matters. Signing in clears
# it, so an ordinary bad week never accumulates.
#
# Not done here, and worth knowing why: NIST also offers a delay that grows
# as the budget runs down. A delay holds a worker open, and these are
# synchronous workers, so a slow refusal is its own denial of service.
LOGIN_FAILURES = 100
LOGIN_FAILURE_WINDOW = 900


@bp.route('/login', methods=['GET', 'POST'])
@rate_limit(limit=10, window=60)
def login():
    if current_user.is_authenticated:
        return redirect(_safe_next(url_for('orgs.launcher')))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        user = User.get_by_email(email)

        # Counted against the address that was typed, whether or not it
        # names an account. Counting only real ones would say which is
        # which, which is the oracle the rest of this block avoids.
        spent = too_many_failures(email, LOGIN_FAILURES, LOGIN_FAILURE_WINDOW)

        # One generic failure message, and one fixed cost to produce it:
        # distinguishing "no such user" from "wrong password" by wording or
        # by the clock is the same oracle. The comparison runs even when the
        # budget is spent, so a refusal costs what an attempt costs.
        ok = verify_credentials(user, password)
        if spent or not ok:
            if not ok:
                record_failure(email, LOGIN_FAILURE_WINDOW)
            # Truncated for the same reason the counter key is: the field
            # is whatever was typed, and a log line is another place it
            # would otherwise arrive at full size.
            if spent:
                log.warning('login_throttled', email=email[:255])
            log.info('login_failed', email=email[:255])
            flash(t('auth.invalid_credentials'), 'error')
            return render_device_template('auth/login.html'), 401

        clear_failures(email)

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

    return render_device_template('auth/login.html',
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

    return render_device_template('auth/password.html')
