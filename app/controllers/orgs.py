"""Organization launcher, creation, and member-facing org pages."""

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from app.models import InstallationSetting, Membership, Organization
from app.platform.authz import org_required
from app.platform.errors import ValidationError
from app.platform.i18n import t
from app.platform.logger import get_logger
from app.platform.tenant import org_url

bp = Blueprint('orgs', __name__)
log = get_logger()


def signups_enabled() -> bool:
    return InstallationSetting.get_bool('installation.allow_organization_signups', False)


def user_may_create_org() -> bool:
    return current_user.is_platform_admin or signups_enabled()


@bp.route('/launcher')
@login_required
def launcher():
    memberships = current_user.org_memberships()

    if len(memberships) == 1 and not current_user.is_platform_admin:
        return redirect(org_url(memberships[0].organization))

    return render_template('orgs/launcher.html', memberships=memberships,
                           may_create=user_may_create_org())


@bp.route('/launcher/new', methods=['GET', 'POST'])
@login_required
def create():
    if not user_may_create_org():
        abort(403)

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        slug = request.form.get('slug', '').strip().lower()
        try:
            org = Organization.provision(name=name, slug=slug, owner=current_user)
            log.info('org_created', org_id=org.id, slug=org.slug,
                     user_id=current_user.id)
            flash(t('orgs.created', name=org.name), 'success')
            return redirect(org_url(org))
        except ValidationError as e:
            flash(e.message, 'error')

    return render_template('orgs/new.html')


@bp.route('/dashboard')
@org_required
@login_required
def dashboard():
    """Community home: where members land inside the organization."""
    if g.membership is None and not current_user.is_platform_admin:
        abort(404)
    from app.models import Post
    from app.models.discussion import Space, DiscussionPost

    spaces = [space for space in
              Space.query.order_by(Space.position, Space.name).all()
              if space.readable_by_current_visitor()]
    space_ids = [space.id for space in spaces]
    recent_posts = []
    if space_ids:
        recent_posts = (DiscussionPost.query
                        .filter(DiscussionPost.space_id.in_(space_ids),
                                DiscussionPost.is_hidden.is_(False))
                        .order_by(DiscussionPost.last_activity_at.desc())
                        .limit(15).all())
    announcements = Post.published_query().limit(3).all()
    member_count = Membership.query.filter_by(org_id=g.org.id,
                                              is_active=True).count()
    return render_template('orgs/dashboard.html', org=g.org, spaces=spaces,
                           recent_posts=recent_posts,
                           announcements=announcements,
                           member_count=member_count)
