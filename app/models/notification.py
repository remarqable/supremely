"""In-app notifications. Email delivery happens through the jobs queue and
only when the installation has email configured."""

from app.extensions import db
from .base import BaseModel, OrgScoped, utcnow
from .types import BigIntFK, JSONColumn, TZDateTime

TYPES = ('reply.followed', 'reply.to_author', 'mention', 'moderation')


class Notification(OrgScoped, BaseModel):
    __tablename__ = 'notification'

    user_id = db.Column(BigIntFK, db.ForeignKey('user.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    type = db.Column(db.String(30), nullable=False)
    payload = db.Column(JSONColumn, nullable=False, default=dict)
    # payload keys: title, url, actor_name, snippet
    read_at = db.Column(TZDateTime, nullable=True)
    emailed_at = db.Column(TZDateTime, nullable=True)   # idempotency marker

    __table_args__ = (
        db.Index('ix_notification_user_read', 'user_id', 'read_at'),
    )

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    def mark_read(self):
        if self.read_at is None:
            self.read_at = utcnow()
            self.save()
        return self

    @classmethod
    def unread_count(cls, user_id: int) -> int:
        return cls.query.filter_by(user_id=user_id, read_at=None).count()

    @classmethod
    def for_user(cls, user_id: int, limit: int = 50):
        return (cls.query.filter_by(user_id=user_id)
                .order_by(cls.created_at.desc()).limit(limit).all())

    @classmethod
    def mark_all_read(cls, user_id: int, org_id: int) -> None:
        # Bulk UPDATE bypasses the do_orm_execute read filter, so scope by
        # org_id explicitly. unscoped() signals to the tenant guard that
        # scoping is handled here (both user_id AND org_id are pinned).
        import sqlalchemy as sa
        from app.platform.tenant import unscoped
        with unscoped():
            db.session.execute(
                sa.update(cls)
                .where(cls.user_id == user_id, cls.org_id == org_id,
                       cls.read_at.is_(None))
                .values(read_at=utcnow()))
            db.session.commit()

    @classmethod
    def notify(cls, *, user_id: int, org_id: int, type: str,
               title: str, url: str, actor_name: str = '',
               snippet: str = '') -> 'Notification':
        notification = cls(user_id=user_id, org_id=org_id, type=type, payload={
            'title': title, 'url': url, 'actor_name': actor_name,
            'snippet': snippet[:300],
        })
        notification.save()

        # Email is optional infrastructure: queue a delivery attempt; the
        # handler is a no-op when SMTP is absent.
        from app.platform.mailer import is_email_configured
        if is_email_configured():
            from app.platform.jobs import enqueue
            enqueue('notifications.email', org_id=org_id,
                    notification_id=notification.id)
        return notification
