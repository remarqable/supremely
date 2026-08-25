"""Public organization site: pages through the theme system, uploaded files,
theme assets."""

from flask import (Blueprint, abort, g, redirect, request, send_file,
                   send_from_directory, url_for)
from flask_login import current_user

from app.extensions import db
from app.models import Upload
from app.models.page import Page
from app.models.upload import VARIANTS
from app.platform.authz import is_org_member, org_required
from app.platform.theming import AVAILABLE_THEMES, render_site

bp = Blueprint('site', __name__)


def render_org_home():
    """The organization homepage: the designated Page, or the app default."""
    page = g.org.homepage()
    if page is not None and page.visible_to_current_visitor():
        return render_site(
            ['front-page.html', f'{page.template}.html', 'page.html'],
            org=g.org, page=page)
    return render_site(['front-page.html'], org=g.org, page=None)


@bp.route('/<slug>')
@org_required
def page(slug):
    page = Page.published_by_slug(slug)
    if page is None:
        abort(404)
    if not page.visible_to_current_visitor():
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login', next=request.path))
        abort(404)
    return render_site(
        [f'page-{page.slug}.html', f'{page.template}.html', 'page.html'],
        org=g.org, page=page)


@bp.route('/files/<int:upload_id>/<variant>')
def serve_upload(upload_id, variant):
    # On an org host the tenant filter already hides other orgs' rows.
    upload = db.session.get(Upload, upload_id)
    if upload is None:
        abort(404)
    if variant not in VARIANTS and variant != 'original':
        abort(404)
    if upload.visibility != 'public':
        if not (is_org_member() or (current_user.is_authenticated
                                    and current_user.is_platform_admin)):
            abort(404)

    from app.platform.storage import storage
    if variant != 'original' and not upload.has_variants:
        variant = 'original'            # non-raster files: serve as-is
    key = upload.variant_key(variant)
    if not storage().exists(key):
        abort(404)
    mimetype = 'image/webp' if key.endswith('.webp') else upload.content_type
    response = send_file(storage().open(key), mimetype=mimetype,
                         max_age=31536000, download_name=upload.filename)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@bp.route('/themes/<theme>/static/<path:filename>')
def theme_static(theme, filename):
    info = AVAILABLE_THEMES.get(theme)      # whitelist, never trust the URL
    if info is None or info['path'] is None:
        abort(404)
    return send_from_directory(info['path'] / 'static', filename,
                               max_age=31536000)
