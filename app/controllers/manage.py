"""Organization management: pages, navigation, media, settings, theme.

Runs on the org host under /manage; every query is tenant-scoped
automatically."""

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    request,
    url_for,
)
from flask_login import current_user

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
)
from app.platform.content_types import CONTENT_TYPES, active_types, get_content_type
from app.platform.devices import render_device_template
from app.platform.errors import ValidationError
from app.platform.i18n import t
from app.platform.logger import get_logger
from app.platform.theming import (
    AVAILABLE_THEMES,
    current_theme,
    page_template_allowed,
    page_template_exists,
)

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
    items = (Content.of_type(type_slug)
             .order_by(Content.created_at.desc()).all())
    return render_device_template('manage/content_list.html', items=items,
                           content_type=ct)


def _content_from_form(content):
    content.title = request.form.get('title', '')
    content.slug = request.form.get('slug', '')
    content.body = request.form.get('body', '')
    content.excerpt = request.form.get('excerpt', '').strip() or None
    content.visibility = request.form.get('visibility', 'public')
    content.seo_title = request.form.get('seo_title', '').strip() or None
    content.seo_description = request.form.get('seo_description', '').strip() or None
    # `template` reaches render_site()'s candidate list, so a value the rule
    # refuses is ignored at render. Here we also stop it blocking future saves.
    stored_template = content.template
    if stored_template and not page_template_allowed(stored_template):
        # Types without a Template field in their editor have no other way to
        # clear a value stored before the rule existed.
        content.template = None
        flash(t('manage.template_dropped', name=stored_template), 'warning')
    if content.content_type.is_page:
        template = request.form.get('template', '').strip() or None
        if template and not page_template_allowed(template):
            if template == stored_template:
                template = None         # the form posting a legacy value back
            else:
                raise ValidationError(
                    t('manage.template_unknown', name=template))
        elif (template and template != stored_template
                and not page_template_exists(template)):
            # Only a value the author is actually choosing: a theme switch can
            # strand an older one, and render_site falls back to page.html.
            raise ValidationError(t('manage.template_unknown', name=template))
        content.template = template
        # Presentation is a page-only choice; model validation rejects
        # anything but the two known values.
        content.presentation = request.form.get('presentation', 'site')
    # Featured image: an inline file wins over a library pick. The file
    # becomes a real media-library Upload (sanitized like any other), so
    # it shows up under Manage → Media and is reusable elsewhere.
    new_image = request.files.get('featured_upload_file')
    if new_image is not None and new_image.filename:
        content.featured_upload_id = Upload.from_file(new_image).id
    else:
        content.featured_upload_id = _own_upload_id('featured_upload_id')
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
    # The featured-image chooser: recent library images, with the currently
    # attached one always present — otherwise re-saving an old item whose
    # image fell off the recency window would silently clear it.
    image_uploads = (Upload.query.filter(Upload.content_type.like('image/%'))
                     .order_by(Upload.created_at.desc()).limit(24).all())
    if (content is not None and content.featured_upload is not None
            and content.featured_upload not in image_uploads):
        image_uploads.insert(0, content.featured_upload)
    return render_device_template('manage/content_form.html', content=content,
                           content_type=ct, image_uploads=image_uploads,
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
    content = _active_content_or_404(content_id)
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
    content = _active_content_or_404(content_id)
    type_slug = content.type
    content.delete()
    flash(t('manage.content_deleted'), 'success')
    return redirect(url_for('manage.content_list', type_slug=type_slug))


@bp.route('/content/<int:content_id>/preview')
@org_required
@require('content.write')
def preview_content(content_id):
    from app.platform.theming import render_site
    content = _active_content_or_404(content_id)
    ct = content.content_type
    if ct.is_page:
        # Preview is the same sink as the public page; a stored value can
        # predate the rule.
        tmpl = (content.template
                if page_template_allowed(content.template) else None) or ct.template
        names = [f'{tmpl}.html', 'page.html']
    else:
        names = [f'single-{ct.slug}.html', f'{ct.template}.html', 'single.html']
    return render_site(names, content=content, content_type=ct,
                       page=content, preview=True)


@bp.route('/content-types')
@org_required
@require('content.write')
def content_types_page():
    """The content-type library: what this organization can publish today,
    and the premade types that are on the way."""
    from app.platform.content_library import COMING_SOON
    counts = dict(Content.count_by_type())
    return render_device_template('manage/content_types.html',
                           types=CONTENT_TYPES.values(), counts=counts,
                           coming_soon=COMING_SOON,
                           section_visibility=Content.section_visibility)


@bp.route('/content-types/<type_slug>/visibility', methods=['POST'])
@org_required
@require('org.settings')
def toggle_section_visibility(type_slug):
    """Lock/unlock a whole content section: locked sections gate every item
    in them for non-members, item settings notwithstanding."""
    ct = CONTENT_TYPES.get(type_slug)
    if ct is None or not ct.base:              # only nav sections lock
        abort(404)
    store = dict(g.org.setting('section_visibility') or {})
    if store.get(type_slug) == 'members':
        store.pop(type_slug)
    else:
        store[type_slug] = 'members'
    g.org.update_settings(section_visibility=store)
    flash(t('common.saved'), 'success')
    return redirect(url_for('manage.content_types_page'))


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
    return render_device_template('manage/categories.html', categories=category_list)


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
    from app.platform.mailer import is_email_configured
    return render_device_template('manage/members.html', members=member_list,
                           invitations=invitations,
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
            Membership.add(user.id, g.org.id, role=_granted_role())
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


def _own_membership(membership_id):
    from app.models import Membership
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
    email = request.form.get('email', '').strip().lower() or None
    try:
        invitation, token = Invitation.create(
            g.org.id, role=_granted_role(), email=email)
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
    if request.method == 'POST' and 'area_visibility' in request.form:
        # The whole-area switch; per-group visibility still applies in
        # 'per_group' mode.
        value = request.form['area_visibility']
        if value in DiscussionGroup.AREA_VISIBILITIES:
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
    """Lock/unlock one group (flips public <-> members)."""
    from app.models.discussion import DiscussionGroup
    group = db.get_or_404(DiscussionGroup, group_id)
    group.visibility = ('public' if group.visibility == 'members'
                        else 'members')
    # A flag flip on a row this request is not otherwise editing: a
    # description saved before the length rule must not block it.
    group.save_flag()
    flash(t('common.saved'), 'success')
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
    return render_device_template('manage/theme.html', org=org,
                           themes=AVAILABLE_THEMES)


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
                           fields=fields,
                           image_uploads=image_uploads,
                           theme_name=AVAILABLE_THEMES[theme]['name'])
