"""Discussion routes: spaces, posts, comments, reactions, follows,
moderation, flags. URLs stay /discussions/<space>/<post_id>."""

import sqlalchemy as sa
from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from app.extensions import db
from app.models.discussion import (Comment, DiscussionPost, Flag, PostFollow,
                                   Reaction, Space, REACTION_EMOJI)
from app.platform.authz import can, is_org_member, org_required, require
from app.platform.errors import ValidationError
from app.platform.i18n import t
from app.platform.logger import get_logger
from app.platform.notify import (notify_comment_created, notify_moderation,
                                 notify_post_mentions)
from app.platform.theming import render_site

bp = Blueprint('discussions', __name__, url_prefix='/discussions')
log = get_logger()


def _visible_spaces():
    spaces = Space.query.order_by(Space.position, Space.name).all()
    return [space for space in spaces if space.readable_by_current_visitor()]


def _space_or_404(slug) -> Space:
    space = Space.get_by_slug(slug)
    if space is None or not space.readable_by_current_visitor():
        abort(404)
    return space


def _post_or_404(space, post_id) -> DiscussionPost:
    post = db.session.get(DiscussionPost, post_id)
    if post is None or post.space_id != space.id:
        abort(404)
    if post.is_hidden and not can('content.moderate'):
        abort(404)
    return post


def _moderator_filter(query):
    if can('content.moderate'):
        return query
    return query.filter(DiscussionPost.is_hidden.is_(False))


@bp.route('/')
@org_required
def index():
    spaces = _visible_spaces()
    space_ids = [space.id for space in spaces]
    q = request.args.get('q', '').strip()
    recent = []
    if space_ids:
        query = _moderator_filter(
            DiscussionPost.query.filter(DiscussionPost.space_id.in_(space_ids)))
        if q:
            query = query.filter(sa.or_(DiscussionPost.title.ilike(f'%{q}%'),
                                        DiscussionPost.body.ilike(f'%{q}%')))
        recent = (query.order_by(DiscussionPost.last_activity_at.desc())
                  .limit(20).all())
    return render_site(['discussions.html'], spaces=spaces,
                       recent_posts=recent, q=q)


@bp.route('/<slug>')
@org_required
def space(slug):
    space = _space_or_404(slug)
    q = request.args.get('q', '').strip()
    query = _moderator_filter(DiscussionPost.query.filter_by(space_id=space.id))
    if q:
        query = query.filter(sa.or_(DiscussionPost.title.ilike(f'%{q}%'),
                                    DiscussionPost.body.ilike(f'%{q}%')))
    posts = (query.order_by(DiscussionPost.is_pinned.desc(),
                            DiscussionPost.last_activity_at.desc())
             .limit(100).all())
    return render_site(['discussion-space.html'], space=space,
                       posts=posts, q=q)


@bp.route('/<slug>/new', methods=['POST'])
@org_required
@require('discuss')
def new_post(slug):
    space = _space_or_404(slug)
    post = DiscussionPost(space_id=space.id, title=request.form.get('title', ''),
                          body=request.form.get('body', ''))
    post.stamp_audit()
    try:
        post.save()
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
        return redirect(url_for('discussions.space', slug=space.slug))
    PostFollow.follow(current_user.id, post)
    notify_post_mentions(post)
    log.info('discussion_post_created', post_id=post.id, org_id=g.org.id)
    return redirect(post.url)


@bp.route('/<slug>/<int:post_id>')
@org_required
def post(slug, post_id):
    space = _space_or_404(slug)
    post = _post_or_404(space, post_id)

    comments = Comment.query.filter_by(post_id=post.id) \
        .order_by(Comment.created_at).all()
    if not can('content.moderate'):
        comments = [c for c in comments if not c.is_hidden]

    top_level = [c for c in comments if c.parent_id is None]
    children: dict = {}
    for comment in comments:
        if comment.parent_id:
            children.setdefault(comment.parent_id, []).append(comment)

    reactions = {
        'post': Reaction.counts_for('post', [post.id]).get(post.id, {}),
        'comment': Reaction.counts_for('comment', [c.id for c in comments]),
    }
    following = (current_user.is_authenticated and
                 PostFollow.is_following(current_user.id, post.id))
    return render_site(['discussion-post.html'], space=space,
                       post=post, top_level=top_level, children=children,
                       reactions=reactions, following=following,
                       emoji_set=REACTION_EMOJI)


@bp.route('/<slug>/<int:post_id>/comment', methods=['POST'])
@org_required
@require('discuss')
def comment(slug, post_id):
    space = _space_or_404(slug)
    post = _post_or_404(space, post_id)
    if post.is_locked and not can('content.moderate'):
        flash(t('discussions.locked'), 'error')
        return redirect(post.url)

    comment = Comment(post_id=post.id, body=request.form.get('body', ''),
                      parent_id=request.form.get('parent_id', type=int) or None)
    comment.stamp_audit()
    try:
        comment.save()
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
        return redirect(post.url)

    post.recount_comments()
    post.touch()
    db.session.commit()
    PostFollow.follow(current_user.id, post)
    notify_comment_created(comment)
    return redirect(f'{post.url}#comment-{comment.id}')


@bp.route('/<slug>/<int:post_id>/edit', methods=['POST'])
@org_required
@login_required
def edit_post(slug, post_id):
    space = _space_or_404(slug)
    post = _post_or_404(space, post_id)
    if not post.can_edit():
        abort(403)
    post.title = request.form.get('title', post.title)
    post.body = request.form.get('body', post.body)
    post.stamp_audit()
    try:
        post.save()
        flash(t('common.saved'), 'success')
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
    return redirect(post.url)


@bp.route('/comments/<int:comment_id>/edit', methods=['POST'])
@org_required
@login_required
def edit_comment(comment_id):
    comment = db.session.get(Comment, comment_id)
    if comment is None:
        abort(404)
    if not comment.can_edit():
        abort(403)
    comment.body = request.form.get('body', comment.body)
    comment.stamp_audit()
    try:
        comment.save()
        flash(t('common.saved'), 'success')
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
    return redirect(f'{comment.post.url}#comment-{comment.id}')


@bp.route('/<slug>/<int:post_id>/delete', methods=['POST'])
@org_required
@login_required
def delete_post(slug, post_id):
    space = _space_or_404(slug)
    post = _post_or_404(space, post_id)
    if not post.can_edit():
        abort(403)
    post.delete()
    flash(t('discussions.post_deleted'), 'success')
    return redirect(url_for('discussions.space', slug=space.slug))


@bp.route('/comments/<int:comment_id>/delete', methods=['POST'])
@org_required
@login_required
def delete_comment(comment_id):
    comment = db.session.get(Comment, comment_id)
    if comment is None:
        abort(404)
    if not comment.can_edit():
        abort(403)
    post = comment.post
    comment.delete()
    post.recount_comments()
    db.session.commit()
    return redirect(post.url)


# --- Moderation --------------------------------------------------------------

@bp.route('/<slug>/<int:post_id>/lock', methods=['POST'])
@org_required
@require('content.moderate')
def lock_post(slug, post_id):
    post = _post_or_404(_space_or_404(slug), post_id)
    post.is_locked = not post.is_locked
    post.save()
    if post.is_locked:
        notify_moderation(post, 'locked')
    return redirect(post.url)


@bp.route('/<slug>/<int:post_id>/pin', methods=['POST'])
@org_required
@require('content.moderate')
def pin_post(slug, post_id):
    post = _post_or_404(_space_or_404(slug), post_id)
    post.is_pinned = not post.is_pinned
    post.save()
    return redirect(post.url)


@bp.route('/<slug>/<int:post_id>/hide', methods=['POST'])
@org_required
@require('content.moderate')
def hide_post(slug, post_id):
    post = _post_or_404(_space_or_404(slug), post_id)
    post.is_hidden = not post.is_hidden
    post.save()
    if post.is_hidden:
        notify_moderation(post, 'hidden')
    return redirect(url_for('discussions.space', slug=slug))


@bp.route('/comments/<int:comment_id>/hide', methods=['POST'])
@org_required
@require('content.moderate')
def hide_comment(comment_id):
    comment = db.session.get(Comment, comment_id)
    if comment is None:
        abort(404)
    comment.is_hidden = not comment.is_hidden
    comment.save()
    if comment.is_hidden:
        notify_moderation(comment, 'hidden')
    return redirect(comment.post.url)


# --- Reactions, follows, flags --------------------------------------------------

@bp.route('/react', methods=['POST'])
@org_required
@require('discuss')
def react():
    target_type = request.form.get('target_type', '')
    target_id = request.form.get('target_id', type=int)
    emoji = request.form.get('emoji', '👍')
    model = DiscussionPost if target_type == 'post' else Comment
    target = db.session.get(model, target_id) if target_id else None
    if target is None:
        abort(404)
    try:
        Reaction.toggle(current_user.id, target_type, target_id, emoji)
    except ValidationError as e:
        flash(e.message, 'error')
    post = target if target_type == 'post' else target.post

    # HTMX: swap just the reaction bar in place; full-page fallback otherwise.
    if request.headers.get('HX-Request') == 'true':
        counts = Reaction.counts_for(target_type, [target_id]).get(target_id, {})
        return render_template('partials/_reaction_bar.html',
                               target_type=target_type, target_id=target_id,
                               counts=counts, emoji_set=REACTION_EMOJI)
    return redirect(post.url)


@bp.route('/<slug>/<int:post_id>/follow', methods=['POST'])
@org_required
@require('discuss')
def follow(slug, post_id):
    post = _post_or_404(_space_or_404(slug), post_id)
    if PostFollow.is_following(current_user.id, post.id):
        PostFollow.unfollow(current_user.id, post)
        flash(t('discussions.unfollowed'), 'success')
    else:
        PostFollow.follow(current_user.id, post)
        flash(t('discussions.followed'), 'success')
    return redirect(post.url)


@bp.route('/flag', methods=['POST'])
@org_required
@require('discuss')
def flag():
    target_type = request.form.get('target_type', '')
    target_id = request.form.get('target_id', type=int)
    model = DiscussionPost if target_type == 'post' else Comment
    target = db.session.get(model, target_id) if target_id else None
    if target is None:
        abort(404)
    flag = Flag(user_id=current_user.id, target_type=target_type,
                target_id=target_id,
                reason=request.form.get('reason', '').strip()[:500] or None)
    try:
        flag.save()
        flash(t('discussions.flagged'), 'success')
    except ValidationError as e:
        flash(e.message, 'error')
    post = target if target_type == 'post' else target.post
    return redirect(post.url)
