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
    return redirect(request.form.get('next') or url_for('orgs.dashboard'))


FEED_TABS = ('all', 'posts', 'discussions', 'announcements')
FEED_LIMIT = 20
ANNOUNCEMENTS_CATEGORY = 'announcements'


def _feed_items(tab, spaces):
    """The community feed: one reverse-chronological stream of discussion
    posts and published content. Merged in Python — both sides are already
    small, indexed, tenant-scoped queries."""
    from app.models import Content
    from app.models.content import Category
    from app.models.discussion import DiscussionPost
    from app.platform.content_types import feed_types

    discussions = []
    if tab in ('all', 'discussions') and spaces:
        discussions = (DiscussionPost.query
                       .filter(DiscussionPost.space_id.in_(
                                   [space.id for space in spaces]),
                               DiscussionPost.is_hidden.is_(False))
                       .order_by(DiscussionPost.last_activity_at.desc())
                       .limit(FEED_LIMIT).all())

    content = []
    if tab in ('all', 'posts'):
        type_slugs = [ct.slug for ct in feed_types()]
        content = (Content.published_query()
                   .filter(Content.type.in_(type_slugs))
                   .limit(FEED_LIMIT).all())
    elif tab == 'announcements':
        category = Category.query.filter_by(
            slug=ANNOUNCEMENTS_CATEGORY).first()
        if category is not None:
            content = (Content.published_query('article')
                       .filter(Content.categories.contains(category))
                       .limit(FEED_LIMIT).all())

    items = ([('discussion', post.last_activity_at, post)
              for post in discussions]
             + [('content', item.published_at, item) for item in content])
    items.sort(key=lambda entry: entry[1], reverse=True)
    return items[:FEED_LIMIT]


def _upcoming_event():
    """The next published event dated today or later. Event dates live in the
    structured `fields` JSON, so the (few) events are filtered in Python."""
    from datetime import date

    from app.models import Content
    today = date.today().isoformat()
    events = [(event.fields.get('starts_on'), event)
              for event in Content.published_query('event').limit(50).all()
              if (event.fields or {}).get('starts_on', '') >= today]
    return min(events, default=(None, None))[1]


@bp.route('/dashboard')
@org_required
@login_required
def dashboard():
    """Community home: where members land inside the organization."""
    if g.membership is None and not current_user.is_platform_admin:
        abort(404)
    from app.models import User
    from app.models.discussion import Space

    tab = request.args.get('tab', 'all')
    if tab not in FEED_TABS:
        tab = 'all'

    spaces = [space for space in
              Space.query.order_by(Space.position, Space.name).all()
              if space.readable_by_current_visitor()]

    member_count = Membership.query.filter_by(org_id=g.org.id,
                                              is_active=True).count()
    recent_members = (User.query.join(Membership, Membership.user_id == User.id)
                      .filter(Membership.org_id == g.org.id,
                              Membership.is_active.is_(True))
                      .order_by(Membership.created_at.desc()).limit(5).all())

    return render_template('orgs/dashboard.html', org=g.org, spaces=spaces,
                           tab=tab, feed=_feed_items(tab, spaces),
                           member_count=member_count,
                           recent_members=recent_members,
                           upcoming_event=_upcoming_event())
