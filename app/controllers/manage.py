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
