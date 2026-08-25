"""Discussion routes: spaces, topics, replies, reactions, follows,
moderation, flags."""

import sqlalchemy as sa
from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from app.extensions import db
from app.models.discussion import (Flag, Reaction, Reply, Space, Topic,
                                   TopicFollow, REACTION_EMOJI)
from app.platform.authz import can, is_org_member, org_required, require
from app.platform.errors import ValidationError
from app.platform.i18n import t
from app.platform.logger import get_logger
from app.platform.notify import (notify_moderation, notify_reply_created,
                                 notify_topic_mentions)
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


def _topic_or_404(space, topic_id) -> Topic:
    topic = db.session.get(Topic, topic_id)
    if topic is None or topic.space_id != space.id:
        abort(404)
    if topic.is_hidden and not can('content.moderate'):
        abort(404)
    return topic


def _moderator_filter(query):
    if can('content.moderate'):
        return query
    return query.filter(Topic.is_hidden.is_(False))


@bp.route('/')
@org_required
def index():
    spaces = _visible_spaces()
    space_ids = [space.id for space in spaces]
    q = request.args.get('q', '').strip()
    recent = []
    if space_ids:
        query = _moderator_filter(
            Topic.query.filter(Topic.space_id.in_(space_ids)))
        if q:
            query = query.filter(sa.or_(Topic.title.ilike(f'%{q}%'),
                                        Topic.body.ilike(f'%{q}%')))
        recent = (query.order_by(Topic.last_activity_at.desc())
                  .limit(20).all())
    return render_site(['discussions.html'], spaces=spaces,
                       recent_topics=recent, q=q)


@bp.route('/<slug>')
@org_required
def space(slug):
    space = _space_or_404(slug)
    q = request.args.get('q', '').strip()
    query = _moderator_filter(Topic.query.filter_by(space_id=space.id))
    if q:
        query = query.filter(sa.or_(Topic.title.ilike(f'%{q}%'),
                                    Topic.body.ilike(f'%{q}%')))
    topics = (query.order_by(Topic.is_pinned.desc(),
                             Topic.last_activity_at.desc())
              .limit(100).all())
    return render_site(['discussion-space.html'], space=space,
                       topics=topics, q=q)


@bp.route('/<slug>/new', methods=['POST'])
@org_required
@require('discuss')
def new_topic(slug):
    space = _space_or_404(slug)
    topic = Topic(space_id=space.id, title=request.form.get('title', ''),
                  body=request.form.get('body', ''))
    topic.stamp_audit()
    try:
        topic.save()
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
        return redirect(url_for('discussions.space', slug=space.slug))
    TopicFollow.follow(current_user.id, topic)
    notify_topic_mentions(topic)
    log.info('topic_created', topic_id=topic.id, org_id=g.org.id)
    return redirect(topic.url)


@bp.route('/<slug>/<int:topic_id>')
@org_required
def topic(slug, topic_id):
    space = _space_or_404(slug)
    topic = _topic_or_404(space, topic_id)

    replies = Reply.query.filter_by(topic_id=topic.id) \
        .order_by(Reply.created_at).all()
    if not can('content.moderate'):
        visible_ids = {reply.id for reply in replies if not reply.is_hidden}
        replies = [reply for reply in replies
                   if not reply.is_hidden or reply.id in visible_ids]

    top_level = [reply for reply in replies if reply.parent_id is None]
    children: dict = {}
    for reply in replies:
        if reply.parent_id:
            children.setdefault(reply.parent_id, []).append(reply)

    reactions = {
        'topic': Reaction.counts_for('topic', [topic.id]).get(topic.id, {}),
        'reply': Reaction.counts_for('reply', [reply.id for reply in replies]),
    }
    following = (current_user.is_authenticated and
                 TopicFollow.is_following(current_user.id, topic.id))
    return render_site(['discussion-topic.html'], space=space,
                       topic=topic, top_level=top_level, children=children,
                       reactions=reactions, following=following,
                       emoji_set=REACTION_EMOJI)


@bp.route('/<slug>/<int:topic_id>/reply', methods=['POST'])
@org_required
@require('discuss')
def reply(slug, topic_id):
    space = _space_or_404(slug)
    topic = _topic_or_404(space, topic_id)
    if topic.is_locked and not can('content.moderate'):
        flash(t('discussions.locked'), 'error')
        return redirect(topic.url)

    reply = Reply(topic_id=topic.id, body=request.form.get('body', ''),
                  parent_id=request.form.get('parent_id', type=int) or None)
    reply.stamp_audit()
    try:
        reply.save()
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
        return redirect(topic.url)

    topic.reply_count = Reply.query.filter_by(topic_id=topic.id).count()
    topic.touch()
    db.session.commit()
    TopicFollow.follow(current_user.id, topic)
    notify_reply_created(reply)
    return redirect(f'{topic.url}#reply-{reply.id}')


@bp.route('/<slug>/<int:topic_id>/edit', methods=['POST'])
@org_required
@login_required
def edit_topic(slug, topic_id):
    space = _space_or_404(slug)
    topic = _topic_or_404(space, topic_id)
    if not topic.can_edit():
        abort(403)
    topic.title = request.form.get('title', topic.title)
    topic.body = request.form.get('body', topic.body)
    topic.stamp_audit()
    try:
        topic.save()
        flash(t('common.saved'), 'success')
    except ValidationError as e:
        db.session.rollback()
        flash(e.message, 'error')
    return redirect(topic.url)


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
    return redirect(f'{reply.topic.url}#reply-{reply.id}')


@bp.route('/<slug>/<int:topic_id>/delete', methods=['POST'])
@org_required
@login_required
def delete_topic(slug, topic_id):
    space = _space_or_404(slug)
    topic = _topic_or_404(space, topic_id)
    if not topic.can_edit():
        abort(403)
    topic.delete()
    flash(t('discussions.topic_deleted'), 'success')
    return redirect(url_for('discussions.space', slug=space.slug))


@bp.route('/replies/<int:reply_id>/delete', methods=['POST'])
@org_required
@login_required
def delete_reply(reply_id):
    reply = db.session.get(Reply, reply_id)
    if reply is None:
        abort(404)
    if not reply.can_edit():
        abort(403)
    topic = reply.topic
    reply.delete()
    topic.reply_count = Reply.query.filter_by(topic_id=topic.id).count()
    db.session.commit()
    return redirect(topic.url)


# --- Moderation --------------------------------------------------------------

@bp.route('/<slug>/<int:topic_id>/lock', methods=['POST'])
@org_required
@require('content.moderate')
def lock_topic(slug, topic_id):
    topic = _topic_or_404(_space_or_404(slug), topic_id)
    topic.is_locked = not topic.is_locked
    topic.save()
    if topic.is_locked:
        notify_moderation(topic, 'locked')
    return redirect(topic.url)


@bp.route('/<slug>/<int:topic_id>/pin', methods=['POST'])
@org_required
@require('content.moderate')
def pin_topic(slug, topic_id):
    topic = _topic_or_404(_space_or_404(slug), topic_id)
    topic.is_pinned = not topic.is_pinned
    topic.save()
    return redirect(topic.url)


@bp.route('/<slug>/<int:topic_id>/hide', methods=['POST'])
@org_required
@require('content.moderate')
def hide_topic(slug, topic_id):
    topic = _topic_or_404(_space_or_404(slug), topic_id)
    topic.is_hidden = not topic.is_hidden
    topic.save()
    if topic.is_hidden:
        notify_moderation(topic, 'hidden')
    return redirect(url_for('discussions.space', slug=slug))


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
    return redirect(reply.topic.url)


# --- Reactions, follows, flags --------------------------------------------------

@bp.route('/react', methods=['POST'])
@org_required
@require('discuss')
def react():
    target_type = request.form.get('target_type', '')
    target_id = request.form.get('target_id', type=int)
    emoji = request.form.get('emoji', '👍')
    model = Topic if target_type == 'topic' else Reply
    target = db.session.get(model, target_id) if target_id else None
    if target is None:
        abort(404)
    try:
        Reaction.toggle(current_user.id, target_type, target_id, emoji)
    except ValidationError as e:
        flash(e.message, 'error')
    topic = target if target_type == 'topic' else target.topic
    return redirect(topic.url)


@bp.route('/<slug>/<int:topic_id>/follow', methods=['POST'])
@org_required
@require('discuss')
def follow(slug, topic_id):
    topic = _topic_or_404(_space_or_404(slug), topic_id)
    if TopicFollow.is_following(current_user.id, topic.id):
        TopicFollow.unfollow(current_user.id, topic)
        flash(t('discussions.unfollowed'), 'success')
    else:
        TopicFollow.follow(current_user.id, topic)
        flash(t('discussions.followed'), 'success')
    return redirect(topic.url)


@bp.route('/flag', methods=['POST'])
@org_required
@require('discuss')
def flag():
    target_type = request.form.get('target_type', '')
    target_id = request.form.get('target_id', type=int)
    model = Topic if target_type == 'topic' else Reply
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
    topic = target if target_type == 'topic' else target.topic
    return redirect(topic.url)
