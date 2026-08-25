"""Platform administration: /admin. Requires the installation-level
Platform Admin privilege. Organization, User, Membership, and Job are not
OrgScoped, so these views naturally see the whole installation."""

import platform as _platform
import sys

import sqlalchemy as sa
from flask import (Blueprint, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user

from app.extensions import db
from app.models import (InstallationSetting, Job, Membership, Organization,
                        User)
from app.platform.authz import platform_admin_required
from app.platform.errors import ValidationError
from app.platform.i18n import t
from app.platform.logger import get_logger
from app.platform.mailer import is_email_configured, send_email

bp = Blueprint('admin', __name__, url_prefix='/admin')
log = get_logger()


@bp.before_request
@platform_admin_required
def gate():
    pass


@bp.route('/')
def dashboard():
    stats = {
        'organizations': Organization.query.count(),
        'active_organizations': Organization.query.filter_by(is_active=True).count(),
        'users': User.query.count(),
        'memberships': Membership.query.count(),
        'jobs_pending': Job.query.filter_by(status='pending').count(),
        'jobs_failed': Job.query.filter_by(status='failed').count(),
    }
    recent_orgs = (Organization.query
                   .order_by(Organization.created_at.desc()).limit(5).all())
    return render_template('admin/dashboard.html', stats=stats,
                           recent_orgs=recent_orgs,
                           email_configured=is_email_configured())


# --- Organizations -----------------------------------------------------------

@bp.route('/orgs')
def orgs():
    q = request.args.get('q', '').strip()
    query = Organization.query
    if q:
        query = query.filter(sa.or_(Organization.name.ilike(f'%{q}%'),
                                    Organization.slug.ilike(f'%{q}%')))
    organizations = query.order_by(Organization.created_at.desc()).all()
    return render_template('admin/orgs.html', organizations=organizations, q=q)


@bp.route('/orgs/new', methods=['GET', 'POST'])
def new_org():
    users = User.query.order_by(User.email).all()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        slug = request.form.get('slug', '').strip().lower()
        owner_id = request.form.get('owner_id', type=int)
        owner = db.session.get(User, owner_id) if owner_id else current_user
        if owner is None:
            flash(t('admin.owner_not_found'), 'error')
            return render_template('admin/org_new.html', users=users), 400
        try:
            org = Organization.provision(name=name, slug=slug, owner=owner)
            log.info('org_created', org_id=org.id, slug=org.slug, by='admin')
            flash(t('orgs.created', name=org.name), 'success')
            return redirect(url_for('admin.org_detail', org_id=org.id))
        except ValidationError as e:
            flash(e.message, 'error')
    return render_template('admin/org_new.html', users=users)


@bp.route('/orgs/<int:org_id>')
def org_detail(org_id):
    org = db.get_or_404(Organization, org_id)
    memberships = (Membership.query.filter_by(org_id=org.id)
                   .join(Membership.user).order_by(User.email).all())
    return render_template('admin/org_detail.html', org=org,
                           memberships=memberships)


@bp.route('/orgs/<int:org_id>/edit', methods=['POST'])
def org_edit(org_id):
    org = db.get_or_404(Organization, org_id)
    org.name = request.form.get('name', org.name).strip()
    org.description = request.form.get('description', '').strip() or None
    try:
        org.save()
        flash(t('common.saved'), 'success')
    except ValidationError as e:
        flash(e.message, 'error')
    return redirect(url_for('admin.org_detail', org_id=org.id))


@bp.route('/orgs/<int:org_id>/suspend', methods=['POST'])
def org_suspend(org_id):
    org = db.get_or_404(Organization, org_id)
    org.suspend()
    log.info('org_suspended', org_id=org.id)
    flash(t('admin.org_suspended', name=org.name), 'success')
    return redirect(url_for('admin.org_detail', org_id=org.id))


@bp.route('/orgs/<int:org_id>/reactivate', methods=['POST'])
def org_reactivate(org_id):
    org = db.get_or_404(Organization, org_id)
    org.reactivate()
    log.info('org_reactivated', org_id=org.id)
    flash(t('admin.org_reactivated', name=org.name), 'success')
    return redirect(url_for('admin.org_detail', org_id=org.id))


@bp.route('/orgs/<int:org_id>/archive', methods=['POST'])
def org_archive(org_id):
    org = db.get_or_404(Organization, org_id)
    org.archive()
    log.info('org_archived', org_id=org.id)
    flash(t('admin.org_archived', name=org.name), 'success')
    return redirect(url_for('admin.org_detail', org_id=org.id))


@bp.route('/orgs/<int:org_id>/members', methods=['POST'])
def org_add_member(org_id):
    org = db.get_or_404(Organization, org_id)
    email = request.form.get('email', '').strip().lower()
    role = request.form.get('role', 'member')
    user = User.get_by_email(email)
    if user is None:
        flash(t('admin.user_not_found', email=email), 'error')
    else:
        try:
            Membership.add(user.id, org.id, role=role)
            log.info('membership_added', org_id=org.id, user_id=user.id, role=role)
            flash(t('admin.member_added', email=email), 'success')
        except ValidationError as e:
            flash(e.message, 'error')
    return redirect(url_for('admin.org_detail', org_id=org.id))


@bp.route('/memberships/<int:membership_id>/role', methods=['POST'])
def membership_role(membership_id):
    membership = db.get_or_404(Membership, membership_id)
    org_id = membership.org_id
    try:
        membership.change_role(request.form.get('role', 'member'))
        flash(t('common.saved'), 'success')
    except ValidationError as e:
        flash(e.message, 'error')
    return redirect(url_for('admin.org_detail', org_id=org_id))


@bp.route('/memberships/<int:membership_id>/delete', methods=['POST'])
def membership_delete(membership_id):
    membership = db.get_or_404(Membership, membership_id)
    org_id = membership.org_id
    try:
        membership.remove()
        flash(t('admin.member_removed'), 'success')
    except ValidationError as e:
        flash(e.message, 'error')
    return redirect(url_for('admin.org_detail', org_id=org_id))


# --- Users -------------------------------------------------------------------

@bp.route('/users')
def users():
    q = request.args.get('q', '').strip()
    query = User.query
    if q:
        query = query.filter(sa.or_(User.email.ilike(f'%{q}%'),
                                    User.name.ilike(f'%{q}%')))
    user_list = query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=user_list, q=q)


@bp.route('/users/new', methods=['GET', 'POST'])
def new_user():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        name = request.form.get('name', '').strip()
        password = request.form.get('password', '')
        is_admin = request.form.get('is_platform_admin') == 'on'
        try:
            user = User.create(email=email, name=name, password=password,
                               is_platform_admin=is_admin)
            log.info('user_created', user_id=user.id, by='admin')
            flash(t('admin.user_created', email=email), 'success')
            return redirect(url_for('admin.user_detail', user_id=user.id))
        except ValidationError as e:
            flash(e.message, 'error')
    return render_template('admin/user_new.html')


@bp.route('/users/<int:user_id>')
def user_detail(user_id):
    user = db.get_or_404(User, user_id)
    memberships = (Membership.query.filter_by(user_id=user.id)
                   .join(Membership.organization).order_by(Organization.name).all())
    organizations = Organization.query.order_by(Organization.name).all()
    return render_template('admin/user_detail.html', user=user,
                           memberships=memberships, organizations=organizations)


@bp.route('/users/<int:user_id>/memberships', methods=['POST'])
def user_add_membership(user_id):
    user = db.get_or_404(User, user_id)
    org_id = request.form.get('org_id', type=int)
    role = request.form.get('role', 'member')
    org = db.session.get(Organization, org_id) if org_id else None
    if org is None:
        flash(t('admin.org_not_found'), 'error')
    else:
        try:
            Membership.add(user.id, org.id, role=role)
            flash(t('admin.member_added', email=user.email), 'success')
        except ValidationError as e:
            flash(e.message, 'error')
    return redirect(url_for('admin.user_detail', user_id=user.id))


@bp.route('/users/<int:user_id>/toggle-admin', methods=['POST'])
def user_toggle_admin(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash(t('admin.cannot_demote_self'), 'error')
    else:
        user.is_platform_admin = not user.is_platform_admin
        user.save()
        log.info('platform_admin_toggled', user_id=user.id,
                 is_platform_admin=user.is_platform_admin)
        flash(t('common.saved'), 'success')
    return redirect(url_for('admin.user_detail', user_id=user.id))


@bp.route('/users/<int:user_id>/toggle-active', methods=['POST'])
def user_toggle_active(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash(t('admin.cannot_deactivate_self'), 'error')
    else:
        user.is_active = not user.is_active
        user.save()
        flash(t('common.saved'), 'success')
    return redirect(url_for('admin.user_detail', user_id=user.id))


@bp.route('/users/<int:user_id>/password', methods=['POST'])
def user_set_password(user_id):
    user = db.get_or_404(User, user_id)
    try:
        user.set_password(request.form.get('password', ''))
        user.save()
        log.info('password_reset_by_admin', user_id=user.id)
        flash(t('auth.password_changed'), 'success')
    except ValidationError as e:
        flash(e.message, 'error')
    return redirect(url_for('admin.user_detail', user_id=user.id))


# --- Installation settings ---------------------------------------------------

@bp.route('/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'POST':
        section = request.form.get('section', 'general')
        if section == 'general':
            InstallationSetting.set('installation.name',
                                    request.form.get('name', '').strip())
            InstallationSetting.set('installation.allow_organization_signups',
                                    'true' if request.form.get('allow_signups') == 'on'
                                    else 'false')
            InstallationSetting.set('installation.timezone',
                                    request.form.get('timezone', 'UTC').strip())
            InstallationSetting.set('installation.language',
                                    request.form.get('language', 'en').strip())
        elif section == 'email':
            for field in ('smtp_host', 'smtp_port', 'smtp_username',
                          'smtp_password', 'from_address'):
                InstallationSetting.set(f'email.{field}',
                                        request.form.get(field, '').strip())
            InstallationSetting.set('email.use_tls',
                                    'true' if request.form.get('use_tls') == 'on'
                                    else 'false')
        flash(t('common.saved'), 'success')
        return redirect(url_for('admin.settings'))

    values = InstallationSetting.get_map()
    return render_template('admin/settings.html', values=values,
                           email_configured=is_email_configured())


@bp.route('/settings/test-email', methods=['POST'])
def test_email():
    to = request.form.get('to', '').strip() or current_user.email
    try:
        send_email(to, 'Supremely test email',
                   'Email delivery from your Supremely installation works.')
        flash(t('admin.test_email_sent', to=to), 'success')
    except Exception as e:      # noqa: BLE001 -- report any SMTP failure to the admin
        flash(t('admin.test_email_failed', error=str(e)), 'error')
    return redirect(url_for('admin.settings'))


# --- System ------------------------------------------------------------------

@bp.route('/system')
def system():
    from app import APP_VERSION
    engine = db.engine
    info = {
        'version': APP_VERSION,
        'python': sys.version.split()[0],
        'platform': _platform.platform(),
        'database': engine.dialect.name,
        'database_url': engine.url.render_as_string(hide_password=True),
        'email_configured': is_email_configured(),
    }
    job_stats = dict(db.session.execute(
        sa.select(Job.status, sa.func.count()).group_by(Job.status)).all())
    return render_template('admin/system.html', info=info, job_stats=job_stats)
