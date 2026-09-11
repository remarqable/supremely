"""Organization management: pages, navigation, media, settings, theme.

Runs on the org host under /manage; every query is tenant-scoped
automatically."""

from typing import TYPE_CHECKING

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    request,
    url_for,
)
from flask.typing import ResponseReturnValue
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.middleware.ratelimit import rate_limit
from app.models import Content, Upload
from app.models.base import reject_control_characters
from app.models.content import Category
from app.models.navigation import MENUS, NavigationItem
from app.platform import theme_content as tc
from app.platform.authz import (
    VISIBILITY_LEVELS,
    can,
    grants_more_than,
    org_required,
    require,
    visibility_is_valid,
)
from app.platform.content_types import (
    CONTENT_TYPES,
    LIST_ROW_CAP,
    ContentType,
    active_types,
    get_content_type,
    nestable_types,
    offerable_sections,
    site_entry_types,
    submitted_fields,
)
from app.platform.devices import render_device_template
from app.platform.emails import invitation_message, password_reset_message
from app.platform.errors import ValidationError
from app.platform.i18n import t
from app.platform.logger import get_logger
from app.platform.notify import notify_password_reset_issued
from app.platform.theming import (
    AVAILABLE_THEMES,
    current_theme,
    page_template_allowed,
    page_template_exists,
)

if TYPE_CHECKING:
    from app.models import Tier

bp = Blueprint('manage', __name__, url_prefix='/manage')
log = get_logger()


@bp.route('/')
@org_required
@require('content.write')
def index():
    return redirect(url_for('manage.content_list', type_slug='page'))


# --- Content (all types: page, article, event, plugin types) -------------------

def _own_upload_id(field: str, public_only: bool = False) -> int | None:
    """The id of the upload named in `field`, or None.

    Resolved through a query rather than trusted, so an id belonging to
    another organization comes back None instead of being stored: the tenant
    filter runs on a real query. Same reasoning as _own_content_id.

    `public_only` for anything that lands on a public page (a logo, a
    favicon, a hero image): accepting a members-only file there would store
    a reference that renders as a broken image to every visitor.
    """
    raw = request.form.get(field, '')
    if not raw.isdigit():
        return None
    query = Upload.query.filter_by(id=int(raw))
    if public_only:
        query = query.filter_by(visibility='public')
    chosen = query.first()
    return chosen.id if chosen else None


def _own_content_id(content_id):
    """A content id from a form, or None if it is not ours.

    Storing a foreign key nobody checked leaves a row pointing into
    another organization. Reading it back through the relationship is
    filtered on the ordinary request path, but not when the parent was
    loaded unscoped or outside a request, which is where jobs and the
    command line run.
    """
    if not content_id:
        return None
    # A query, not session.get: get() answers from the identity map without
    # emitting SQL, and the tenant filter only runs on a real query.
    return content_id if Content.query.filter_by(id=content_id).first() else None


def _content_or_404(content_id) -> Content:
    return db.get_or_404(Content, content_id)


def _active_content_or_404(content_id) -> Content:
    """As _content_or_404, plus the per-org plugin gate.

    The type-slug routes check active_types(); reaching the same content
    by id skipped it, so an org could keep editing, publishing and
    mailing a plugin's content after disabling that plugin. A disable is
    reversible: re-enabling restores the list and every action on it.
    Content whose type has left CONTENT_TYPES entirely (plugin removed
    from disk) is a separate case and needs a CLI purge, not a route.
    """
    content = _content_or_404(content_id)
    if content.type not in active_types():
        abort(404)
    return content


@bp.route('/content/<type_slug>')
@org_required
@require('content.write')
def content_list(type_slug):
    if type_slug not in active_types():
        abort(404)
    ct = get_content_type(type_slug)
    # The order the type declares, so the console list and the public
    # archive agree about what order a roster reads in.
    items = Content.of_type(type_slug).order_by(
        *Content.order_for(ct)).all()
    return render_device_template('manage/content_list.html', items=items,
                           content_type=ct)


def _content_from_form(content: Content, *, previewing: bool = False) -> Content:
    """Fill `content` from the posted editor form.

    previewing=True fills an object whose render is thrown away, so it must
    leave nothing behind: an inline image upload is not stored, and a
    template the rule refuses falls back instead of flashing or raising.
    Saving reports both, so nothing is hidden, only deferred to the save.
    """
    content.title = request.form.get('title', '')
    content.slug = request.form.get('slug', '')
    content.body = request.form.get('body', '')
    content.visibility = _submitted_visibility(content.visibility)
    content.excerpt = request.form.get('excerpt', '').strip() or None
    content.seo_title = request.form.get('seo_title', '').strip() or None
    content.seo_description = request.form.get('seo_description', '').strip() or None
    # `template` reaches render_site()'s candidate list, so a value the rule
    # refuses is ignored at render. Here we also stop it blocking future saves.
    stored_template = content.template
    if stored_template and not page_template_allowed(stored_template):
        # Types without a Template field in their editor have no other way to
        # clear a value stored before the rule existed.
        content.template = None
        if not previewing:
            flash(t('manage.template_dropped', name=stored_template), 'warning')
    if content.content_type.is_page:
        template = request.form.get('template', '').strip() or None
        if template and not page_template_allowed(template):
            if template == stored_template or previewing:
                template = None         # the form posting a legacy value back
            else:
                raise ValidationError(
                    t('manage.template_unknown', name=template))
        elif (template and template != stored_template
                and not page_template_exists(template)):
            # Only a value the author is actually choosing: a theme switch can
            # strand an older one, and render_site falls back to page.html.
            if not previewing:
                raise ValidationError(t('manage.template_unknown', name=template))
            template = None
        content.template = template
        # Presentation is a page-only choice; model validation rejects
        # anything but the two known values.
        content.presentation = request.form.get('presentation', 'site')
    # Featured image: an inline file wins over a library pick. The file
    # becomes a real media-library Upload (sanitized like any other), so
    # it shows up under Manage → Media and is reusable elsewhere.
    # A preview stores nothing, so a file chosen but not yet saved has no
    # Upload to point at: the preview shows the library pick instead, and
    # the real image appears once the item is saved.
    new_image = request.files.get('featured_upload_file')
    if new_image is not None and new_image.filename and not previewing:
        content.featured_upload_id = Upload.from_file(new_image).id
    else:
        content.featured_upload_id = _own_upload_id('featured_upload_id')
    content.tags = [tag.strip() for tag in
                    request.form.get('tags', '').split(',') if tag.strip()]
    # One category, or none. The editor offers a radio list because a
    # category carries the template an item starts from and the colour its
    # card wears, and neither question has an answer when two are picked.
    # Stored through the existing join table, so the relation itself is
    # unchanged and this is a rule about how many, not a schema change.
    category_id = request.form.get('category_id', type=int)
    # A real scoped SELECT, not Query.get: get can answer from the
    # identity map and skip the tenant filter, and this id arrives
    # from a form where anyone could type another org's number.
    category = (Category.query.filter_by(id=category_id).first()
                if category_id else None)
    content.categories = [category] if category else []
    # Not a comprehension over request.form any more: a repeating field
    # arrives as one list per sub-field and has to be zipped back into rows.
    content.set_structured_fields(
        submitted_fields(content.content_type, request.form))
    return content


def _referenced_upload_ids(content, ct) -> set:
    """Upload ids the stored fields point at, top level and inside rows."""
    stored = (content.fields or {}) if content is not None else {}
    ids = set()
    for spec in ct.fields:
        value = stored.get(spec.key)
        if spec.type in ('image', 'file'):
            ids.add(value)
        elif spec.type == 'list' and isinstance(value, list):
            ids.update(row.get(sub.key) for row in value
                       for sub in spec.of if sub.type in ('image', 'file'))
    return {value for value in ids if isinstance(value, int)}


def _keep_referenced(content, ct, image_uploads, file_uploads) -> None:
    """Put back any referenced upload the recency window left out."""
    wanted = _referenced_upload_ids(content, ct)
    if not wanted:
        return
    listed = {upload.id for upload in image_uploads} | {
        upload.id for upload in file_uploads}
    missing = wanted - listed
    if not missing:
        return
    for upload in Upload.query.filter(Upload.id.in_(missing)).all():
        (image_uploads if upload.is_image else file_uploads).insert(0, upload)


def _render_content_form(content, ct, submitted=None):
    """The editor. `submitted` is the form as it was posted, passed only
    when a save was refused.

    Without it the form re-renders from what is stored, and what is stored
    is whatever the save did not change: an author whose fifth ingredient
    was missing its name would get their whole table back empty, having
    typed the other four. The refused values are theirs and they should get
    them back.
    """
    # The featured-image chooser: recent library images, with the currently
    # attached one always present — otherwise re-saving an old item whose
    # image fell off the recency window would silently clear it.
    image_uploads = (Upload.query.filter(Upload.content_type.like('image/%'))
                     .order_by(Upload.created_at.desc()).limit(24).all())
    if (content is not None and content.featured_upload is not None
            and content.featured_upload not in image_uploads):
        image_uploads.insert(0, content.featured_upload)
    # A file field offers everything in the library, not only pictures, and
    # only when the type has one: otherwise every editor page paid for a
    # query nothing rendered.
    wants_files = any(spec.type == 'file' or
                      any(sub.type == 'file' for sub in spec.of)
                      for spec in ct.fields)
    file_uploads = ((Upload.query.order_by(Upload.created_at.desc())
                     .limit(48).all()) if wants_files else [])
    # Anything an image or file field already points at, whether or not it
    # is still recent enough to be in the list. Without this the chooser
    # offers no option matching the stored value, so re-saving an older
    # picture either clears the field or fails its required check. The
    # featured image has carried the same rule for the same reason.
    _keep_referenced(content, ct, image_uploads, file_uploads)
    return render_device_template('manage/content_form.html', content=content,
                           content_type=ct, image_uploads=image_uploads,
                           file_uploads=file_uploads,
                           list_row_cap=LIST_ROW_CAP, submitted=submitted,
                           block_types=nestable_types(content),
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
            return _render_content_form(content, ct,
                                        submitted_fields(ct, request.form))
        except IntegrityError:
            # The friendly duplicate check in Content.validate races: two
            # authors publishing the same title at once, or one impatient
            # double-click, both derive the same slug and only one insert
            # wins. The constraint is the real guarantee, so say what the
            # constraint said rather than serving a 500.
            db.session.rollback()
            flash(t('manage.slug_taken'), 'error')
            return _render_content_form(content, ct,
                                        submitted_fields(ct, request.form))
    return _render_content_form(None, ct)


@bp.route('/content/<int:content_id>/edit', methods=['GET', 'POST'])
@org_required
@require('content.write')
def edit_content(content_id):
    content = _active_content_or_404(content_id)
    ct = content.content_type
    posted = None
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
            posted = submitted_fields(ct, request.form)
        except IntegrityError:
            db.session.rollback()
            flash(t('manage.slug_taken'), 'error')
            posted = submitted_fields(ct, request.form)
    return _render_content_form(content, ct, posted)


@bp.route('/content/<int:content_id>/delete', methods=['POST'])
@org_required
@require('content.write')
def delete_content(content_id):
    content = _active_content_or_404(content_id)
    type_slug = content.type
    parent_id = content.parent_id
    content.delete()
    flash(t('manage.content_deleted'), 'success')
    if parent_id is not None:
        # Back to the item it was written inside, which is where the author
        # is working. A block has no listing of its own to return to.
        return redirect(url_for('manage.edit_content', content_id=parent_id))
    return redirect(url_for('manage.content_list', type_slug=type_slug))


@bp.route('/content/<int:content_id>/blocks', methods=['POST'])
@org_required
@require('content.write')
def add_block(content_id: int) -> ResponseReturnValue:
    """Start a block inside this item and open it for writing.

    A block is an ordinary content row, so it is written in the ordinary
    editor rather than in a second one nested inside this form. That is the
    point of blocks being content: there is one place that knows how to
    edit a type's fields, and adding blocks did not have to build another.
    """
    parent = _active_content_or_404(content_id)
    type_slug = request.form.get('block_type', '')
    if parent.is_child or type_slug not in {ct.slug for ct
                                            in nestable_types(parent)}:
        abort(404)
    block = Content(type=type_slug, parent_id=parent.id,
                    title=t('manage.untitled_block'), body='')
    block.stamp_audit()
    block.save()
    return redirect(url_for('manage.edit_content', content_id=block.id))


@bp.route('/content/<int:content_id>/blocks/move', methods=['POST'])
@org_required
@require('content.write')
def move_block(content_id: int) -> ResponseReturnValue:
    """Move one block up or down inside its parent.

    How the order is kept is the model's rule, not this page's.
    """
    parent = _active_content_or_404(content_id)
    try:
        parent.move_child(request.form.get('block_id', type=int),
                          -1 if request.form.get('direction') == 'up' else 1)
    except ValidationError:
        abort(404)
    return redirect(url_for('manage.edit_content', content_id=parent.id))


def _render_preview(content: Content, ct: ContentType,
                    return_id: int | None = None) -> str:
    """`return_id` is the stored item the preview belongs to, for the
    banner's link back to the editor. A preview of unsaved form data
    renders a throwaway row with no id of its own, so the id has to be
    carried in beside it."""
    from app.platform.theming import render_site
    if ct.is_page:
        # Preview is the same sink as the public page; a stored value can
        # predate the rule.
        tmpl = (content.template
                if page_template_allowed(content.template) else None) or ct.template
        names = [f'{tmpl}.html', 'page.html']
    else:
        names = [f'single-{ct.slug}.html', f'{ct.template}.html', 'single.html']
    return render_site(names, content=content, content_type=ct,
                       page=content, preview=True,
                       preview_return_id=return_id or content.id)


def _preview_from_form(stored: Content | None, ct: ContentType) -> str:
    """Render what is in the editor right now and store none of it.

    Previewing an item that is already published must never push the edits
    live, so the form is read into a throwaway row that is never added to
    the session and the stored row is not touched at all. That is what makes
    this safe; no_autoflush and the rollback only stop a half-finished
    render leaving anything pending behind it.

    The related rows a template reads are attached by hand, because a row
    that was never written cannot lazy-load them from an id.
    """
    draft = Content.unsaved_draft(ct.slug, stored)
    try:
        with db.session.no_autoflush:
            _content_from_form(draft, previewing=True)
            draft.attach_featured_upload()
            return _render_preview(draft, ct,
                                   return_id=stored.id if stored else None)
    finally:
        db.session.rollback()


@bp.route('/content/<int:content_id>/preview', methods=['GET', 'POST'])
@org_required
@require('content.write')
@rate_limit(limit=60, window=60)
def preview_content(content_id: int) -> str:
    """GET renders what is stored, for the link on the content list. POST
    renders the unsaved editor, which is what the editor's own button
    sends."""
    content = _active_content_or_404(content_id)
    ct = content.content_type
    if request.method == 'POST':
        return _preview_from_form(content, ct)
    return _render_preview(content, ct)


@bp.route('/content/<type_slug>/preview', methods=['POST'])
@org_required
@require('content.write')
@rate_limit(limit=60, window=60)
def preview_new_content(type_slug: str) -> str:
    """A brand-new item has no id to preview by, so its type names the route
    and the form carries everything else."""
    if type_slug not in active_types():
        abort(404)
    ct = get_content_type(type_slug)
    return _preview_from_form(None, ct)


@bp.route('/content-types')
@org_required
@require('content.write')
def content_types_page():
    """The content-type library: what this organization can publish today,
    and the premade types that are on the way."""
    from app.platform.content_library import COMING_SOON
    # count_by_type groups every row this organization has, whatever its
    # type is doing, so a disabled type still reports what is waiting in it.
    counts = dict(Content.count_by_type())
    # A plugin's types belong to the plugin: installing it is the act of
    # choosing them, so a type whose plugin is not installed here has no
    # switch to offer and no row to draw.
    types = [ct for ct in CONTENT_TYPES.values()
             if ct.plugin is None or ct.slug in active_types()]
    return render_device_template('manage/content_types.html',
                           types=types, counts=counts,
                           coming_soon=COMING_SOON, org=g.org)


@bp.route('/content-types/<type_slug>', methods=['POST'])
@org_required
@require('org.settings')
def update_content_type(type_slug):
    """What this organization does with one type: whether it publishes it
    at all, who may read the section, and whether a gated item of this kind
    shows as a locked title or not at all.

    Turning a type off never deletes anything. The rows stay, the routes
    stop answering, and the row here says how many are waiting, so turning
    it back on restores exactly what was there. The same stance a plugin
    takes when it is uninstalled.
    """
    from app.platform.authz import VISIBILITY_LEVELS
    ct = CONTENT_TYPES.get(type_slug)
    if ct is None or (ct.plugin is not None
                      and type_slug not in active_types()):
        abort(404)
    if not ct.has_archive and ct.essential:
        # Nothing to decide: pages are how a site has an About page at all,
        # and a type with no archive has no section to gate.
        abort(404)
    enabled = True if ct.essential else request.form.get('enabled') == 'on'
    settings = {'enabled': enabled}
    # Only a type with an archive has a section to lock or a surface to
    # move, which is what the form offers. And only what the form actually
    # carried: a POST missing a field is not a request to clear it, and an
    # absent value stored as None reads as "no opinion", which would quietly
    # unlock a locked section.
    if ct.has_archive:
        if 'visibility' in request.form:
            # None means "no opinion", which reads as public. Anything the
            # organization does not recognise falls back to what is stored
            # rather than to that, so a form that cannot express a tier
            # cannot quietly unlock a section gated to one.
            stored = g.org.type_visibility(type_slug)
            chosen = _submitted_visibility(stored)
            settings['visibility'] = (None if chosen == VISIBILITY_LEVELS[0]
                                      else chosen)
        if 'tease' in request.form:
            settings['tease'] = {'yes': True, 'no': False}.get(
                request.form['tease'])
        if 'presentation' in request.form:
            # Empty means "whatever the type says", a real answer and not
            # the same as copying today's answer into storage.
            presentation = request.form['presentation']
            settings['presentation'] = (
                presentation if presentation in ('site', 'community') else None)
    g.org.set_type_settings(type_slug, **settings)
    flash(t('common.saved'), 'success')
    return redirect(url_for('manage.content_types_page'))


@bp.route('/categories', methods=['GET', 'POST'])
@org_required
@require('content.write')
def categories():
    if request.method == 'POST':
        category = Category(name=request.form.get('name', ''),
                            slug=request.form.get('slug', ''),
                            icon=request.form.get('icon', ''),
                            body_template=request.form.get('body_template', ''))
        try:
            category.save()
            flash(t('common.saved'), 'success')
        except ValidationError as e:
            flash(e.message, 'error')
        return redirect(url_for('manage.categories'))
    category_list = Category.query.order_by(Category.name).all()
    return render_device_template('manage/categories.html',
                                  categories=category_list,
                                  icons=Category.ICONS)


@bp.route('/categories/<int:category_id>', methods=['POST'])
@org_required
@require('content.write')
def edit_category(category_id):
    """Rename a category or change its icon.

    The slug is editable too, and changing it moves the category's archive
    URL -- the same trade every slug in the product makes.
    """
    category = db.get_or_404(Category, category_id)
    category.name = request.form.get('name', '')
    category.slug = request.form.get('slug', '')
    category.icon = request.form.get('icon', '')
    category.body_template = request.form.get('body_template', '')
    try:
        category.save()
        flash(t('common.saved'), 'success')
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
    return redirect(url_for('manage.categories'))


@bp.route('/categories/<int:category_id>/delete', methods=['POST'])
@org_required
@require('content.write')
def delete_category(category_id):
    category = db.get_or_404(Category, category_id)
    category.delete()
    return redirect(url_for('manage.categories'))


# --- Navigation ----------------------------------------------------------------

@bp.route('/navigation', methods=['GET', 'POST'])
@org_required
@require('content.write')
def navigation():
    if request.method == 'POST':
        parent_id = request.form.get('parent_id', type=int) or None
        url = request.form.get('url', '').strip() or None
        submitted_content_id = request.form.get('content_id', type=int)
        content_id = _own_content_id(submitted_content_id)
        # The UI says which it is adding; a raw POST without `kind` keeps
        # the old convention (destination → link, label-only → group).
        kind = request.form.get('kind') or (
            'link' if (url or submitted_content_id) else 'group')
        if kind == 'group':
            url = content_id = parent_id = None
        elif not (url or content_id):
            # Covers the empty form and a content_id pointing at another
            # tenant's row (_own_content_id nulls it): nothing is created.
            flash(t('manage.nav_link_needs_destination'), 'error')
            return redirect(url_for('manage.navigation'))
        item = NavigationItem(
            menu=request.form.get('menu', 'primary'),
            label=request.form.get('label', ''),
            url=url,
            content_id=content_id,
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
    # published_query lists only types this organization publishes, so a
    # disabled section cannot be offered as a link that would 404.
    linkable = (Content.published_query()
                .order_by(Content.type, Content.title).all())
    return render_device_template('manage/navigation.html', menus=menus,
                           linkable=linkable)


@bp.route('/navigation/columns/suggested', methods=['POST'])
@org_required
@require('content.write')
def navigation_suggested_columns():
    NavigationItem.create_suggested_footer_column()
    flash(t('common.saved'), 'success')
    return redirect(url_for('manage.navigation'))


@bp.route('/navigation/<int:item_id>/move', methods=['POST'])
@org_required
@require('content.write')
def move_navigation(item_id):
    item = db.get_or_404(NavigationItem, item_id)
    item.move(-1 if request.form.get('direction') == 'up' else 1)
    return redirect(url_for('manage.navigation'))


@bp.route('/navigation/<int:item_id>/delete', methods=['POST'])
@org_required
@require('content.write')
def delete_navigation(item_id):
    item = db.get_or_404(NavigationItem, item_id)
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
    from app.models import PasswordReset, Tier
    from app.platform.mailer import is_email_configured
    all_tiers = Tier.in_order(include_retired=True)
    return render_device_template(
        'manage/members.html', members=member_list, invitations=invitations,
        # So the page states the same lifetime the model enforces.
        reset_expiry_hours=PasswordReset.EXPIRY_HOURS,
        # Two lists, deliberately. A picker offers what may be chosen
        # afresh; a member's own row has to show the rung they are actually
        # on, retired or not, or one Save on that row moves them off it
        # without anybody meaning to.
        tiers=[tier for tier in all_tiers if tier.is_active],
        all_tiers=all_tiers,
        email_configured=is_email_configured())


@bp.route('/members/add', methods=['POST'])
@org_required
@require('members.manage')
def add_member():
    from app.models import Membership, User
    email = request.form.get('email', '').strip().lower()
    user = User.get_by_email(email)
    if user is None:
        flash(t('members.user_not_found', email=email), 'error')
    else:
        try:
            tier = _chosen_tier()
            Membership.add(user.id, g.org.id, role=_granted_role(),
                           tier_id=tier.id if tier else None)
            flash(t('admin.member_added', email=email), 'success')
        except ValidationError as e:
            flash(e.message, 'error')
    return redirect(url_for('manage.members'))


def _granted_role(default: str = 'member') -> str:
    """The role from the form, refused if the caller cannot grant it.

    members.manage belongs to admin as well as owner, so nothing stopped an
    admin handing out owner. Doing that to their own membership made them a
    second owner, which satisfied the keep-an-owner guard and let them then
    remove the founder. Granting owner is an ownership change, so it takes
    the ownership.transfer permission that only an owner holds.
    """
    role = request.form.get('role', default)
    if role == 'owner' and not can('ownership.transfer'):
        raise ValidationError(t('members.cannot_grant_owner'))
    return role


# A bigint column holds this much and no more. Flask's <int:> converter
# puts no ceiling on a path segment, so without this a long enough number
# reaches the driver and raises there, which is a 500 rather than a 404.
MAX_ROW_ID = 2 ** 63


def _own_membership(membership_id):
    from app.models import Membership
    if not 0 < membership_id < MAX_ROW_ID:
        abort(404)
    membership = db.session.get(Membership, membership_id)
    if membership is None or membership.org_id != g.org.id:
        abort(404)
    return membership


def _manageable_membership(membership_id):
    """The target, refused when it holds more than the caller does.

    members.manage belongs to admin as well as owner, and these routes only
    ever checked what the caller may grant, never what the target already
    holds. An admin could demote, suspend or remove a founding owner, which
    the keep-an-owner rule allowed as long as a second owner existed.
    """
    membership = _own_membership(membership_id)
    if grants_more_than(membership.role, g.membership.role):
        raise ValidationError(t('members.cannot_manage_higher_role'))
    return membership


@bp.route('/members/<int:membership_id>/role', methods=['POST'])
@org_required
@require('members.manage')
def member_role(membership_id):
    try:
        membership = _manageable_membership(membership_id)
        role = _granted_role()
        if (membership.user_id == current_user.id
                and grants_more_than(role, membership.role)):
            # Stepping down, or re-saving the role you already hold, is
            # fine. Handing yourself more than you have is not.
            raise ValidationError(t('members.cannot_promote_self'))
        membership.change_role(role)
        flash(t('common.saved'), 'success')
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
    return redirect(url_for('manage.members'))


@bp.route('/members/<int:membership_id>/suspend', methods=['POST'])
@org_required
@require('members.manage')
def member_suspend(membership_id):
    try:
        membership = _manageable_membership(membership_id)
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
    try:
        _manageable_membership(membership_id).unsuspend()
        flash(t('common.saved'), 'success')
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
    return redirect(url_for('manage.members'))


@bp.route('/members/<int:membership_id>/remove', methods=['POST'])
@org_required
@require('members.manage')
def member_remove(membership_id):
    try:
        membership = _manageable_membership(membership_id)
        membership.remove()
        flash(t('admin.member_removed'), 'success')
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
    return redirect(url_for('manage.members'))


@bp.route('/members/<int:membership_id>/reset-password', methods=['POST'])
@org_required
@require('members.manage')
@rate_limit(limit=10, window=300)
def member_reset_password(membership_id: int) -> ResponseReturnValue:
    """Hand a member a one-time link back into their account.

    The recovery path for an installation with no email, which is every
    installation that has not configured any: without this, a member who
    forgets their password waits for whoever can reach a shell on the
    server.

    Three questions are asked before a link exists, and they are different
    questions. Whether the caller may manage this member at all is
    _manageable_membership, the same guard that stops an admin suspending
    an owner. Whether the caller may do it to themselves is below, and the
    answer is no because Change password is the door for that and this one
    would lock them out of the session they are standing in. Whether this
    organization may reset this account at all belongs to the account
    rather than to the request, so PasswordReset.issue asks it.
    """
    from app.models import PasswordReset
    from app.models.password_reset import can_be_reset_by_org
    from app.platform.mailer import is_email_configured, try_send_email
    try:
        membership = _manageable_membership(membership_id)
        if membership.user_id == current_user.id:
            raise ValidationError(t('members.reset_not_yourself'))
        if not can_be_reset_by_org(membership.user, g.org.id):
            # The model owns the rule; the wording is ours. One refusal for
            # every reason it can say no to, so the message cannot be read
            # backwards into a fact about the rest of the installation.
            raise ValidationError(t('members.reset_refused'))
        reset, token = PasswordReset.issue(membership.user, g.org.id)
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
        return redirect(url_for('manage.members'))

    reset_url = reset.url(token)
    # Logged like the other membership actions, and deliberately without the
    # token: this line says a link was made, never what it was.
    log.info('password_reset_link_issued', org_id=g.org.id,
             user_id=membership.user_id, by_user_id=current_user.id)
    # The person it was done to hears about it. On an installation with no
    # email this is the only signal they get, and a capability that leaves
    # no trace the subject can see is not one anybody can hold to account.
    notify_password_reset_issued(membership.user, g.org.id, current_user.name)
    sent = False
    if is_email_configured() and membership.user.is_emailable:
        # Where there is email, the member gets it directly and the admin
        # need not handle it at all. The link is still shown, because the
        # message may not arrive and the admin is standing right there.
        #
        # is_emailable as well as is_email_configured: an identity column
        # does not have to hold a deliverable address, and the property
        # exists because senders are expected to ask.
        subject, text, html = password_reset_message(g.org, reset_url)
        sent = try_send_email(membership.user.email, subject, text,
                              html=html, attribution=False)
    # Rendered, not flashed, and so not redirected to either. A flash goes
    # home in the session cookie, which is signed but not encrypted and is
    # sent to every organization's address on the installation, so flashing
    # this would put a working key to somebody's account in the admin's
    # cookie jar until whenever their browser next asked for a page that
    # draws one. A page of its own also suits a value somebody has to copy
    # better than a banner above a table does.
    return render_device_template('manage/member_reset.html',
                                  member=membership.user, reset_url=reset_url,
                                  emailed=sent,
                                  expiry_hours=PasswordReset.EXPIRY_HOURS)


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


# --- Tiers ------------------------------------------------------------------

def _submitted_visibility(stored: str | None) -> str:
    """The visibility the form asked for, or what is already stored.

    For editing something that already has one. Every form carrying this
    field is a select built from authz.visibility_choices, so an
    unrecognised value means either a crafted post or a form rendered
    before a tier was retired. Falling back to what is stored is the safe
    direction: the alternative is defaulting to public, which turns "for
    Pro members" into "for anyone" because somebody saved a title.

    Not for creating something, where there is nothing to fall back to and
    a value the vocabulary does not know should be reported by validate
    rather than silently replaced.
    """
    submitted = request.form.get('visibility')
    if submitted and visibility_is_valid(submitted):
        return submitted
    if stored is None:
        # Nothing to fall back to: this row is being created. Hand the
        # value on and let validate refuse it, rather than substituting a
        # default that reads as public.
        return submitted or VISIBILITY_LEVELS[0]
    return stored


def _chosen_tier(*, held_by=None) -> 'Tier | None':
    """The tier named by the form's `tier_id`, or None when it is absent.

    The value comes from a form and a form is whatever the browser sends,
    so both rules the templates draw are enforced again here. Another
    organization's tier is refused (Membership.validate refuses it too, so
    this is the message rather than the guard). A retired tier is refused
    as well, except for the one membership already standing on it: the
    Members page has to offer that rung so saving the row does not move
    somebody off it, and that is the only place a retired tier is a legal
    answer.
    """
    from app.models import Tier
    raw = (request.form.get('tier_id') or '').strip()
    if not raw:
        return None
    try:
        # isdigit() alone is true of Unicode digits int() will not take.
        tier_id = int(raw)
    except ValueError:
        raise ValidationError(t('tiers.unknown')) from None
    # And an integer wider than the column reaches the driver and
    # overflows there, which is a 500 rather than a refusal.
    if not 0 < tier_id < MAX_ROW_ID:
        raise ValidationError(t('tiers.unknown'))
    tier = Tier.query.filter_by(id=tier_id).first()
    if tier is None:
        raise ValidationError(t('tiers.unknown'))
    if not tier.is_active and (held_by is None
                               or held_by.tier_id != tier.id):
        raise ValidationError(t('tiers.retired_not_offered'))
    return tier


@bp.route('/tiers')
@org_required
@require('members.manage')
def tiers() -> ResponseReturnValue:
    """The ladder: what a member may read, bottom rung first."""
    from app.models import Tier
    # Retired rungs included: this is the only page that can bring one
    # back, and a tier that vanished from it would be unrecoverable.
    ladder = Tier.in_order(include_retired=True)
    held = Tier.member_counts(g.org.id)
    return render_device_template(
        'manage/tiers.html', tiers=ladder,
        counts={tier.id: held.get(tier.id, 0) for tier in ladder})


@bp.route('/tiers', methods=['POST'])
@org_required
@require('members.manage')
def create_tier() -> ResponseReturnValue:
    from app.models import Tier
    try:
        Tier.add(name=request.form.get('name', ''),
                 slug=request.form.get('slug', ''))
        flash(t('common.saved'), 'success')
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
    return redirect(url_for('manage.tiers'))


def _own_tier(tier_id: int) -> 'Tier':
    from app.models import Tier
    if not 0 < tier_id < MAX_ROW_ID:
        abort(404)
    tier = db.session.get(Tier, tier_id)
    if tier is None or tier.org_id != g.org.id:
        abort(404)
    return tier


@bp.route('/tiers/<int:tier_id>/rename', methods=['POST'])
@org_required
@require('members.manage')
def rename_tier(tier_id: int) -> ResponseReturnValue:
    try:
        _own_tier(tier_id).rename(request.form.get('name', ''))
        flash(t('common.saved'), 'success')
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
    return redirect(url_for('manage.tiers'))


@bp.route('/tiers/<int:tier_id>/move', methods=['POST'])
@org_required
@require('members.manage')
def move_tier(tier_id: int) -> ResponseReturnValue:
    """Reordering changes who can read what, so the page says so before
    the button is pressed rather than after."""
    direction = -1 if request.form.get('direction') == 'up' else 1
    try:
        _own_tier(tier_id).move(direction)
        flash(t('common.saved'), 'success')
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
    return redirect(url_for('manage.tiers'))


@bp.route('/tiers/<int:tier_id>/toggle', methods=['POST'])
@org_required
@require('members.manage')
def toggle_tier(tier_id: int) -> ResponseReturnValue:
    """Retired, never deleted: members on it keep it, and content that
    requires it still means what it meant."""
    tier = _own_tier(tier_id)
    try:
        tier.restore() if not tier.is_active else tier.retire()
        flash(t('common.saved'), 'success')
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
    return redirect(url_for('manage.tiers'))


@bp.route('/members/<int:membership_id>/tier', methods=['POST'])
@org_required
@require('members.manage')
def member_tier(membership_id: int) -> ResponseReturnValue:
    """An administrator may move anyone between tiers at any time, their
    own membership included. A tier is not a permission: moving one grants
    nothing but reading."""
    try:
        membership = _own_membership(membership_id)
        tier = _chosen_tier(held_by=membership)
        if tier is None:
            raise ValidationError(t('tiers.unknown'))
        membership.set_tier(tier)
        flash(t('common.saved'), 'success')
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
    email = request.form.get('email', '').strip().lower() or None
    try:
        tier = _chosen_tier()
        invitation, token = Invitation.create(
            g.org.id, role=_granted_role(), email=email,
            tier_id=tier.id if tier else None)
    except ValidationError as e:
        flash(e.message, 'error')
        return redirect(url_for('manage.members'))

    invite_url = invitation.url(token)
    if email:
        # Composed only when there is somebody to send it to: an invitation
        # taken away as a link costs no render.
        subject, text, html = invitation_message(g.org, invite_url)
        if try_send_email(email, subject, text, html=html,
                          attribution=False):
            flash(t('members.invite_sent', email=email), 'success')
    # The URL is shown once: only its hash is stored.
    flash(t('members.invite_link', url=invite_url), 'invite')
    return redirect(url_for('manage.members'))


@bp.route('/invitations/<int:invitation_id>/revoke', methods=['POST'])
@org_required
@require('members.manage')
def revoke_invitation(invitation_id):
    from app.models.invitation import Invitation
    invitation = db.get_or_404(Invitation, invitation_id)
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
@rate_limit(limit=60, window=60)
def media():
    if request.method == 'POST':
        file = request.files.get('file')
        if file is None or not file.filename:
            flash(t('manage.no_file'), 'error')
        else:
            try:
                Upload.from_file(file, visibility=_upload_visibility())
                flash(t('common.saved'), 'success')
            except ValidationError as e:
                flash(e.message, 'error')
        return redirect(url_for('manage.media'))

    uploads = Upload.query.order_by(Upload.created_at.desc()).all()
    return render_device_template('manage/media.html', uploads=uploads)


def _upload_visibility(current: str = 'public') -> str:
    """The posted visibility, or `current` if the field is absent.

    Public is the default for a new upload: this is a publishing product
    and most media belongs on the public site. On an update `current` is
    the file's own setting, so a post that omits the field cannot quietly
    turn a members-only file public.
    """
    choice = request.form.get('visibility')
    if choice is None:
        return current
    return choice if choice in VISIBILITY_LEVELS else current


@bp.route('/media/<int:upload_id>', methods=['POST'])
@org_required
@require('content.write')
def update_media(upload_id):
    """The per-file form in Manage → Media: who may see it, and what a
    screen reader says in its place."""
    upload = db.get_or_404(Upload, upload_id)
    upload.visibility = _upload_visibility(upload.visibility)
    # Absent means "not being edited", never "clear it" -- same reasoning as
    # _upload_visibility. The form omits this field for non-images.
    if 'alt' in request.form:
        upload.alt = request.form['alt'].strip()[:200] or None
    upload.stamp_audit()
    upload.save()
    flash(t('common.saved'), 'success')
    return redirect(url_for('manage.media'))


@bp.route('/media/<int:upload_id>/delete', methods=['POST'])
@org_required
@require('content.write')
def delete_media(upload_id):
    upload = db.get_or_404(Upload, upload_id)
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
    return render_device_template('manage/domains.html', domains=domain_list)


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
    return render_device_template('manage/plugins.html', manifests=MANIFESTS,
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
        'subscribed': Subscriber.audience(g.org.id).count(),
        'pending': Subscriber.query.filter_by(status='pending').count(),
        'unsubscribed': Subscriber.query.filter_by(status='unsubscribed').count(),
    }
    return render_device_template('manage/newsletter.html', subscribers=subscribers,
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
    subscriber = db.get_or_404(Subscriber, subscriber_id)
    subscriber.delete()
    flash(t('newsletter.subscriber_removed'), 'success')
    return redirect(url_for('manage.newsletter'))


@bp.route('/content/<int:content_id>/send-newsletter', methods=['POST'])
@org_required
@require('content.write')
def send_content_newsletter(content_id):
    from app.models.newsletter import Delivery, Subscriber
    from app.platform.jobs import enqueue
    from app.platform.mailer import is_email_configured

    content = _active_content_or_404(content_id)
    if content.is_child:
        # A block is read inside the thing it belongs to. Mailing one would
        # hand it a delivery record and a line in the newsletter archive --
        # an address for something that has none by design.
        abort(404)
    if not is_email_configured():
        flash(t('newsletter.email_required_to_send'), 'error')
        return redirect(url_for('manage.edit_content', content_id=content.id))
    if Subscriber.audience(g.org.id).count() == 0:
        flash(t('newsletter.no_subscribers'), 'error')
        return redirect(url_for('manage.edit_content', content_id=content.id))

    delivery = Delivery.create_for_content(content)
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
    if request.method == 'POST' and 'content_discussion_group' in request.form:
        # Which group a "Discuss this" thread on an article opens in.
        slug = request.form['content_discussion_group']
        if slug and DiscussionGroup.query.filter_by(slug=slug).first() is None:
            slug = None                  # a group deleted in another tab
        g.org.update_settings(content_discussion_group=slug or None)
        flash(t('common.saved'), 'success')
        return redirect(url_for('manage.discussions'))
    if request.method == 'POST' and 'area_visibility' in request.form:
        # The whole-area switch; per-group visibility still applies in
        # 'per_group' mode.
        value = request.form['area_visibility']
        if value == 'per_group' or visibility_is_valid(value):
            g.org.update_settings(discussions_visibility=value)
            flash(t('common.saved'), 'success')
        return redirect(url_for('manage.discussions'))
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
    groups = DiscussionGroup.in_order()
    return render_device_template('manage/discussions.html', groups=groups)


@bp.route('/discussions/<int:group_id>/visibility', methods=['POST'])
@org_required
@require('content.moderate')
def toggle_group_visibility(group_id):
    """Set who may read one group.

    A select rather than the two-state toggle this replaced. That toggle
    could say public or members and nothing else, so a group gated to a
    tier had no way back: pressing it would have flattened the tier into
    one of the two, and refusing left the group gated for good.

    The value is checked here as well as offered by the picker, because a
    form is whatever the browser sends.
    """
    from app.models.discussion import DiscussionGroup
    group = db.get_or_404(DiscussionGroup, group_id)
    chosen = request.form.get('visibility', '')
    if not visibility_is_valid(chosen):
        flash(t('manage.visibility_unknown'), 'error')
        return redirect(url_for('manage.discussions'))
    group.visibility = chosen
    # A flag flip on a row this request is not otherwise editing: a
    # description saved before the length rule must not block it.
    group.save_flag()
    flash(t('common.saved'), 'success')
    return redirect(url_for('manage.discussions'))


@bp.route('/discussions/<int:group_id>/move', methods=['POST'])
@org_required
@require('content.moderate')
def move_group(group_id):
    from app.models.discussion import DiscussionGroup
    group = db.get_or_404(DiscussionGroup, group_id)
    group.move(-1 if request.form.get('direction') == 'up' else 1)
    return redirect(url_for('manage.discussions'))


@bp.route('/discussions/<int:group_id>/delete', methods=['POST'])
@org_required
@require('content.moderate')
def delete_group(group_id):
    from app.models.discussion import DiscussionGroup
    group = db.get_or_404(DiscussionGroup, group_id)
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
    return render_device_template('manage/flags.html', flags=open_flags)


@bp.route('/flags/<int:flag_id>/resolve', methods=['POST'])
@org_required
@require('content.moderate')
def resolve_flag(flag_id):
    from app.models.discussion import Flag
    flag = db.get_or_404(Flag, flag_id)
    flag.resolve()
    flash(t('common.saved'), 'success')
    return redirect(url_for('manage.flags'))


# --- Settings pages (branding / theme / analytics / privacy) ---------------------
# Each section is its own page: Branding, Theme, and Analytics stand alone in
# the sidenav; the remaining sections live under the Settings entry, which
# opens a second-column sub-nav (see manage/_layout.html). Every page handles
# its own POST and redirects to itself, so a validation flash lands where the
# form is.

@bp.route('/settings')
@org_required
@require('org.settings')
def settings():
    # The old one-page settings URL, kept as the Settings entry point.
    return redirect(url_for('manage.privacy_settings'))


@bp.route('/branding', methods=['GET', 'POST'])
@org_required
@require('org.settings')
def branding():
    org = g.org
    if request.method == 'POST':
        try:
            org.name = request.form.get('name', org.name)
            org.description = request.form.get('description', '').strip() or None
            org.brand_primary = request.form.get('brand_primary', '').strip() or None
            org.save()
            # Public-site name: blank means "same as the community name", so
            # it is stored empty rather than copied, and follows a later
            # rename on its own.
            site_name = request.form.get('site_name', '').strip()[:100]
            reject_control_characters(site_name, t('manage.site_name'))
            org.update_settings(site_name=site_name)
            org.update_settings(**{
                field: _own_upload_id(field, public_only=True)
                for field in ('logo_upload_id', 'favicon_upload_id',
                              'hero_upload_id')})
            flash(t('common.saved'), 'success')
        except ValidationError as e:
            db.session.rollback()
            flash(e.message, 'error')
        return redirect(url_for('manage.branding'))

    # Only public images: the logo and favicon are rendered to visitors,
    # so a members-only file here is a broken image, not a private one.
    uploads = Upload.query.filter(Upload.content_type.like('image/%'),
                                  Upload.visibility == 'public') \
        .order_by(Upload.created_at.desc()).all()
    return render_device_template('manage/branding.html', org=org, uploads=uploads)


@bp.route('/theme', methods=['GET', 'POST'])
@org_required
@require('org.settings')
def theme_settings():
    org = g.org
    if request.method == 'POST':
        from app.platform.theming import clean_theme_config
        try:
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
        return redirect(url_for('manage.theme_settings'))
    from app.platform.theming import saved_theme, theme_setting_values
    # active, not org.theme: a legacy alias or an uninstalled theme would
    # otherwise leave the picker with nothing selected and point the
    # preview at a theme that no longer resolves.
    return render_device_template('manage/theme.html', org=org,
                           themes=AVAILABLE_THEMES,
                           active=saved_theme(org),
                           theme_values=theme_setting_values(org))


@bp.route('/theme/preview')
@org_required
@require('org.settings')
def theme_preview() -> ResponseReturnValue:
    """The home page as a given theme would render it, for the iframe beside
    the picker.

    The real front page through the real pipeline, so what is shown is what
    will ship: the org's own copy, branding and colour, in a theme it has
    not chosen yet. g.preview_theme is read by theming.current_theme and
    honoured only on this endpoint, so a stale value cannot reach a
    visitor. This is also the one response the console may frame, which
    _init_security_headers decides by endpoint rather than by a flag.
    """
    from app.controllers.site import render_org_home
    theme = request.args.get('theme', '')
    if theme not in AVAILABLE_THEMES:
        abort(404)
    g.preview_theme = theme
    # The picker sends its settings along, so the colour in the preview is
    # the one in the form rather than the one last saved. Validated exactly
    # as the save path validates it: these land in a <style> block. A value
    # half typed is simply not applied yet, which is better than an error
    # page appearing inside the frame.
    from app.platform.theming import (
        PREVIEWABLE_SETTING_TYPES,
        clean_theme_config,
    )
    submitted = {
        key: request.args.get(f'theme_{key}', '')
        for key, spec in AVAILABLE_THEMES[theme].get('settings', {}).items()
        if spec.get('type') in PREVIEWABLE_SETTING_TYPES}
    try:
        g.preview_config = clean_theme_config(theme, submitted)
    except ValidationError:
        # Set either way rather than left alone: a value being typed is not
        # an error page, and the theme's own default is the honest thing to
        # show until the value is a colour again.
        g.preview_config = {}
    return render_org_home()


@bp.route('/analytics', methods=['GET', 'POST'])
@org_required
@require('org.settings')
def analytics_settings():
    from app.platform.analytics import ANALYTICS_PROVIDERS, clean_analytics_settings
    org = g.org
    if request.method == 'POST':
        try:
            org.update_settings(
                analytics=clean_analytics_settings(request.form))
            flash(t('common.saved'), 'success')
        except ValidationError as e:
            db.session.rollback()
            flash(e.message, 'error')
        return redirect(url_for('manage.analytics_settings'))
    return render_device_template('manage/analytics.html', org=org,
                           analytics_providers=ANALYTICS_PROVIDERS)


@bp.route('/settings/privacy', methods=['GET', 'POST'])
@org_required
@require('org.settings')
def privacy_settings():
    org = g.org
    if request.method == 'POST':
        # Checkbox: absent from the form when unchecked.
        org.update_settings(gated_teasers='gated_teasers' in request.form)
        flash(t('common.saved'), 'success')
        return redirect(url_for('manage.privacy_settings'))
    return render_device_template('manage/privacy.html', org=org)


# --- Theme editor (theme-declared editable content) ---------------------------

@bp.route('/landing/sections', methods=['POST'])
@org_required
@require('content.write')
def landing_sections() -> ResponseReturnValue:
    """Which sections the public front page advertises, and in what order.

    Its own address, not a second form posting to the page's. Sharing one
    meant a save here ran the theme-copy save over a form carrying no copy,
    and the headline somebody had written was gone. Two things that save
    separately should submit separately.

    What to store, and in what order, is the organization's rule and lives
    on the model with the rest of the per-type map.
    """
    g.org.set_site_entries(request.form.getlist('site_entries'),
                           offerable_sections())
    flash(t('common.saved'), 'success')
    return redirect(url_for('manage.landing_settings'))


@bp.route('/landing', methods=['GET', 'POST'])
@org_required
@require('content.write')
def landing_settings():
    """Edit the active theme's declared content (theme.json "content"). The
    design ships in the theme; only the words are per-org, stored under
    settings['theme_content'][<theme>] so each theme keeps its own copy. Text
    renders into autoescaped HTML — the schema's length caps are the only
    write-time guard (see app.platform.theme_content).

    Always reachable, even for a theme that declares nothing editable: the
    page then explains why it is empty. A nav entry that comes and goes with
    the active theme is harder to learn than one that is always there.
    """
    theme = current_theme()

    if request.method == 'POST':
        if tc.has_editor(theme):
            store = dict(g.org.setting('theme_content') or {})
            store[theme] = tc.clean(theme, request.form)
            g.org.update_settings(theme_content=store)
        flash(t('common.saved'), 'success')
        return redirect(url_for('manage.landing_settings'))

    fields = tc.editor_view(theme, g.org)
    # Only public images: what the theme renders is a public page, so a
    # members-only file would be a broken image on it.
    image_uploads = (Upload.query
                     .filter(Upload.content_type.like('image/%'),
                             Upload.visibility == 'public')
                     .order_by(Upload.created_at.desc()).limit(24).all())
    # A picture chosen long ago can fall out of that list two ways, and they
    # need opposite handling. Still public but no longer recent: put it back,
    # or the form would offer no radio for it and the next save would clear
    # it. No longer public (or deleted): it cannot go back, because the
    # chooser's whole promise is that everything in it is safe to publish --
    # so the field says so instead of quietly showing None.
    chosen = {f['value'] for f in fields
              if f['type'] == 'image' and f['value']}
    still_offered = set()
    if chosen:
        for upload in (Upload.query
                       .filter(Upload.id.in_(chosen),
                               Upload.visibility == 'public').all()):
            still_offered.add(upload.id)
            if upload not in image_uploads:
                image_uploads.insert(0, upload)
    for field in fields:
        if field['type'] == 'image' and field['value']:
            field['unavailable'] = field['value'] not in still_offered
    return render_device_template('manage/landing.html',
                           offered_sections=offerable_sections(),
                           chosen_sections=[ct.slug for ct in site_entry_types()],
                           fields=fields,
                           image_uploads=image_uploads,
                           theme_name=AVAILABLE_THEMES[theme]['name'])
