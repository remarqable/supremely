"""Discussions: Organization -> Groups -> Posts -> Replies (one level).

Vocabulary: a Group is the container ("General", "Product Feedback"); a
Post is the conversation someone starts (universal social vocabulary);
Replies hang under it. Published content never uses the word "post" —
see app/models/content.py. Durable community conversation, not realtime
chat — which is why it isn't called "channels".
"""

import re

from flask_login import current_user

from app.extensions import db
from app.platform.authz import VISIBILITY_LEVELS
from app.platform.errors import ValidationError

from .base import (
    AuditMixin,
    BaseModel,
    OrgScoped,
    reject_control_characters,
    scoped_to_own_org,
    utcnow,
)
from .types import BigIntFK, TZDateTime

# Generous for a forum post, but bounded: every view re-renders the body
# through Markdown and the sanitiser, so an unbounded one is a way to make
# a public page expensive to serve.
BODY_MAX = 64_000


class DiscussionGroup(OrgScoped, BaseModel):
    """The container. Code name is scoped (not bare `Group`) because the
    console roadmap reserves plain "groups" for member/access groups."""
    __tablename__ = 'discussion_group'

    # Org-wide discussions visibility (org.settings['discussions_visibility']):
    #   per_group — each group's own visibility applies (the default)
    #   public    — the whole area is public, group settings notwithstanding
    #   members   — the whole area is members-only
    AREA_VISIBILITIES = ('per_group', 'public', 'members')

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
        if len(self.name) > 100:
            raise ValidationError('Group name too long (max 100 chars)')
        if len(self.description or '') > 500:
            raise ValidationError('Description too long (max 500 chars)')
        if not re.fullmatch(r'[a-z0-9]([a-z0-9-]{0,98})?', self.slug):
            raise ValidationError('Slug must be lowercase letters, numbers, hyphens')
        if self.visibility not in VISIBILITY_LEVELS:
            raise ValidationError('Invalid visibility')
        existing = scoped_to_own_org(
            DiscussionGroup.query.filter_by(slug=self.slug), self).first()
        if existing and existing.id != self.id:
            raise ValidationError('A group with that slug already exists')

    @classmethod
    def area_visibility(cls) -> str:
        """The org-wide discussions setting for the current tenant."""
        from flask import g
        org = getattr(g, 'org', None)
        value = org.setting('discussions_visibility') if org else None
        return value if value in cls.AREA_VISIBILITIES else 'per_group'

    @classmethod
    def area_readable_by_current_visitor(cls) -> bool:
        """Can the current visitor see the discussions area at all? False
        only when the org gated the whole area and the visitor is neither a
        member nor a platform admin."""
        from app.platform.authz import is_org_member
        if is_org_member() or (current_user.is_authenticated
                               and current_user.is_platform_admin):
            return True
        return cls.area_visibility() != 'members'

    def readable_by_current_visitor(self) -> bool:
        from app.platform.authz import is_org_member
        if is_org_member() or (current_user.is_authenticated
                               and current_user.is_platform_admin):
            return True
        area = self.area_visibility()
        if area != 'per_group':
            return area == 'public'
        return self.visibility == 'public'

    @classmethod
    def get_by_slug(cls, slug: str):
        return cls.query.filter_by(slug=(slug or '').strip().lower()).first()

    def post_count(self) -> int:
        return Post.query.filter_by(group_id=self.id,
                                     is_hidden=False).count()


class Post(OrgScoped, AuditMixin, BaseModel):
    __tablename__ = 'discussion_post'

    group_id = db.Column(BigIntFK,
                         db.ForeignKey('discussion_group.id', ondelete='CASCADE'),
                         nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False, default='')
    is_locked = db.Column(db.Boolean, nullable=False, default=False)
    is_pinned = db.Column(db.Boolean, nullable=False, default=False)
    is_hidden = db.Column(db.Boolean, nullable=False, default=False)
    # Provisioning seed marker: shown as a quiet badge to the OWNER only,
    # so they know which starter posts to make their own. Members never see
    # it, and the post is otherwise a completely ordinary post.
    is_seeded = db.Column(db.Boolean, nullable=False, default=False)
    reply_count = db.Column(db.Integer, nullable=False, default=0)
    last_activity_at = db.Column(TZDateTime, nullable=False, default=utcnow)

    group = db.relationship('DiscussionGroup', lazy='select')
    replies = db.relationship('Reply', back_populates='post', lazy='select',
                              cascade='all, delete-orphan',
                              order_by='Reply.created_at')

    __table_args__ = (
        db.Index('ix_discussion_post_group_activity',
                 'group_id', 'last_activity_at'),
    )

    def validate(self):
        self.title = (self.title or '').strip()
        if not self.title:
            raise ValidationError('Title is required')
        if len(self.title) > 200:
            raise ValidationError('Title too long (max 200 chars)')
        reject_control_characters(self.title, 'Title')
        if not (self.body or '').strip():
            raise ValidationError('Body is required')
        if len(self.body) > BODY_MAX:
            raise ValidationError(f'Body too long (max {BODY_MAX} chars)')

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
        self.reply_count = Reply.query.filter_by(post_id=self.id).count()
        return self


class Reply(OrgScoped, AuditMixin, BaseModel):
    __tablename__ = 'discussion_reply'

    post_id = db.Column(BigIntFK,
                        db.ForeignKey('discussion_post.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    # One-level threading: a reply may answer another top-level reply.
    parent_id = db.Column(BigIntFK,
                          db.ForeignKey('discussion_reply.id', ondelete='CASCADE'),
                          nullable=True)
    body = db.Column(db.Text, nullable=False)
    is_hidden = db.Column(db.Boolean, nullable=False, default=False)

    post = db.relationship('Post', back_populates='replies')
    parent = db.relationship('Reply', remote_side='Reply.id', lazy='select')

    def validate(self):
        if not (self.body or '').strip():
            raise ValidationError('Reply cannot be empty')
        if len(self.body) > BODY_MAX:
            raise ValidationError(f'Reply too long (max {BODY_MAX} chars)')
        if self.parent_id:
            # A query, not session.get: get() can answer from the identity
            # map without emitting SQL, and the tenant filter only runs on
            # a real query.
            parent = Reply.query.filter_by(id=self.parent_id).first()
            if parent is None or parent.post_id != self.post_id:
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
REACTION_TARGETS = ('post', 'reply')


class Reaction(OrgScoped, BaseModel):
    __tablename__ = 'discussion_reaction'

    user_id = db.Column(BigIntFK, db.ForeignKey('user.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    target_type = db.Column(db.String(10), nullable=False)     # post | reply
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
    def follow(cls, user_id: int, post: 'Post'):
        existing = cls.query.filter_by(user_id=user_id, post_id=post.id).first()
        if existing is None:
            cls(user_id=user_id, post_id=post.id, org_id=post.org_id).save()

    @classmethod
    def unfollow(cls, user_id: int, post: 'Post'):
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
    target_type = db.Column(db.String(10), nullable=False)     # post | reply
    target_id = db.Column(BigIntFK, nullable=False)
    reason = db.Column(db.String(500), nullable=True)
    resolved_at = db.Column(TZDateTime, nullable=True)

    def validate(self):
        if self.target_type not in REACTION_TARGETS:
            raise ValidationError('Invalid flag target')
        if len(self.reason or '') > 500:
            raise ValidationError('Reason too long (max 500 chars)')

    def target(self):
        model = Post if self.target_type == 'post' else Reply
        return db.session.get(model, self.target_id)

    def resolve(self):
        self.resolved_at = utcnow()
        return self.save()
