"""Public organization site: unified Content routing (pages + feed types),
uploaded files, theme assets.

Routing model:
  /<slug>                       -> a published `page`-type Content
  /<base>                       -> archive of a feed type (e.g. /blog)
  /<base>/<slug>                -> a single feed-type Content
  /<base>/category/<cslug>      -> feed archive filtered by category
  /<base>/tag/<tag>             -> feed archive filtered by tag
A single-segment path is dispatched to a feed archive if it matches a type's
base, otherwise treated as a page slug.
"""

from flask import (
    Blueprint,
    abort,
    g,
    request,
    send_file,
    send_from_directory,
)

from app.extensions import db
from app.models import Content, Upload
from app.models.content import Category
from app.models.upload import VARIANTS
from app.platform.authz import is_member_or_platform_admin, org_required
from app.platform.content_types import type_for_base
from app.platform.theming import (
    AVAILABLE_THEMES,
    page_template_allowed,
    render_gate,
    render_site,
)

bp = Blueprint('site', __name__)

PER_PAGE = 10


def render_org_home():
    """The organization home page is the active theme's front page, whose copy
    is theme-declared content (Manage → Home page). One concept, every theme —
    there is no separate 'homepage' Page that can shadow it."""
    # The front page is the org's public landing: always themed, even for
    # members — the member home is /dashboard.
    return render_site(['front-page.html'], force_theme=True,
                       org=g.org, content=None, page=None)


# Gated content is teased by default: archives list members-only items as
# locked titles (templates render them via can_view, body and summary
# withheld) and a direct hit lands on the gate page. Only the title leaks —
# that is the demonstration of what membership unlocks. Orgs can turn
# teasing off (Manage → Settings → Privacy): Content.visible_query then
# drops gated items from listings, and render_gate degrades to a login
# redirect or a 404. That one query method is the rule for every listing --
# archives here and a theme's latest_content() alike -- so the two cannot
# drift apart.


def _render_page(content):
    ct = content.content_type
    if not Content.section_readable_by_current_visitor(ct.slug):
        # Section locked: gate the section, the way the archive does.
        return render_gate(ct.plural)
    if not content.visible_to_current_visitor():
        return render_gate(content.title, kind=ct.singular)
    # A row written before this rule existed can still hold anything.
    tmpl = (content.template
            if page_template_allowed(content.template) else None) or ct.template
    # The page declares its own presentation ('site' = themed public look,
    # 'community' = the shell); the object's data drives the one policy
    # point in render_site — never a membership test here.
    return render_site([f'page-{content.slug}.html', f'{tmpl}.html', 'page.html'],
                       force_theme=(content.presentation == 'site'),
                       org=g.org, content=content, page=content)


def _render_archive(ct, title=None):
    if not Content.section_readable_by_current_visitor(ct.slug):
        # The whole section is locked: one gate, no item titles teased.
        return render_gate(ct.plural)
    page_number = request.args.get('page', 1, type=int)
    pagination = Content.visible_query(ct.slug).paginate(
        page=page_number, per_page=PER_PAGE, error_out=False)
    return render_site([f'archive-{ct.slug}.html', ct.list_template + '.html',
                        'archive.html'],
                       force_theme=(ct.presentation == 'site'),
                       content_type=ct, items=pagination.items,
                       pagination=pagination,
                       archive_title=title or ct.plural)


def _render_single(ct, content):
    if not Content.section_readable_by_current_visitor(ct.slug):
        # Section locked: gate the section, the way the archive does.
        return render_gate(ct.plural)
    if not content.visible_to_current_visitor():
        return render_gate(content.title, kind=ct.singular)
    # Specificity order, and symmetric with archives: this item, then this
    # type, then the generic single. A theme shipping single-recipe.html has
    # it used for recipes without registering anything.
    return render_site(
        [f'single-{content.slug}.html', f'single-{ct.slug}.html',
         f'{ct.template}.html', 'single.html'],
        force_theme=(ct.presentation == 'site'),
        content_type=ct, content=content)


@bp.route('/<seg>')
@org_required
def entry(seg):
    ct = type_for_base('/' + seg)
    if ct is not None:                       # a feed type's archive, e.g. /blog
        return _render_archive(ct)
    page = Content.published_page(seg)       # otherwise a standalone page
    if page is None:
        abort(404)
    return _render_page(page)


@bp.route('/<seg>/category/<cslug>')
@org_required
def archive_category(seg, cslug):
    ct = type_for_base('/' + seg)
    if ct is None:
        abort(404)
    if not Content.section_readable_by_current_visitor(ct.slug):
        return render_gate(ct.plural)
    category = Category.get_by_slug(cslug)
    if category is None:
        abort(404)
    page_number = request.args.get('page', 1, type=int)
    pagination = (Content.visible_in_category(ct.slug, category)
                  .paginate(page=page_number, per_page=PER_PAGE,
                            error_out=False))
    return render_site([f'archive-{ct.slug}.html', ct.list_template + '.html',
                        'archive.html'],
                       force_theme=(ct.presentation == 'site'),
                       content_type=ct, items=pagination.items,
                       pagination=pagination, archive_title=category.name)


@bp.route('/<seg>/tag/<tag>')
@org_required
def archive_tag(seg, tag):
    ct = type_for_base('/' + seg)
    if ct is None:
        abort(404)
    if not Content.section_readable_by_current_visitor(ct.slug):
        return render_gate(ct.plural)
    page_number = request.args.get('page', 1, type=int)
    pagination = Content.with_tag(ct.slug, tag).paginate(
        page=page_number, per_page=PER_PAGE, error_out=False)
    return render_site([f'archive-{ct.slug}.html', ct.list_template + '.html',
                        'archive.html'],
                       force_theme=(ct.presentation == 'site'),
                       content_type=ct, items=pagination.items,
                       pagination=pagination, archive_title=f'#{tag}')


@bp.route('/<seg>/<slug>')
@org_required
def single(seg, slug):
    ct = type_for_base('/' + seg)
    if ct is None:
        abort(404)
    content = Content.published_by_slug(ct.slug, slug)
    if content is None:
        abort(404)
    return _render_single(ct, content)


# --- Files & theme assets ------------------------------------------------------

@bp.route('/files/<int:upload_id>/<variant>')
@org_required
def serve_upload(upload_id, variant):
    # @org_required ensures g.org is set, so the tenant filter scopes this
    # lookup. Without it, the bare installation host (g.org is None) would
    # serve ANY tenant's upload by id.
    upload = db.get_or_404(Upload, upload_id)
    if variant not in VARIANTS and variant != 'original':
        abort(404)
    if upload.visibility != 'public' and not is_member_or_platform_admin():
        abort(404)

    from app.platform.storage import storage
    if variant != 'original' and not upload.has_variants:
        variant = 'original'            # non-raster files: serve as-is
    key = upload.variant_key(variant)
    if not storage().exists(key):
        abort(404)
    mimetype = 'image/webp' if key.endswith('.webp') else upload.content_type
    public = upload.visibility == 'public'
    response = send_file(storage().open(key), mimetype=mimetype,
                         max_age=31536000 if public else 0,
                         download_name=upload.filename)
    if not public:
        # send_file's max_age also decides Cache-Control: public, which
        # would let any shared cache hand a members-only file to someone
        # who could never have fetched it themselves.
        response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@bp.route('/themes/<theme>/static/<path:filename>')
def theme_static(theme, filename):
    info = AVAILABLE_THEMES.get(theme)      # whitelist, never trust the URL
    if info is None or info['path'] is None:
        abort(404)
    return send_from_directory(info['path'] / 'static', filename,
                               max_age=31536000)
