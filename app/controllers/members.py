"""Member-facing routes: invitation acceptance (email-free), member
directory, profile, avatars."""

import io
import secrets

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    request,
    send_file,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user

from app.extensions import db
from app.middleware.ratelimit import rate_limit
from app.models import Membership, Post, User
from app.models.invitation import Invitation
from app.models.upload import open_bounded, sniff
from app.platform.authz import can, is_member_or_platform_admin, org_required
from app.platform.devices import render_device_template
from app.platform.errors import ValidationError
from app.platform.i18n import t
from app.platform.logger import get_logger
from app.platform.theming import render_site

bp = Blueprint('members', __name__)
log = get_logger()


# --- Invitations ----------------------------------------------------------------

@bp.route('/invite/<token>')
@org_required
def invite(token):
    invitation = Invitation.find_valid(token)
    if invitation is None:
        abort(404)
    already_member = (current_user.is_authenticated and
                      Membership.get(current_user.id, g.org.id) is not None)
    return render_device_template('members/invite.html', invitation=invitation,
                           token=token, already_member=already_member)


@bp.route('/invite/<token>/accept', methods=['POST'])
@org_required
@login_required
def accept_invite(token):
    invitation = Invitation.find_valid(token)
    if invitation is None:
        abort(404)
    invitation.accept(current_user)
    log.info('invitation_accepted', org_id=g.org.id, user_id=current_user.id)
    flash(t('members.welcome', org=g.org.name), 'success')
    return redirect('/')


@bp.route('/invite/<token>/signup', methods=['POST'])
@org_required
@rate_limit(limit=10, window=300)
def signup_via_invite(token):
    """Account creation carried by the invitation token, so acceptance is
    atomic with signup. No email delivery involved anywhere."""
    invitation = Invitation.find_valid(token)
    if invitation is None:
        abort(404)
    if current_user.is_authenticated:
        return redirect(url_for('members.invite', token=token))

    email = request.form.get('email', '').strip().lower()
    name = request.form.get('name', '').strip()
    password = request.form.get('password', '')

    if User.get_by_email(email) is not None:
        flash(t('members.account_exists'), 'error')
        return redirect(url_for('members.invite', token=token))
    try:
        user = User.create(email=email, name=name or email.split('@')[0],
                           password=password)
    except ValidationError as e:
        flash(e.message, 'error')
        return redirect(url_for('members.invite', token=token))

    invitation.accept(user)
    session.clear()
    login_user(user, remember=True)
    log.info('invitation_signup', org_id=g.org.id, user_id=user.id)
    flash(t('members.welcome', org=g.org.name), 'success')
    return redirect('/')


# --- Member directory -------------------------------------------------------------

@bp.route('/members')
@org_required
def directory():
    if not g.org.setting('member_directory'):
        abort(404)
    if not is_member_or_platform_admin():
        # Member data stays members-only; the gate explains instead of 404ing.
        from app.platform.i18n import t
        from app.platform.theming import render_gate
        return render_gate(t('members.directory_title'))
    member_list = (Membership.query
                   .filter_by(org_id=g.org.id, is_active=True)
                   .join(Membership.user)
                   .filter(User.is_active.is_(True))
                   .order_by(Membership.created_at).all())
    return render_site(['members.html'], context_name='application', members=member_list)


@bp.route('/members/<int:user_id>')
@org_required
def member(user_id):
    """One member's profile: name, avatar, bio, and their recent posts.

    Independent of the directory switch, because names already appear on
    every post; what the page adds is the bio and the post list, which are
    member data and so stay behind the same gate as the directory."""
    if not is_member_or_platform_admin():
        from app.platform.theming import render_gate
        return render_gate(t('members.profile_gate'))
    membership = Membership.get(user_id, g.org.id)
    if (membership is None or not membership.is_active
            or not membership.user.is_active):
        abort(404)
    posts = Post.recent_by_author(user_id, include_hidden=can('content.moderate'))
    return render_site(['member.html'], context_name='application',
                       member=membership.user, membership=membership,
                       posts=posts)


# --- Profile ------------------------------------------------------------------------

@bp.route('/profile', methods=['GET', 'POST'])
@login_required
@rate_limit(limit=10, window=300)
def profile():
    if request.method == 'POST':
        current_user.name = request.form.get('name', current_user.name)
        current_user.bio = request.form.get('bio', '').strip()[:2000] or None

        file = request.files.get('avatar')
        if file is not None and file.filename:
            try:
                _set_avatar(current_user, file)
            except ValidationError as e:
                db.session.rollback()
                flash(e.message, 'error')
                return render_device_template('members/profile.html')
        try:
            current_user.save()
            flash(t('common.saved'), 'success')
            return redirect(url_for('members.profile'))
        except ValidationError as e:
            db.session.rollback()
            flash(e.message, 'error')
    return render_device_template('members/profile.html')


AVATAR_EDGE = 400


def _set_avatar(user, file) -> None:
    from PIL import ImageOps

    from app.platform.storage import storage

    head = file.stream.read(5 * 1024 * 1024 + 1)
    if len(head) > 5 * 1024 * 1024:
        raise ValidationError('Avatar too large (max 5 MB)')
    sniffed = sniff(head)
    if sniffed is None or sniffed[0] not in ('image/png', 'image/jpeg',
                                             'image/webp'):
        raise ValidationError('Avatar must be a PNG, JPEG, or WebP image')

    # The result is a 400px square whatever came in, so ask for a reduced
    # decode. A JPEG can do it and drops from hundreds of megabytes to a
    # dozen; a PNG cannot, which is what the size ceiling is for.
    img = open_bounded(head, draft_to=AVATAR_EDGE)
    try:
        img = ImageOps.exif_transpose(img)
        if img.mode == 'P':
            img = img.convert('RGBA')
        img.thumbnail((AVATAR_EDGE, AVATAR_EDGE))
        out = io.BytesIO()
        img.save(out, 'WEBP', quality=85)
    except (OSError, ValueError) as exc:
        # A header can parse and the pixels still be missing, which is what
        # an upload cut short by a dropped connection looks like.
        raise ValidationError('That image could not be read in full') from exc
    out.seek(0)

    old_key = user.avatar_key
    user.avatar_key = f'avatars/{user.id}/{secrets.token_hex(8)}.webp'
    storage().save(user.avatar_key, out)
    if old_key:
        storage().delete(old_key)


def _avatar_is_visible(user) -> bool:
    """Whose picture this host is allowed to show.

    User is not org scoped, so nothing here is filtered for us. Without a
    check the route served any picture on any host, which leaked the faces
    of a private community to anyone, and answered 200 or 404 per id, which
    listed every account on the installation.
    """
    # Your own follows you. The top bar renders it on installation pages,
    # where no organization is resolved at all.
    if current_user.is_authenticated and current_user.id == user.id:
        return True
    org = getattr(g, 'org', None)
    if org is None:
        return False
    return Membership.query.filter_by(org_id=org.id, user_id=user.id,
                                      is_active=True).first() is not None


@bp.route('/avatars/<int:user_id>')
def avatar(user_id):
    user = db.session.get(User, user_id)
    if user is None or not user.avatar_key or not _avatar_is_visible(user):
        abort(404)
    from app.platform.storage import storage
    if not storage().exists(user.avatar_key):
        abort(404)
    # Private, not public: the answer now depends on who is asking, so a
    # shared cache must not hand one visitor's copy to the next.
    response = send_file(storage().open(user.avatar_key),
                         mimetype='image/webp', max_age=86400)
    response.headers['Cache-Control'] = 'private, max-age=86400'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
