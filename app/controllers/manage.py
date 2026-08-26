"""Organization management: pages, navigation, media, settings, theme.

Runs on the org host under /manage; every query is tenant-scoped
automatically."""

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

from app.extensions import db
from app.models import Content, Upload
from app.models.content import Category
from app.models.navigation import MENUS, NavigationItem
from app.platform import theme_content as tc
from app.platform.authz import org_required, require
from app.platform.content_types import CONTENT_TYPES, active_types, get_content_type
from app.platform.errors import ValidationError
from app.platform.i18n import t
from app.platform.logger import get_logger
from app.platform.theming import AVAILABLE_THEMES, current_theme

bp = Blueprint('manage', __name__, url_prefix='/manage')
log = get_logger()


@bp.route('/')
@org_required
@require('content.write')
def index():
    return redirect(url_for('manage.content_list', type_slug='page'))


# --- Content (all types: page, article, event, plugin types) -------------------

def _content_or_404(content_id) -> Content:
    content = db.session.get(Content, content_id)
    if content is None:
        abort(404)
    return content


@bp.route('/content/<type_slug>')
@org_required
@require('content.write')
def content_list(type_slug):
    if type_slug not in active_types():
        abort(404)
    ct = get_content_type(type_slug)
    items = (Content.of_type(type_slug)
             .order_by(Content.created_at.desc()).all())
    return render_template('manage/content_list.html', items=items,
                           content_type=ct)


def _content_from_form(content):
    content.title = request.form.get('title', '')
    content.slug = request.form.get('slug', '')
    content.body = request.form.get('body', '')
    content.excerpt = request.form.get('excerpt', '').strip() or None
    content.visibility = request.form.get('visibility', 'public')
    content.seo_title = request.form.get('seo_title', '').strip() or None
    content.seo_description = request.form.get('seo_description', '').strip() or None
    if content.content_type.is_page:
        content.template = (request.form.get('template', '').strip() or None)
    raw = request.form.get('featured_upload_id', '')
    content.featured_upload_id = int(raw) if raw.isdigit() and int(raw) else None
    content.tags = [tag.strip() for tag in
                    request.form.get('tags', '').split(',') if tag.strip()]
    category_ids = request.form.getlist('category_ids', type=int)
    content.categories = (Category.query.filter(Category.id.in_(category_ids)).all()
                          if category_ids else [])
    content.set_structured_fields({
        key[len('field_'):]: value for key, value in request.form.items()
        if key.startswith('field_')})
    return content


def _render_content_form(content, ct):
    return render_template('manage/content_form.html', content=content,
                           content_type=ct,
                           categories=Category.query.order_by(Category.name).all())


@bp.route('/content/<type_slug>/new', methods=['GET', 'POST'])
@org_required
@require('content.write')
def new_content(type_slug):
    if type_slug not in active_types():
        abort(404)
    ct = get_content_type(type_slug)
    if request.method == 'POST':
        content = Content(type=ct.slug)
        try:
            _content_from_form(content)
            content.stamp_audit()
            if request.form.get('action') == 'publish':
                content.validate()
                db.session.add(content)
                db.session.commit()
                content.publish()
            else:
                content.save()
            flash(t('common.saved'), 'success')
            return redirect(url_for('manage.edit_content', content_id=content.id))
        except ValidationError as e:
            db.session.rollback()
            flash(e.message, 'error')
            return _render_content_form(content, ct)
    return _render_content_form(None, ct)


@bp.route('/content/<int:content_id>/edit', methods=['GET', 'POST'])
@org_required
@require('content.write')
def edit_content(content_id):
    content = _content_or_404(content_id)
    ct = content.content_type
    if request.method == 'POST':
        try:
            _content_from_form(content)
            content.stamp_audit()
            action = request.form.get('action', 'save')
            if action == 'publish':
                content.publish()
            elif action == 'unpublish':
                content.unpublish()
            elif action == 'archive':
                content.archive()
            else:
                content.save()
            flash(t('common.saved'), 'success')
            return redirect(url_for('manage.edit_content', content_id=content.id))
        except ValidationError as e:
            db.session.rollback()
            flash(e.message, 'error')
    return _render_content_form(content, ct)


@bp.route('/content/<int:content_id>/delete', methods=['POST'])
@org_required
@require('content.write')
def delete_content(content_id):
    content = _content_or_404(content_id)
    type_slug = content.type
    content.delete()
    flash(t('manage.content_deleted'), 'success')
    return redirect(url_for('manage.content_list', type_slug=type_slug))


@bp.route('/content/<int:content_id>/preview')
@org_required
@require('content.write')
def preview_content(content_id):
    from app.platform.theming import render_site
    content = _content_or_404(content_id)
    ct = content.content_type
    if ct.is_page:
        tmpl = content.template or ct.template
        names = [f'{tmpl}.html', 'page.html']
    else:
        names = [f'{ct.template}.html', 'single.html']
    return render_site(names, content=content, content_type=ct, post=content,
                       page=content, preview=True)


@bp.route('/content-types')
@org_required
@require('content.write')
def content_types_page():
    """The content-type library: what this organization can publish today,
    and the premade types that are on the way."""
    from app.platform.content_library import COMING_SOON
    counts = dict(Content.count_by_type())
    return render_template('manage/content_types.html',
                           types=CONTENT_TYPES.values(), counts=counts,
                           coming_soon=COMING_SOON)


@bp.route('/categories', methods=['GET', 'POST'])
@org_required
@require('content.write')
def categories():
    if request.method == 'POST':
        category = Category(name=request.form.get('name', ''),
                            slug=request.form.get('slug', ''))
        try:
            category.save()
            flash(t('common.saved'), 'success')
        except ValidationError as e:
            flash(e.message, 'error')
        return redirect(url_for('manage.categories'))
    category_list = Category.query.order_by(Category.name).all()
    return render_template('manage/categories.html', categories=category_list)


@bp.route('/categories/<int:category_id>/delete', methods=['POST'])
@org_required
@require('content.write')
def delete_category(category_id):
    category = db.session.get(Category, category_id)
    if category is None:
        abort(404)
    category.delete()
    return redirect(url_for('manage.categories'))


# --- Navigation ----------------------------------------------------------------

@bp.route('/navigation', methods=['GET', 'POST'])
@org_required
@require('content.write')
def navigation():
    if request.method == 'POST':
        parent_id = request.form.get('parent_id', type=int) or None
        item = NavigationItem(
            menu=request.form.get('menu', 'primary'),
            label=request.form.get('label', ''),
            url=request.form.get('url', '').strip() or None,
            content_id=request.form.get('content_id', type=int) or None,
            parent_id=parent_id,
        )
        item.position = NavigationItem.next_position(item.menu, parent_id)
        try:
            item.save()
            flash(t('common.saved'), 'success')
        except ValidationError as e:
            db.session.rollback()
            flash(e.message, 'error')
        return redirect(url_for('manage.navigation'))

    menus = {menu: NavigationItem.items_for(menu) for menu in MENUS}
    # Any published content can be a nav target (pages most commonly).
    linkable = (Content.published_query()
                .order_by(Content.type, Content.title).all())
    return render_template('manage/navigation.html', menus=menus,
                           linkable=linkable)


@bp.route('/navigation/<int:item_id>/move', methods=['POST'])
@org_required
@require('content.write')
def move_navigation(item_id):
    item = db.session.get(NavigationItem, item_id)
    if item is None:
        abort(404)
    item.move(-1 if request.form.get('direction') == 'up' else 1)
    return redirect(url_for('manage.navigation'))


@bp.route('/navigation/<int:item_id>/delete', methods=['POST'])
@org_required
@require('content.write')
def delete_navigation(item_id):
    item = db.session.get(NavigationItem, item_id)
    if item is None:
        abort(404)
    item.delete()
    return redirect(url_for('manage.navigation'))


# --- Members & invitations -------------------------------------------------------

@bp.route('/members')
@org_required
@require('members.manage')
def members():
    from app.models import Membership, User
    from app.models.invitation import Invitation
    member_list = (Membership.query.filter_by(org_id=g.org.id)
                   .join(Membership.user).order_by(User.name).all())
    invitations = (Invitation.query
                   .order_by(Invitation.created_at.desc()).limit(20).all())
    from app.platform.mailer import is_email_configured
    return render_template('manage/members.html', members=member_list,
                           invitations=invitations,
                           email_configured=is_email_configured())


@bp.route('/members/add', methods=['POST'])
@org_required
@require('members.manage')
def add_member():
    from app.models import Membership, User
    email = request.form.get('email', '').strip().lower()
    role = request.form.get('role', 'member')
    user = User.get_by_email(email)
    if user is None:
        flash(t('members.user_not_found', email=email), 'error')
    else:
        try:
            Membership.add(user.id, g.org.id, role=role)
            flash(t('admin.member_added', email=email), 'success')
        except ValidationError as e:
            flash(e.message, 'error')
    return redirect(url_for('manage.members'))


def _own_membership(membership_id):
    from app.models import Membership
    membership = db.session.get(Membership, membership_id)
    if membership is None or membership.org_id != g.org.id:
        abort(404)
    return membership


@bp.route('/members/<int:membership_id>/role', methods=['POST'])
@org_required
@require('members.manage')
def member_role(membership_id):
    membership = _own_membership(membership_id)
    try:
        membership.change_role(request.form.get('role', 'member'))
        flash(t('common.saved'), 'success')
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
    return redirect(url_for('manage.members'))


@bp.route('/members/<int:membership_id>/suspend', methods=['POST'])
@org_required
@require('members.manage')
def member_suspend(membership_id):
    membership = _own_membership(membership_id)
    try:
        membership.suspend()
        flash(t('members.suspended'), 'success')
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
    return redirect(url_for('manage.members'))


@bp.route('/members/<int:membership_id>/unsuspend', methods=['POST'])
@org_required
@require('members.manage')
def member_unsuspend(membership_id):
    _own_membership(membership_id).unsuspend()
    flash(t('common.saved'), 'success')
    return redirect(url_for('manage.members'))


@bp.route('/members/<int:membership_id>/remove', methods=['POST'])
@org_required
@require('members.manage')
def member_remove(membership_id):
    membership = _own_membership(membership_id)
    try:
        membership.remove()
        flash(t('admin.member_removed'), 'success')
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
    return redirect(url_for('manage.members'))


@bp.route('/members/<int:membership_id>/transfer', methods=['POST'])
@org_required
@require('ownership.transfer')
def member_transfer(membership_id):
    target = _own_membership(membership_id)
    try:
        g.membership.transfer_ownership_to(target)
        log.info('ownership_transferred', org_id=g.org.id,
                 to_user_id=target.user_id)
        flash(t('members.ownership_transferred', name=target.user.name), 'success')
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
    return redirect(url_for('manage.members'))


@bp.route('/invitations', methods=['POST'])
@org_required
@require('members.manage')
def create_invitation():
    from app.models.invitation import Invitation
    from app.platform.mailer import try_send_email
    role = request.form.get('role', 'member')
    email = request.form.get('email', '').strip().lower() or None
    try:
        invitation, token = Invitation.create(g.org.id, role=role, email=email)
    except ValidationError as e:
        flash(e.message, 'error')
        return redirect(url_for('manage.members'))

    invite_url = invitation.url(token)
    if email and try_send_email(
            email, t('members.invite_email_subject', org=g.org.name),
            t('members.invite_email_body', org=g.org.name, url=invite_url)):
        flash(t('members.invite_sent', email=email), 'success')
    # The URL is shown once: only its hash is stored.
    flash(t('members.invite_link', url=invite_url), 'invite')
    return redirect(url_for('manage.members'))


@bp.route('/invitations/<int:invitation_id>/revoke', methods=['POST'])
@org_required
@require('members.manage')
def revoke_invitation(invitation_id):
    from app.models.invitation import Invitation
    invitation = db.session.get(Invitation, invitation_id)
    if invitation is None:
        abort(404)
    invitation.delete()
    flash(t('members.invite_revoked'), 'success')
    return redirect(url_for('manage.members'))


@bp.route('/directory', methods=['POST'])
@org_required
@require('org.settings')
def toggle_directory():
    enabled = request.form.get('enabled') == 'on'
    g.org.update_settings(member_directory=enabled)
    flash(t('common.saved'), 'success')
    return redirect(url_for('manage.members'))


# --- Media ---------------------------------------------------------------------

@bp.route('/media', methods=['GET', 'POST'])
@org_required
@require('content.write')
def media():
    if request.method == 'POST':
        file = request.files.get('file')
        if file is None or not file.filename:
            flash(t('manage.no_file'), 'error')
        else:
            try:
                Upload.from_file(file, visibility='public')
                flash(t('common.saved'), 'success')
            except ValidationError as e:
                flash(e.message, 'error')
        return redirect(url_for('manage.media'))

    uploads = Upload.query.order_by(Upload.created_at.desc()).all()
    return render_template('manage/media.html', uploads=uploads)


@bp.route('/media/<int:upload_id>/delete', methods=['POST'])
@org_required
@require('content.write')
def delete_media(upload_id):
    upload = db.session.get(Upload, upload_id)
    if upload is None:
        abort(404)
    settings = g.org.settings or {}
    updates = {key: None for key in ('logo_upload_id', 'favicon_upload_id')
               if settings.get(key) == upload.id}
    if updates:
        g.org.update_settings(**updates)
    upload.delete()
    flash(t('manage.media_deleted'), 'success')
    return redirect(url_for('manage.media'))


# --- Custom domains ---------------------------------------------------------------------

@bp.route('/domains', methods=['GET', 'POST'])
@org_required
@require('org.settings')
def domains():
    from app.models.domain import OrgDomain
    if request.method == 'POST':
        domain = OrgDomain(org_id=g.org.id,
                           domain=request.form.get('domain', ''))
        try:
            domain.save()
            flash(t('domains.added'), 'success')
        except ValidationError as e:
            db.session.rollback()
            flash(e.message, 'error')
        return redirect(url_for('manage.domains'))
    domain_list = OrgDomain.query.filter_by(org_id=g.org.id) \
        .order_by(OrgDomain.created_at).all()
    return render_template('manage/domains.html', domains=domain_list)


@bp.route('/domains/<int:domain_id>/delete', methods=['POST'])
@org_required
@require('org.settings')
def delete_domain(domain_id):
    from app.models.domain import OrgDomain
    domain = db.session.get(OrgDomain, domain_id)
    if domain is None or domain.org_id != g.org.id:
        abort(404)
    domain.delete()
    flash(t('domains.removed'), 'success')
    return redirect(url_for('manage.domains'))


# --- Plugins --------------------------------------------------------------------------

@bp.route('/plugins')
@org_required
@require('plugins.manage')
def plugins():
    from app.models.org_plugin import OrgPlugin
    from app.platform.plugins import MANIFESTS
    rows = {row.plugin_slug: row
            for row in OrgPlugin.query.filter_by(org_id=g.org.id).all()}
    return render_template('manage/plugins.html', manifests=MANIFESTS,
                           rows=rows)


@bp.route('/plugins/<slug>/install', methods=['POST'])
@org_required
@require('plugins.manage')
def install_plugin(slug):
    from app.platform.errors import NotFoundError
    from app.platform.plugins import install
    try:
        install(g.org.id, slug)
        flash(t('plugins.installed'), 'success')
    except (ValidationError, NotFoundError) as e:
        flash(e.message, 'error')
    return redirect(url_for('manage.plugins'))


@bp.route('/plugins/<slug>/uninstall', methods=['POST'])
@org_required
@require('plugins.manage')
def uninstall_plugin(slug):
    from app.platform.plugins import uninstall
    try:
        uninstall(g.org.id, slug)
        flash(t('plugins.disabled'), 'success')
    except ValidationError as e:
        flash(e.message, 'error')
    return redirect(url_for('manage.plugins'))


@bp.route('/plugins/<slug>/upgrade', methods=['POST'])
@org_required
@require('plugins.manage')
def upgrade_plugin(slug):
    from app.platform.errors import NotFoundError
    from app.platform.plugins import upgrade
    try:
        upgrade(g.org.id, slug, request.form.get('version', ''))
        flash(t('plugins.upgraded'), 'success')
    except (ValidationError, NotFoundError) as e:
        flash(e.message, 'error')
    return redirect(url_for('manage.plugins'))


@bp.route('/plugins/<slug>/settings', methods=['POST'])
@org_required
@require('plugins.manage')
def plugin_settings_save(slug):
    from app.models.org_plugin import OrgPlugin
    from app.platform.plugins import MANIFESTS
    row = OrgPlugin.query.filter_by(org_id=g.org.id, plugin_slug=slug).first()
    if row is None or slug not in MANIFESTS:
        abort(404)
    schema = MANIFESTS[slug].get('settings', {})
    row.settings = {key: request.form.get(f'setting_{key}', '')
                    for key in schema}
    db.session.commit()
    flash(t('common.saved'), 'success')
    return redirect(url_for('manage.plugins'))


# --- Newsletter ---------------------------------------------------------------------

@bp.route('/newsletter')
@org_required
@require('content.write')
def newsletter():
    from app.models.newsletter import Delivery, Subscriber
    from app.platform.mailer import is_email_configured
    subscribers = Subscriber.query.order_by(Subscriber.created_at.desc()).all()
    deliveries = (Delivery.query.order_by(Delivery.created_at.desc())
                  .limit(20).all())
    stats = {
        'subscribed': Subscriber.audience().count(),
        'pending': Subscriber.query.filter_by(status='pending').count(),
        'unsubscribed': Subscriber.query.filter_by(status='unsubscribed').count(),
    }
    return render_template('manage/newsletter.html', subscribers=subscribers,
                           deliveries=deliveries, stats=stats,
                           email_configured=is_email_configured())


@bp.route('/newsletter/subscribers', methods=['POST'])
@org_required
@require('content.write')
def add_subscriber():
    from app.models.newsletter import Subscriber
    try:
        Subscriber.subscribe(request.form.get('email', ''), g.org.id,
                             require_confirmation=False)
        flash(t('common.saved'), 'success')
    except ValidationError as e:
        flash(e.message, 'error')
    return redirect(url_for('manage.newsletter'))


@bp.route('/newsletter/subscribers/<int:subscriber_id>/remove', methods=['POST'])
@org_required
@require('content.write')
def remove_subscriber(subscriber_id):
    from app.models.newsletter import Subscriber
    subscriber = db.session.get(Subscriber, subscriber_id)
    if subscriber is None:
        abort(404)
    subscriber.delete()
    flash(t('newsletter.subscriber_removed'), 'success')
    return redirect(url_for('manage.newsletter'))


@bp.route('/content/<int:content_id>/send-newsletter', methods=['POST'])
@org_required
@require('content.write')
def send_post_newsletter(content_id):
    from app.models.newsletter import Delivery, Subscriber
    from app.platform.jobs import enqueue
    from app.platform.mailer import is_email_configured

    post = db.session.get(Content, content_id)
    if post is None:
        abort(404)
    if not is_email_configured():
        flash(t('newsletter.email_required_to_send'), 'error')
        return redirect(url_for('manage.edit_content', content_id=post.id))
    if Subscriber.audience().count() == 0:
        flash(t('newsletter.no_subscribers'), 'error')
        return redirect(url_for('manage.edit_content', content_id=post.id))

    delivery = Delivery.create_for_post(post)
    enqueue('newsletter.send_delivery', org_id=g.org.id,
            delivery_id=delivery.id)
    log.info('newsletter_queued', delivery_id=delivery.id,
             recipients=delivery.recipients_total)
    flash(t('newsletter.queued', n=delivery.recipients_total), 'success')
    return redirect(url_for('manage.newsletter'))


# --- Discussion groups & moderation queue ------------------------------------------

@bp.route('/discussions', methods=['GET', 'POST'])
@org_required
@require('content.moderate')
def discussions():
    from app.models.discussion import DiscussionGroup
    if request.method == 'POST':
        group = DiscussionGroup(name=request.form.get('name', ''),
                      slug=request.form.get('slug', ''),
                      description=request.form.get('description', '').strip() or None,
                      visibility=request.form.get('visibility', 'members'))
        group.position = DiscussionGroup.query.count() + 1
        try:
            group.save()
            flash(t('common.saved'), 'success')
        except ValidationError as e:
            flash(e.message, 'error')
        return redirect(url_for('manage.discussions'))
    groups = DiscussionGroup.query.order_by(DiscussionGroup.position, DiscussionGroup.name).all()
    return render_template('manage/discussions.html', groups=groups)


@bp.route('/discussions/<int:group_id>/delete', methods=['POST'])
@org_required
@require('content.moderate')
def delete_group(group_id):
    from app.models.discussion import DiscussionGroup
    group = db.session.get(DiscussionGroup, group_id)
    if group is None:
        abort(404)
    group.delete()
    flash(t('manage.group_deleted'), 'success')
    return redirect(url_for('manage.discussions'))


@bp.route('/flags')
@org_required
@require('content.moderate')
def flags():
    from app.models.discussion import Flag
    open_flags = (Flag.query.filter_by(resolved_at=None)
                  .order_by(Flag.created_at.desc()).all())
    return render_template('manage/flags.html', flags=open_flags)


@bp.route('/flags/<int:flag_id>/resolve', methods=['POST'])
@org_required
@require('content.moderate')
def resolve_flag(flag_id):
    from app.models.discussion import Flag
    flag = db.session.get(Flag, flag_id)
    if flag is None:
        abort(404)
    flag.resolve()
    flash(t('common.saved'), 'success')
    return redirect(url_for('manage.flags'))


# --- Settings / branding / theme -------------------------------------------------

@bp.route('/settings', methods=['GET', 'POST'])
@org_required
@require('org.settings')
def settings():
    org = g.org
    if request.method == 'POST':
        section = request.form.get('section', 'branding')
        try:
            if section == 'branding':
                org.name = request.form.get('name', org.name)
                org.description = request.form.get('description', '').strip() or None
                org.brand_primary = request.form.get('brand_primary', '').strip() or None
                org.save()
                updates = {}
                for field in ('logo_upload_id', 'favicon_upload_id'):
                    raw = request.form.get(field, '')
                    updates[field] = int(raw) if raw.isdigit() and int(raw) else None
                org.update_settings(**updates)
            elif section == 'theme':
                from app.platform.theming import clean_theme_config
                theme = request.form.get('theme', 'origin')
                if theme not in AVAILABLE_THEMES:
                    raise ValidationError('Unknown theme')
                # Validate settings BEFORE persisting the theme choice; the
                # values are interpolated into a <style> block. See
                # theming.clean_theme_config.
                config = clean_theme_config(theme, {
                    key: request.form.get(f'theme_{key}', '')
                    for key in AVAILABLE_THEMES[theme].get('settings', {})})
                org.theme = theme
                org.save()
                org.update_settings(theme_config=config)
            flash(t('common.saved'), 'success')
        except ValidationError as e:
            db.session.rollback()
            flash(e.message, 'error')
        return redirect(url_for('manage.settings'))

    uploads = Upload.query.filter(Upload.content_type.like('image/%')) \
        .order_by(Upload.created_at.desc()).all()
    return render_template('manage/settings.html', org=org, uploads=uploads,
                           themes=AVAILABLE_THEMES)


# --- Landing page (theme-declared editable content) ---------------------------

@bp.route('/landing', methods=['GET', 'POST'])
@org_required
@require('content.write')
def landing_settings():
    """Edit the active theme's declared content (theme.json "content"). The
    design ships in the theme; only the words are per-org, stored under
    settings['theme_content'][<theme>] so each theme keeps its own copy. Text
    renders into autoescaped HTML — the schema's length caps are the only
    write-time guard (see app.platform.theme_content)."""
    theme = current_theme()
    if not tc.has_editor(theme):
        abort(404)

    if request.method == 'POST':
        store = dict(g.org.setting('theme_content') or {})
        store[theme] = tc.clean(theme, request.form)
        g.org.update_settings(theme_content=store)
        flash(t('common.saved'), 'success')
        return redirect(url_for('manage.landing_settings'))

    return render_template('manage/landing.html',
                           fields=tc.editor_view(theme, g.org),
                           theme_name=AVAILABLE_THEMES[theme]['name'])
