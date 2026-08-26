"""Discussions: Organization -> Groups -> Topics -> Replies (one level).

Vocabulary: a Group is the container ("General", "Product Feedback"); a
Topic is the conversation someone starts; Replies hang under it. Durable
community conversation, not realtime chat — which is why it isn't called
"channels". ("Topic" is deliberately distinct from published Content; see
app/models/content.py.)
"""

import re

from flask_login import current_user

from app.extensions import db
from app.platform.errors import ValidationError

from .base import AuditMixin, BaseModel, OrgScoped, utcnow
from .types import BigIntFK, TZDateTime


class DiscussionGroup(OrgScoped, BaseModel):
    """The container. Code name is scoped (not bare `Group`) because the
    console roadmap reserves plain "groups" for member/access groups."""
    __tablename__ = 'discussion_group'

    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    # public: anyone can read; members: org members only. Posting is always
    # members-only.
    visibility = db.Column(db.String(10), nullable=False, default='members')
    position = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (
        db.UniqueConstraint('org_id', 'slug', name='uq_discussion_group_org_slug'),
    )

    def validate(self):
        self.name = (self.name or '').strip()
        self.slug = (self.slug or '').strip().lower()
        self.visibility = self.visibility or 'members'
        if not self.name:
            raise ValidationError('Group name is required')
        if not re.fullmatch(r'[a-z0-9]([a-z0-9-]{0,98})?', self.slug):
            raise ValidationError('Slug must be lowercase letters, numbers, hyphens')
        if self.visibility not in ('public', 'members'):
            raise ValidationError('Invalid visibility')
        existing = DiscussionGroup.query.filter_by(slug=self.slug).first()
        if existing and existing.id != self.id:
            raise ValidationError('A group with that slug already exists')

    def readable_by_current_visitor(self) -> bool:
        if self.visibility == 'public':
            return True
        from app.platform.authz import is_org_member
        return is_org_member() or (
            current_user.is_authenticated and current_user.is_platform_admin)

    @classmethod
    def get_by_slug(cls, slug: str):
        return cls.query.filter_by(slug=(slug or '').strip().lower()).first()

    def topic_count(self) -> int:
        return Topic.query.filter_by(group_id=self.id,
                                     is_hidden=False).count()


class Topic(OrgScoped, AuditMixin, BaseModel):
    __tablename__ = 'discussion_topic'

    group_id = db.Column(BigIntFK,
                         db.ForeignKey('discussion_group.id', ondelete='CASCADE'),
                         nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False, default='')
    is_locked = db.Column(db.Boolean, nullable=False, default=False)
    is_pinned = db.Column(db.Boolean, nullable=False, default=False)
    is_hidden = db.Column(db.Boolean, nullable=False, default=False)
    reply_count = db.Column(db.Integer, nullable=False, default=0)
    last_activity_at = db.Column(TZDateTime, nullable=False, default=utcnow)

    group = db.relationship('DiscussionGroup', lazy='select')
    replies = db.relationship('Reply', back_populates='topic', lazy='select',
                              cascade='all, delete-orphan',
                              order_by='Reply.created_at')

    __table_args__ = (
        db.Index('ix_discussion_topic_group_activity',
                 'group_id', 'last_activity_at'),
    )

    def validate(self):
        self.title = (self.title or '').strip()
        if not self.title:
            raise ValidationError('Title is required')
        if len(self.title) > 200:
            raise ValidationError('Title too long (max 200 chars)')
        if not (self.body or '').strip():
            raise ValidationError('Body is required')

    @property
    def html(self) -> str:
        from app.platform.content import render_markdown
        return render_markdown(self.body)

    @property
    def author(self):
        return self.created_by

    @property
    def url(self) -> str:
        return f'/discussions/{self.group.slug}/{self.id}'

    def can_edit(self) -> bool:
        from app.platform.authz import can
        return can('content.moderate') or (
            current_user.is_authenticated
            and self.created_by_id == current_user.id)

    def touch(self):
        self.last_activity_at = utcnow()
        return self

    def recount_replies(self):
        """Refresh the denormalized reply count from the reply table."""
        self.reply_count = Reply.query.filter_by(topic_id=self.id).count()
        return self


class Reply(OrgScoped, AuditMixin, BaseModel):
    __tablename__ = 'discussion_reply'

    topic_id = db.Column(BigIntFK,
                         db.ForeignKey('discussion_topic.id', ondelete='CASCADE'),
                         nullable=False, index=True)
    # One-level threading: a reply may answer another top-level reply.
    parent_id = db.Column(BigIntFK,
                          db.ForeignKey('discussion_reply.id', ondelete='CASCADE'),
                          nullable=True)
    body = db.Column(db.Text, nullable=False)
    is_hidden = db.Column(db.Boolean, nullable=False, default=False)

    topic = db.relationship('Topic', back_populates='replies')
    parent = db.relationship('Reply', remote_side='Reply.id', lazy='select')

    def validate(self):
        if not (self.body or '').strip():
            raise ValidationError('Reply cannot be empty')
        if self.parent_id:
            parent = db.session.get(Reply, self.parent_id)
            if parent is None or parent.topic_id != self.topic_id:
                raise ValidationError('Invalid parent reply')
            if parent.parent_id is not None:
                raise ValidationError('Replies nest one level only')

    @property
    def html(self) -> str:
        from app.platform.content import render_markdown
        return render_markdown(self.body)

    @property
    def author(self):
        return self.created_by

    def can_edit(self) -> bool:
        from app.platform.authz import can
        return can('content.moderate') or (
            current_user.is_authenticated
            and self.created_by_id == current_user.id)


REACTION_EMOJI = ('👍', '❤️', '🎉')
REACTION_TARGETS = ('topic', 'reply')


class Reaction(OrgScoped, BaseModel):
    __tablename__ = 'discussion_reaction'

    user_id = db.Column(BigIntFK, db.ForeignKey('user.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    target_type = db.Column(db.String(10), nullable=False)     # topic | reply
    target_id = db.Column(BigIntFK, nullable=False)
    emoji = db.Column(db.String(10), nullable=False, default='👍')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'target_type', 'target_id', 'emoji',
                            name='uq_discussion_reaction_user_target'),
        db.Index('ix_discussion_reaction_target', 'target_type', 'target_id'),
    )

    def validate(self):
        if self.target_type not in REACTION_TARGETS:
            raise ValidationError('Invalid reaction target')
        if self.emoji not in REACTION_EMOJI:
            raise ValidationError('Unknown reaction')

    @classmethod
    def toggle(cls, user_id: int, target_type: str, target_id: int,
               emoji: str) -> bool:
        """Returns True if the reaction now exists, False if removed."""
        existing = cls.query.filter_by(
            user_id=user_id, target_type=target_type,
            target_id=target_id, emoji=emoji).first()
        if existing:
            existing.delete()
            return False
        cls(user_id=user_id, target_type=target_type,
            target_id=target_id, emoji=emoji).save()
        return True

    @classmethod
    def counts_for(cls, target_type: str, target_ids: list[int]) -> dict:
        """{target_id: {emoji: count}} for a batch of targets."""
        import sqlalchemy as sa
        if not target_ids:
            return {}
        rows = db.session.execute(
            sa.select(cls.target_id, cls.emoji, sa.func.count())
            .where(cls.target_type == target_type,
                   cls.target_id.in_(target_ids))
            .group_by(cls.target_id, cls.emoji)).all()
        result: dict = {}
        for target_id, emoji, count in rows:
            result.setdefault(target_id, {})[emoji] = count
        return result


class TopicFollow(OrgScoped, BaseModel):
    __tablename__ = 'discussion_follow'

    user_id = db.Column(BigIntFK, db.ForeignKey('user.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    topic_id = db.Column(BigIntFK,
                         db.ForeignKey('discussion_topic.id', ondelete='CASCADE'),
                         nullable=False, index=True)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'topic_id',
                            name='uq_discussion_follow_user_topic'),
    )

    @classmethod
    def follow(cls, user_id: int, topic: 'Topic'):
        existing = cls.query.filter_by(user_id=user_id, topic_id=topic.id).first()
        if existing is None:
            cls(user_id=user_id, topic_id=topic.id, org_id=topic.org_id).save()

    @classmethod
    def unfollow(cls, user_id: int, topic: 'Topic'):
        existing = cls.query.filter_by(user_id=user_id, topic_id=topic.id).first()
        if existing:
            existing.delete()

    @classmethod
    def is_following(cls, user_id: int, topic_id: int) -> bool:
        return cls.query.filter_by(user_id=user_id, topic_id=topic_id).count() > 0

    @classmethod
    def follower_ids(cls, topic_id: int) -> set[int]:
        import sqlalchemy as sa
        return set(db.session.scalars(
            sa.select(cls.user_id).where(cls.topic_id == topic_id)))


class Flag(OrgScoped, BaseModel):
    __tablename__ = 'discussion_flag'

    user_id = db.Column(BigIntFK, db.ForeignKey('user.id', ondelete='CASCADE'),
                        nullable=False)
    target_type = db.Column(db.String(10), nullable=False)     # topic | reply
    target_id = db.Column(BigIntFK, nullable=False)
    reason = db.Column(db.String(500), nullable=True)
    resolved_at = db.Column(TZDateTime, nullable=True)

    def validate(self):
        if self.target_type not in REACTION_TARGETS:
            raise ValidationError('Invalid flag target')

    def target(self):
        model = Topic if self.target_type == 'topic' else Reply
        return db.session.get(model, self.target_id)

    def resolve(self):
        self.resolved_at = utcnow()
        return self.save()
