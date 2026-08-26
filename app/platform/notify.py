"""Notification fan-out for discussions, and the email delivery job."""

import re

from app.extensions import db
from app.models.notification import Notification
from app.platform.jobs import job
from app.platform.logger import get_logger

log = get_logger()

MENTION_RE = re.compile(r'@([A-Za-z0-9._+-]+)')


def _mentioned_user_ids(body: str, org_id: int) -> set[int]:
    """@handle resolves against org members' email local parts."""
    from app.models import Membership, User
    handles = {handle.lower() for handle in MENTION_RE.findall(body or '')}
    if not handles:
        return set()
    members = (User.query.join(Membership, Membership.user_id == User.id)
               .filter(Membership.org_id == org_id,
                       Membership.is_active.is_(True)).all())
    return {user.id for user in members
            if user.email.split('@')[0].lower() in handles}


def notify_reply_created(reply) -> None:
    """Mentions beat author-notify beat follower notifications; one
    notification per user; the actor never notifies themselves."""
    from app.models.discussion import PostFollow
    post = reply.post
    actor_id = reply.created_by_id
    actor_name = reply.author.name if reply.author else ''
    snippet = (reply.body or '')[:300]
    notified: set[int] = {actor_id} if actor_id else set()

    def send(user_id: int, type: str):
        if user_id in notified:
            return
        notified.add(user_id)
        Notification.notify(user_id=user_id, org_id=post.org_id, type=type,
                            title=post.title, url=post.url,
                            actor_name=actor_name, snippet=snippet)

    for user_id in _mentioned_user_ids(reply.body, post.org_id):
        send(user_id, 'mention')

    parent_author = reply.parent.created_by_id if reply.parent else None
    if parent_author:
        send(parent_author, 'reply.to_author')
    if post.created_by_id:
        send(post.created_by_id, 'reply.to_author')

    for user_id in PostFollow.follower_ids(post.id):
        send(user_id, 'reply.followed')


def notify_post_mentions(post) -> None:
    actor_name = post.author.name if post.author else ''
    for user_id in _mentioned_user_ids(post.body, post.org_id):
        if user_id == post.created_by_id:
            continue
        Notification.notify(user_id=user_id, org_id=post.org_id,
                            type='mention', title=post.title, url=post.url,
                            actor_name=actor_name,
                            snippet=(post.body or '')[:300])


def notify_moderation(target, action: str) -> None:
    """Tell the author their content was moderated."""
    if not target.created_by_id:
        return
    post = target if hasattr(target, 'group_id') else target.post
    Notification.notify(user_id=target.created_by_id, org_id=target.org_id,
                        type='moderation', title=post.title, url=post.url,
                        snippet=action)


@job('notifications.email')
def deliver_notification_email(payload: dict) -> None:
    """Best-effort email copy of an in-app notification. No-op without SMTP."""
    from app.models import User
    from app.platform.mailer import is_email_configured, send_email

    if not is_email_configured():
        return
    notification = db.session.get(Notification, payload.get('notification_id'))
    # emailed_at makes this idempotent: a retry or zombie-recovery after a
    # successful send must not re-mail. is_read short-circuits stale sends.
    if notification is None or notification.is_read or notification.emailed_at:
        return
    user = db.session.get(User, notification.user_id)
    if user is None or not user.is_active:
        return
    # The installation administrator signs in with a username, not an address
    # (see User.INSTALL_ADMIN_USERNAME). Handing that to smtplib raises, which
    # would fail the job and burn its retries on every notification.
    if not user.is_emailable:
        return

    data = notification.payload or {}
    subject = f"[{data.get('title', 'Notification')}]"
    body = (f"{data.get('actor_name', 'Someone')} — {notification.type}\n\n"
            f"{data.get('snippet', '')}\n\n{data.get('url', '')}\n")
    send_email(user.email, subject, body)
    from app.models.base import utcnow
    notification.emailed_at = utcnow()
    db.session.commit()
