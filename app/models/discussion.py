"""Discussions: Organization -> Spaces -> Posts -> Comments (one level).

Reddit-style vocabulary: a discussion Post is a new conversation; Comments
hang under it. Durable community conversation, not realtime chat. ("Post"
here is deliberately distinct from published Content; see app/models/content.py.)
"""

import re

from flask_login import current_user

from app.extensions import db
from app.platform.errors import ValidationError

from .base import AuditMixin, BaseModel, OrgScoped, utcnow
from .types import BigIntFK, TZDateTime


class Space(OrgScoped, BaseModel):
    __tablename__ = 'discussion_space'

    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    # public: anyone can read; members: org members only. Posting is always
    # members-only.
    visibility = db.Column(db.String(10), nullable=False, default='members')
    position = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (
        db.UniqueConstraint('org_id', 'slug', name='uq_discussion_space_org_slug'),
    )

    def validate(self):
        self.name = (self.name or '').strip()
        self.slug = (self.slug or '').strip().lower()
        self.visibility = self.visibility or 'members'
        if not self.name:
            raise ValidationError('Space name is required')
        if not re.fullmatch(r'[a-z0-9]([a-z0-9-]{0,98})?', self.slug):
            raise ValidationError('Slug must be lowercase letters, numbers, hyphens')
        if self.visibility not in ('public', 'members'):
            raise ValidationError('Invalid visibility')
        existing = Space.query.filter_by(slug=self.slug).first()
        if existing and existing.id != self.id:
            raise ValidationError('A space with that slug already exists')

    def readable_by_current_visitor(self) -> bool:
        if self.visibility == 'public':
            return True
        from app.platform.authz import is_org_member
        return is_org_member() or (
            current_user.is_authenticated and current_user.is_platform_admin)

    @classmethod
    def get_by_slug(cls, slug: str):
        return cls.query.filter_by(slug=(slug or '').strip().lower()).first()

    def post_count(self) -> int:
        return DiscussionPost.query.filter_by(space_id=self.id,
                                              is_hidden=False).count()


class DiscussionPost(OrgScoped, AuditMixin, BaseModel):
    __tablename__ = 'discussion_post'

    space_id = db.Column(BigIntFK,
                         db.ForeignKey('discussion_space.id', ondelete='CASCADE'),
                         nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False, default='')
    is_locked = db.Column(db.Boolean, nullable=False, default=False)
    is_pinned = db.Column(db.Boolean, nullable=False, default=False)
    is_hidden = db.Column(db.Boolean, nullable=False, default=False)
    comment_count = db.Column(db.Integer, nullable=False, default=0)
    last_activity_at = db.Column(TZDateTime, nullable=False, default=utcnow)

    space = db.relationship('Space', lazy='select')
    comments = db.relationship('Comment', back_populates='post', lazy='select',
                               cascade='all, delete-orphan',
                               order_by='Comment.created_at')

    __table_args__ = (
        db.Index('ix_discussion_post_space_activity',
                 'space_id', 'last_activity_at'),
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
        return f'/discussions/{self.space.slug}/{self.id}'

    def can_edit(self) -> bool:
        from app.platform.authz import can
        return can('content.moderate') or (
            current_user.is_authenticated
            and self.created_by_id == current_user.id)

    def touch(self):
        self.last_activity_at = utcnow()
        return self

    def recount_comments(self):
        """Refresh the denormalized comment count from the comment table."""
        self.comment_count = Comment.query.filter_by(post_id=self.id).count()
        return self


class Comment(OrgScoped, AuditMixin, BaseModel):
    __tablename__ = 'discussion_comment'

    post_id = db.Column(BigIntFK,
                        db.ForeignKey('discussion_post.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    # One-level threading: a comment may answer another top-level comment.
    parent_id = db.Column(BigIntFK,
                          db.ForeignKey('discussion_comment.id', ondelete='CASCADE'),
                          nullable=True)
    body = db.Column(db.Text, nullable=False)
    is_hidden = db.Column(db.Boolean, nullable=False, default=False)

    post = db.relationship('DiscussionPost', back_populates='comments')
    parent = db.relationship('Comment', remote_side='Comment.id', lazy='select')

    def validate(self):
        if not (self.body or '').strip():
            raise ValidationError('Comment cannot be empty')
        if self.parent_id:
            parent = db.session.get(Comment, self.parent_id)
            if parent is None or parent.post_id != self.post_id:
                raise ValidationError('Invalid parent comment')
            if parent.parent_id is not None:
                raise ValidationError('Comments nest one level only')

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
REACTION_TARGETS = ('post', 'comment')


class Reaction(OrgScoped, BaseModel):
    __tablename__ = 'discussion_reaction'

    user_id = db.Column(BigIntFK, db.ForeignKey('user.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    target_type = db.Column(db.String(10), nullable=False)     # post | comment
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


class PostFollow(OrgScoped, BaseModel):
    __tablename__ = 'discussion_follow'

    user_id = db.Column(BigIntFK, db.ForeignKey('user.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    post_id = db.Column(BigIntFK,
                        db.ForeignKey('discussion_post.id', ondelete='CASCADE'),
                        nullable=False, index=True)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'post_id',
                            name='uq_discussion_follow_user_post'),
    )

    @classmethod
    def follow(cls, user_id: int, post: 'DiscussionPost'):
        existing = cls.query.filter_by(user_id=user_id, post_id=post.id).first()
        if existing is None:
            cls(user_id=user_id, post_id=post.id, org_id=post.org_id).save()

    @classmethod
    def unfollow(cls, user_id: int, post: 'DiscussionPost'):
        existing = cls.query.filter_by(user_id=user_id, post_id=post.id).first()
        if existing:
            existing.delete()

    @classmethod
    def is_following(cls, user_id: int, post_id: int) -> bool:
        return cls.query.filter_by(user_id=user_id, post_id=post_id).count() > 0

    @classmethod
    def follower_ids(cls, post_id: int) -> set[int]:
        import sqlalchemy as sa
        return set(db.session.scalars(
            sa.select(cls.user_id).where(cls.post_id == post_id)))


class Flag(OrgScoped, BaseModel):
    __tablename__ = 'discussion_flag'

    user_id = db.Column(BigIntFK, db.ForeignKey('user.id', ondelete='CASCADE'),
                        nullable=False)
    target_type = db.Column(db.String(10), nullable=False)     # post | comment
    target_id = db.Column(BigIntFK, nullable=False)
    reason = db.Column(db.String(500), nullable=True)
    resolved_at = db.Column(TZDateTime, nullable=True)

    def validate(self):
        if self.target_type not in REACTION_TARGETS:
            raise ValidationError('Invalid flag target')

    def target(self):
        model = DiscussionPost if self.target_type == 'post' else Comment
        return db.session.get(model, self.target_id)

    def resolve(self):
        self.resolved_at = utcnow()
        return self.save()
