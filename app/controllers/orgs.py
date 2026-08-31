"""Organization launcher, creation, and member-facing org pages."""

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.models import InstallationSetting, Organization
from app.platform.authz import is_member_or_platform_admin, org_required
from app.platform.errors import ValidationError
from app.platform.i18n import t
from app.platform.logger import get_logger
from app.platform.redirects import safe_next
from app.platform.tenant import org_url

bp = Blueprint('orgs', __name__)
log = get_logger()


def signups_enabled() -> bool:
    return InstallationSetting.organization_signups_enabled()


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


@bp.route('/manage-mode', methods=['POST'])
@org_required
@login_required
def toggle_manage_mode():
    """Flip manage mode: a PRESENTATION state that surfaces the management
    controls the user is already authorized for. It grants nothing — every
    control stays individually permission-gated, and the backend enforces
    each action regardless of this flag."""
    from flask import session

    from app.platform.authz import can
    if not (can('content.write') or can('content.moderate')):
        abort(403)
    session['manage_mode'] = not session.get('manage_mode')
    return redirect(safe_next(request.form.get('next'),
                              url_for('orgs.dashboard')))


FEED_LIMIT = 20


def _recent_posts(groups):
    """The community pulse: recent forum posts. Published content lives in
    Home's org column, not this feed."""
    from app.models.discussion import Post
    if not groups:
        return []
    return (Post.query
            .filter(Post.group_id.in_([group.id for group in groups]),
                    Post.is_hidden.is_(False))
            .order_by(Post.last_activity_at.desc())
            .limit(FEED_LIMIT).all())


def _latest_published():
    """The org's published voice for Home's org column. Announcements are
    excluded — the rail's announcement card already features the latest."""
    from app.models import Content
    from app.platform.content_types import feed_types
    type_slugs = [ct.slug for ct in feed_types() if ct.slug != 'announcement']
    if not type_slugs:
        return []
    return (Content.published_query()
            .filter(Content.type.in_(type_slugs)).limit(6).all())


@bp.route('/dashboard')
@org_required
@login_required
def dashboard():
    """Community home: where members land inside the organization. The right
    rail (announcement, members, event) rides the shell layout and feeds
    itself through template helpers."""
    if not is_member_or_platform_admin():
        abort(404)
    from app.models.discussion import DiscussionGroup

    groups = [group for group in
              DiscussionGroup.in_order()
              if group.readable_by_current_visitor()]

    return render_template('orgs/dashboard.html', org=g.org, groups=groups,
                           feed=_recent_posts(groups),
                           latest_published=_latest_published())
