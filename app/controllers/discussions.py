"""Discussion routes: groups, posts, replies, reactions, follows,
moderation, flags. URLs stay /discussions/<group>/<post_id>."""

import sqlalchemy as sa
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
from flask_login import current_user, login_required

from app.extensions import db
from app.models.discussion import (
    REACTION_EMOJI,
    DiscussionGroup,
    Flag,
    Post,
    PostFollow,
    Reaction,
    Reply,
)
from app.platform.authz import can, org_required, require
from app.platform.errors import ValidationError
from app.platform.i18n import t
from app.platform.logger import get_logger
from app.platform.notify import (
    notify_moderation,
    notify_post_mentions,
    notify_reply_created,
)
from app.platform.theming import render_gate, render_site

bp = Blueprint('discussions', __name__, url_prefix='/discussions')
log = get_logger()


def _all_groups():
    """Every group, in listing order. Gated groups are teased, not hidden:
    listings show name/description/count with a lock, but post queries must
    stay restricted to readable groups (titles are gated content)."""
    return DiscussionGroup.query.order_by(DiscussionGroup.position,
                                          DiscussionGroup.name).all()


def _group_or_404(slug) -> DiscussionGroup:
    """Existence only. Readability is the caller's decision: page views gate
    unreadable groups (tease-don't-hide); write routes sit behind
    require('discuss'), which members-only readability already implies."""
    group = DiscussionGroup.get_by_slug(slug)
    if group is None:
        abort(404)
    return group


def _post_or_404(group, post_id) -> Post:
    post = db.session.get(Post, post_id)
    if post is None or post.group_id != group.id:
        abort(404)
    if post.is_hidden and not can('content.moderate'):
        abort(404)
    return post


def _moderator_filter(query):
    if can('content.moderate'):
        return query
    return query.filter(Post.is_hidden.is_(False))


@bp.route('/')
@org_required
def index():
    """The groups directory. Recent activity lives on the Home feed; this
    page answers "what groups exist and how alive are they"."""
    if not DiscussionGroup.area_readable_by_current_visitor():
        # The org gated the whole area: one gate, no group names teased.
        return render_gate(t('discussions.title'))
    groups = _all_groups()
    if not g.org.teases_gated_content():
        # Teasing is off for this org: gated groups vanish from the listing.
        groups = [group for group in groups
                  if group.readable_by_current_visitor()]
    # Post titles are gated content: recents and search only ever query
    # readable groups, while the listing (when teasing) shows gated ones
    # by name only.
    group_ids = [group.id for group in groups
                 if group.readable_by_current_visitor()]
    q = request.args.get('q', '').strip()
    latest_by_group: dict = {}
    search_results = []
    recent = []
    if group_ids:
        base = _moderator_filter(
            Post.query.filter(Post.group_id.in_(group_ids)))
        recent = (base.order_by(Post.last_activity_at.desc())
                  .limit(50).all())
        for post in recent:
            latest_by_group.setdefault(post.group_id, post)
        if q:
            search_results = (base.filter(
                sa.or_(Post.title.ilike(f'%{q}%'),
                       Post.body.ilike(f'%{q}%')))
                .order_by(Post.last_activity_at.desc()).limit(20).all())
    # recent_posts keeps the public theme template working for visitors.
    return render_site(['discussions.html'], context_name='application', groups=groups,
                       latest_by_group=latest_by_group,
                       search_results=search_results, q=q,
                       recent_posts=(search_results if q else recent[:20]))


@bp.route('/<slug>')
@org_required
def group(slug):
    group = _group_or_404(slug)
    if not group.readable_by_current_visitor():
        return render_gate(group.name, kind=t('discussions.group'))
    q = request.args.get('q', '').strip()
    query = _moderator_filter(Post.query.filter_by(group_id=group.id))
    if q:
        query = query.filter(sa.or_(Post.title.ilike(f'%{q}%'),
                                    Post.body.ilike(f'%{q}%')))
    posts = (query.order_by(Post.is_pinned.desc(),
                             Post.last_activity_at.desc())
              .limit(100).all())
    return render_site(['discussion-group.html'], context_name='application', group=group,
                       posts=posts, q=q)


@bp.route('/<slug>/new', methods=['POST'])
@org_required
@require('discuss')
def new_post(slug):
    group = _group_or_404(slug)
    post = Post(group_id=group.id, title=request.form.get('title', ''),
                  body=request.form.get('body', ''))
    post.stamp_audit()
    try:
        post.save()
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
        return redirect(url_for('discussions.group', slug=group.slug))
    PostFollow.follow(current_user.id, post)
    notify_post_mentions(post)
    log.info('discussion_post_created', post_id=post.id, org_id=g.org.id)
    return redirect(post.url)


@bp.route('/<slug>/<int:post_id>')
@org_required
def post(slug, post_id):
    group = _group_or_404(slug)
    if not group.readable_by_current_visitor():
        # Gate on the group without confirming the post: its title is gated.
        return render_gate(group.name, kind=t('discussions.group'))
    post = _post_or_404(group, post_id)

    replies = Reply.query.filter_by(post_id=post.id) \
        .order_by(Reply.created_at).all()
    if not can('content.moderate'):
        replies = [r for r in replies if not r.is_hidden]

    top_level = [r for r in replies if r.parent_id is None]
    children: dict = {}
    for reply in replies:
        if reply.parent_id:
            children.setdefault(reply.parent_id, []).append(reply)

    reactions = {
        'post': Reaction.counts_for('post', [post.id]).get(post.id, {}),
        'reply': Reaction.counts_for('reply', [r.id for r in replies]),
    }
    sort = request.args.get('sort', 'oldest')
    if sort == 'newest':
        top_level.reverse()

    latest_in_group = (_moderator_filter(
        Post.query.filter(Post.group_id == group.id, Post.id != post.id))
        .order_by(Post.last_activity_at.desc()).limit(5).all())

    following = (current_user.is_authenticated and
                 PostFollow.is_following(current_user.id, post.id))
    return render_site(['discussion-post.html'], context_name='application', group=group,
                       post=post, top_level=top_level, children=children,
                       reactions=reactions, following=following,
                       sort=sort, latest_in_group=latest_in_group,
                       emoji_set=REACTION_EMOJI)


@bp.route('/<slug>/<int:post_id>/reply', methods=['POST'])
@org_required
@require('discuss')
def reply(slug, post_id):
    group = _group_or_404(slug)
    post = _post_or_404(group, post_id)
    if post.is_locked and not can('content.moderate'):
        flash(t('discussions.locked'), 'error')
        return redirect(post.url)

    reply = Reply(post_id=post.id, body=request.form.get('body', ''),
                  parent_id=request.form.get('parent_id', type=int) or None)
    reply.stamp_audit()
    try:
        reply.save()
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
        return redirect(post.url)

    post.recount_replies()
    post.touch()
    db.session.commit()
    PostFollow.follow(current_user.id, post)
    notify_reply_created(reply)
    return redirect(f'{post.url}#reply-{reply.id}')


@bp.route('/<slug>/<int:post_id>/edit', methods=['POST'])
@org_required
@login_required
def edit_post(slug, post_id):
    group = _group_or_404(slug)
    post = _post_or_404(group, post_id)
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


@bp.route('/replies/<int:reply_id>/edit', methods=['POST'])
@org_required
@login_required
def edit_reply(reply_id):
    reply = db.session.get(Reply, reply_id)
    if reply is None:
        abort(404)
    if not reply.can_edit():
        abort(403)
    reply.body = request.form.get('body', reply.body)
    reply.stamp_audit()
    try:
        reply.save()
        flash(t('common.saved'), 'success')
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
    return redirect(f'{reply.post.url}#reply-{reply.id}')


@bp.route('/<slug>/<int:post_id>/delete', methods=['POST'])
@org_required
@login_required
def delete_post(slug, post_id):
    group = _group_or_404(slug)
    post = _post_or_404(group, post_id)
    if not post.can_edit():
        abort(403)
    post.delete()
    flash(t('discussions.post_deleted'), 'success')
    return redirect(url_for('discussions.group', slug=group.slug))


@bp.route('/replies/<int:reply_id>/delete', methods=['POST'])
@org_required
@login_required
def delete_reply(reply_id):
    reply = db.session.get(Reply, reply_id)
    if reply is None:
        abort(404)
    if not reply.can_edit():
        abort(403)
    post = reply.post
    reply.delete()
    post.recount_replies()
    db.session.commit()
    return redirect(post.url)


# --- Moderation --------------------------------------------------------------

@bp.route('/<slug>/<int:post_id>/lock', methods=['POST'])
@org_required
@require('content.moderate')
def lock_post(slug, post_id):
    post = _post_or_404(_group_or_404(slug), post_id)
    post.is_locked = not post.is_locked
    post.save()
    if post.is_locked:
        notify_moderation(post, 'locked')
    return redirect(post.url)


@bp.route('/<slug>/<int:post_id>/pin', methods=['POST'])
@org_required
@require('content.moderate')
def pin_post(slug, post_id):
    post = _post_or_404(_group_or_404(slug), post_id)
    post.is_pinned = not post.is_pinned
    post.save()
    return redirect(post.url)


@bp.route('/<slug>/<int:post_id>/hide', methods=['POST'])
@org_required
@require('content.moderate')
def hide_post(slug, post_id):
    post = _post_or_404(_group_or_404(slug), post_id)
    post.is_hidden = not post.is_hidden
    post.save()
    if post.is_hidden:
        notify_moderation(post, 'hidden')
    return redirect(url_for('discussions.group', slug=slug))


@bp.route('/replies/<int:reply_id>/hide', methods=['POST'])
@org_required
@require('content.moderate')
def hide_reply(reply_id):
    reply = db.session.get(Reply, reply_id)
    if reply is None:
        abort(404)
    reply.is_hidden = not reply.is_hidden
    reply.save()
    if reply.is_hidden:
        notify_moderation(reply, 'hidden')
    return redirect(reply.post.url)


# --- Reactions, follows, flags --------------------------------------------------

def _reaction_target(target_type, target_id):
    model = Post if target_type == 'post' else Reply
    return db.session.get(model, target_id) if target_id else None


@bp.route('/react', methods=['POST'])
@org_required
@require('discuss')
def react():
    target_type = request.form.get('target_type', '')
    target_id = request.form.get('target_id', type=int)
    emoji = request.form.get('emoji', '👍')
    target = _reaction_target(target_type, target_id)
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
    post = _post_or_404(_group_or_404(slug), post_id)
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
    target = _reaction_target(target_type, target_id)
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
