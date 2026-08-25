"""Organization management: pages, navigation, media, settings, theme.

Runs on the org host under /manage; every query is tenant-scoped
automatically."""

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)
from flask_login import current_user

from app.extensions import db
from app.models import Organization, Upload
from app.models.navigation import MENUS, NavigationItem
from app.models.page import Page
from app.platform.authz import org_required, require
from app.platform.errors import ValidationError
from app.platform.i18n import t
from app.platform.logger import get_logger
from app.platform.theming import AVAILABLE_THEMES

bp = Blueprint('manage', __name__, url_prefix='/manage')
log = get_logger()


@bp.route('/')
@org_required
@require('content.write')
def index():
    return redirect(url_for('manage.pages'))


# --- Pages -------------------------------------------------------------------

@bp.route('/pages')
@org_required
@require('content.write')
def pages():
    page_list = Page.query.order_by(Page.created_at.desc()).all()
    homepage_id = g.org.setting('homepage_page_id')
    return render_template('manage/pages.html', pages=page_list,
                           homepage_id=homepage_id)


def _page_from_form(page: Page) -> Page:
    page.title = request.form.get('title', '')
    page.slug = request.form.get('slug', '')
    page.body = request.form.get('body', '')
    page.visibility = request.form.get('visibility', 'public')
    page.template = (request.form.get('template', 'page').strip() or 'page')[:50]
    page.seo_title = request.form.get('seo_title', '').strip() or None
    page.seo_description = request.form.get('seo_description', '').strip() or None
    return page


@bp.route('/pages/new', methods=['GET', 'POST'])
@org_required
@require('content.write')
def new_page():
    if request.method == 'POST':
        page = _page_from_form(Page())
        page.stamp_audit()
        try:
            if request.form.get('action') == 'publish':
                page.validate()
                db.session.add(page)
                db.session.commit()
                page.publish()
            else:
                page.save()
            flash(t('common.saved'), 'success')
            return redirect(url_for('manage.edit_page', page_id=page.id))
        except ValidationError as e:
            db.session.rollback()
            flash(e.message, 'error')
            return render_template('manage/page_form.html', page=page)
    return render_template('manage/page_form.html', page=None)


@bp.route('/pages/<int:page_id>/edit', methods=['GET', 'POST'])
@org_required
@require('content.write')
def edit_page(page_id):
    page = db.session.get(Page, page_id)
    if page is None:
        abort(404)
    if request.method == 'POST':
        _page_from_form(page)
        page.stamp_audit()
        action = request.form.get('action', 'save')
        try:
            if action == 'publish':
                page.publish()
            elif action == 'unpublish':
                page.unpublish()
            else:
                page.save()
            flash(t('common.saved'), 'success')
            return redirect(url_for('manage.edit_page', page_id=page.id))
        except ValidationError as e:
            db.session.rollback()
            flash(e.message, 'error')
    return render_template('manage/page_form.html', page=page)


@bp.route('/pages/<int:page_id>/delete', methods=['POST'])
@org_required
@require('content.write')
def delete_page(page_id):
    page = db.session.get(Page, page_id)
    if page is None:
        abort(404)
    if g.org.setting('homepage_page_id') == page.id:
        g.org.update_settings(homepage_page_id=None)
    page.delete()
    flash(t('manage.page_deleted'), 'success')
    return redirect(url_for('manage.pages'))


@bp.route('/pages/<int:page_id>/homepage', methods=['POST'])
@org_required
@require('content.write')
def set_homepage(page_id):
    page = db.session.get(Page, page_id)
    if page is None:
        abort(404)
    current = g.org.setting('homepage_page_id')
    g.org.update_settings(homepage_page_id=None if current == page.id else page.id)
    flash(t('common.saved'), 'success')
    return redirect(url_for('manage.pages'))


# --- Posts ---------------------------------------------------------------------

@bp.route('/posts')
@org_required
@require('content.write')
def posts():
    from app.models.post import Post
    post_list = Post.query.order_by(Post.created_at.desc()).all()
    return render_template('manage/posts.html', posts=post_list)


def _post_from_form(post):
    from app.models.post import Category
    post.title = request.form.get('title', '')
    post.slug = request.form.get('slug', '')
    post.body = request.form.get('body', '')
    post.excerpt = request.form.get('excerpt', '').strip() or None
    post.visibility = request.form.get('visibility', 'public')
    post.seo_title = request.form.get('seo_title', '').strip() or None
    post.seo_description = request.form.get('seo_description', '').strip() or None
    raw = request.form.get('featured_upload_id', '')
    post.featured_upload_id = int(raw) if raw.isdigit() and int(raw) else None
    post.tags = [tag.strip() for tag in
                 request.form.get('tags', '').split(',') if tag.strip()]
    category_ids = request.form.getlist('category_ids', type=int)
    post.categories = (Category.query.filter(Category.id.in_(category_ids)).all()
                       if category_ids else [])
    post.set_structured_fields({
        key[len('field_'):]: value for key, value in request.form.items()
        if key.startswith('field_')
    })
    return post


@bp.route('/posts/new', methods=['GET', 'POST'])
@org_required
@require('content.write')
def new_post():
    from app.models.post import Category, Post
    from app.platform.post_types import POST_TYPES, get_post_type
    type_slug = request.values.get('type', 'article')
    post_type = get_post_type(type_slug)

    if request.method == 'POST':
        post = Post(type=post_type.slug)
        try:
            _post_from_form(post)
            post.stamp_audit()
            if request.form.get('action') == 'publish':
                post.validate()
                db.session.add(post)
                db.session.commit()
                post.publish()
            else:
                post.save()
            flash(t('common.saved'), 'success')
            return redirect(url_for('manage.edit_post', post_id=post.id))
        except ValidationError as e:
            db.session.rollback()
            flash(e.message, 'error')
            return render_template('manage/post_form.html', post=post,
                                   post_type=post_type,
                                   post_types=POST_TYPES,
                                   categories=Category.query.order_by(Category.name).all())
    return render_template('manage/post_form.html', post=None,
                           post_type=post_type, post_types=POST_TYPES,
                           categories=Category.query.order_by(Category.name).all())


@bp.route('/posts/<int:post_id>/edit', methods=['GET', 'POST'])
@org_required
@require('content.write')
def edit_post(post_id):
    from app.models.post import Category, Post
    from app.platform.post_types import POST_TYPES
    post = db.session.get(Post, post_id)
    if post is None:
        abort(404)
    if request.method == 'POST':
        try:
            _post_from_form(post)
            post.stamp_audit()
            action = request.form.get('action', 'save')
            if action == 'publish':
                post.publish()
            elif action == 'unpublish':
                post.unpublish()
            elif action == 'archive':
                post.archive()
            else:
                post.save()
            flash(t('common.saved'), 'success')
            return redirect(url_for('manage.edit_post', post_id=post.id))
        except ValidationError as e:
            db.session.rollback()
            flash(e.message, 'error')
    return render_template('manage/post_form.html', post=post,
                           post_type=post.post_type, post_types=POST_TYPES,
                           categories=Category.query.order_by(Category.name).all())


@bp.route('/posts/<int:post_id>/delete', methods=['POST'])
@org_required
@require('content.write')
def delete_post(post_id):
    from app.models.post import Post
    post = db.session.get(Post, post_id)
    if post is None:
        abort(404)
    post.delete()
    flash(t('manage.post_deleted'), 'success')
    return redirect(url_for('manage.posts'))


@bp.route('/posts/<int:post_id>/preview')
@org_required
@require('content.write')
def preview_post(post_id):
    from app.models.post import Post
    from app.platform.theming import render_site
    post = db.session.get(Post, post_id)
    if post is None:
        abort(404)
    post_type = post.post_type
    return render_site(
        [f'site/{post_type.template}.html', 'site/post.html'],
        post=post, post_type=post_type, preview=True)


@bp.route('/categories', methods=['GET', 'POST'])
@org_required
@require('content.write')
def categories():
    from app.models.post import Category
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
    from app.models.post import Category
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
        item = NavigationItem(
            menu=request.form.get('menu', 'primary'),
            label=request.form.get('label', ''),
            url=request.form.get('url', '').strip() or None,
            page_id=request.form.get('page_id', type=int) or None,
        )
        item.position = NavigationItem.next_position(item.menu)
        try:
            item.save()
            flash(t('common.saved'), 'success')
        except ValidationError as e:
            flash(e.message, 'error')
        return redirect(url_for('manage.navigation'))

    menus = {menu: NavigationItem.items_for(menu) for menu in MENUS}
    published_pages = (Page.query.filter_by(status='published')
                       .order_by(Page.title).all())
    return render_template('manage/navigation.html', menus=menus,
                           pages=published_pages)


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
    from app.platform.plugins import install
    from app.platform.errors import NotFoundError
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
    from app.platform.plugins import upgrade
    from app.platform.errors import NotFoundError
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


@bp.route('/posts/<int:post_id>/send-newsletter', methods=['POST'])
@org_required
@require('content.write')
def send_post_newsletter(post_id):
    from app.models.post import Post
    from app.models.newsletter import Delivery, Subscriber
    from app.platform.mailer import is_email_configured
    from app.platform.jobs import enqueue

    post = db.session.get(Post, post_id)
    if post is None:
        abort(404)
    if not is_email_configured():
        flash(t('newsletter.email_required_to_send'), 'error')
        return redirect(url_for('manage.edit_post', post_id=post.id))
    if Subscriber.audience().count() == 0:
        flash(t('newsletter.no_subscribers'), 'error')
        return redirect(url_for('manage.edit_post', post_id=post.id))

    delivery = Delivery.create_for_post(post)
    enqueue('newsletter.send_delivery', org_id=g.org.id,
            delivery_id=delivery.id)
    log.info('newsletter_queued', delivery_id=delivery.id,
             recipients=delivery.recipients_total)
    flash(t('newsletter.queued', n=delivery.recipients_total), 'success')
    return redirect(url_for('manage.newsletter'))


# --- Discussion spaces & moderation queue ------------------------------------------

@bp.route('/discussions', methods=['GET', 'POST'])
@org_required
@require('content.moderate')
def discussions():
    from app.models.discussion import Space
    if request.method == 'POST':
        space = Space(name=request.form.get('name', ''),
                      slug=request.form.get('slug', ''),
                      description=request.form.get('description', '').strip() or None,
                      visibility=request.form.get('visibility', 'members'))
        space.position = Space.query.count() + 1
        try:
            space.save()
            flash(t('common.saved'), 'success')
        except ValidationError as e:
            flash(e.message, 'error')
        return redirect(url_for('manage.discussions'))
    spaces = Space.query.order_by(Space.position, Space.name).all()
    return render_template('manage/discussions.html', spaces=spaces)


@bp.route('/discussions/<int:space_id>/delete', methods=['POST'])
@org_required
@require('content.moderate')
def delete_space(space_id):
    from app.models.discussion import Space
    space = db.session.get(Space, space_id)
    if space is None:
        abort(404)
    space.delete()
    flash(t('manage.space_deleted'), 'success')
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
                theme = request.form.get('theme', 'default')
                if theme not in AVAILABLE_THEMES:
                    raise ValidationError('Unknown theme')
                org.theme = theme
                org.save()
                schema = AVAILABLE_THEMES[theme].get('settings', {})
                config = {key: request.form.get(f'theme_{key}', '')
                          for key in schema}
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
