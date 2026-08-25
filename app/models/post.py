"""Posts: the universal publishing system. Every Post has a Post Type
(default: article); structured types add validated fields stored as JSON."""

import json
import re

from app.extensions import db
from app.platform.errors import ValidationError
from .base import AuditMixin, BaseModel, OrgScoped, utcnow
from .types import BigIntFK, JSONColumn, TZDateTime

post_category = db.Table(
    'post_category',
    db.Column('post_id', BigIntFK,
              db.ForeignKey('post.id', ondelete='CASCADE'), primary_key=True),
    db.Column('category_id', BigIntFK,
              db.ForeignKey('category.id', ondelete='CASCADE'), primary_key=True),
)


class Category(OrgScoped, BaseModel):
    __tablename__ = 'category'

    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('org_id', 'slug', name='uq_category_org_slug'),
    )

    def validate(self):
        self.name = (self.name or '').strip()
        self.slug = (self.slug or '').strip().lower()
        if not self.name:
            raise ValidationError('Category name is required')
        if not re.fullmatch(r'[a-z0-9]([a-z0-9-]{0,98})?', self.slug):
            raise ValidationError('Category slug must be lowercase letters, numbers, hyphens')
        existing = Category.query.filter_by(slug=self.slug).first()
        if existing and existing.id != self.id:
            raise ValidationError('A category with that slug already exists')

    @classmethod
    def get_by_slug(cls, slug: str):
        return cls.query.filter_by(slug=(slug or '').strip().lower()).first()


class Post(OrgScoped, AuditMixin, BaseModel):
    __tablename__ = 'post'

    type = db.Column(db.String(50), nullable=False, default='article')
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False, default='')
    excerpt = db.Column(db.String(500), nullable=True)
    featured_upload_id = db.Column(BigIntFK,
                                   db.ForeignKey('upload.id', ondelete='SET NULL'),
                                   nullable=True)
    fields = db.Column(JSONColumn, nullable=False, default=dict)
    tags = db.Column(JSONColumn, nullable=False, default=list)
    status = db.Column(db.String(10), nullable=False, default='draft')
    visibility = db.Column(db.String(10), nullable=False, default='public')
    published_at = db.Column(TZDateTime, nullable=True)
    seo_title = db.Column(db.String(200), nullable=True)
    seo_description = db.Column(db.String(300), nullable=True)

    categories = db.relationship('Category', secondary=post_category,
                                 lazy='select')
    featured_upload = db.relationship('Upload', lazy='select')

    __table_args__ = (
        db.UniqueConstraint('org_id', 'slug', name='uq_post_org_slug'),
        db.Index('ix_post_org_status_published',
                 'org_id', 'status', 'published_at'),
    )

    STATUSES = ('draft', 'published', 'archived')
    VISIBILITIES = ('public', 'members')

    def validate(self):
        from app.platform.post_types import POST_TYPES
        self.title = (self.title or '').strip()
        self.slug = (self.slug or '').strip().lower()
        self.type = self.type or 'article'
        self.status = self.status or 'draft'
        self.visibility = self.visibility or 'public'

        if not self.title:
            raise ValidationError('Title is required')
        if len(self.title) > 200:
            raise ValidationError('Title too long (max 200 chars)')
        if not re.fullmatch(r'[a-z0-9]([a-z0-9-]{0,198})?', self.slug):
            raise ValidationError('Slug must be lowercase letters, numbers, hyphens')
        if self.type not in POST_TYPES:
            raise ValidationError(f'Unknown post type: {self.type}')
        if self.status not in self.STATUSES:
            raise ValidationError('Invalid status')
        if self.visibility not in self.VISIBILITIES:
            raise ValidationError('Invalid visibility')
        if self.tags is not None and not isinstance(self.tags, list):
            raise ValidationError('Tags must be a list')

        existing = Post.query.filter_by(slug=self.slug).first()
        if existing and existing.id != self.id:
            raise ValidationError('A post with that slug already exists')

    @property
    def post_type(self):
        from app.platform.post_types import get_post_type
        return get_post_type(self.type)

    @property
    def html(self) -> str:
        from app.platform.content import render_markdown
        return render_markdown(self.body)

    @property
    def is_published(self) -> bool:
        return self.status == 'published'

    @property
    def permalink(self) -> str:
        return f'/posts/{self.slug}'

    @property
    def author(self):
        return self.created_by

    def excerpt_or_summary(self, length: int = 200) -> str:
        if self.excerpt:
            return self.excerpt
        import nh3
        text = nh3.clean(self.html, tags=set())
        text = ' '.join(text.split())
        return text[:length] + ('…' if len(text) > length else '')

    def set_structured_fields(self, data: dict):
        self.fields = self.post_type.clean_fields(data)
        return self

    def publish(self):
        self.status = 'published'
        if self.published_at is None:
            self.published_at = utcnow()
        return self.save()

    def unpublish(self):
        self.status = 'draft'
        return self.save()

    def archive(self):
        self.status = 'archived'
        return self.save()

    def visible_to_current_visitor(self) -> bool:
        if self.visibility == 'public':
            return True
        from app.platform.authz import is_org_member
        from flask_login import current_user
        return is_org_member() or (
            current_user.is_authenticated and current_user.is_platform_admin)

    @classmethod
    def published_query(cls):
        return (cls.query.filter_by(status='published')
                .order_by(cls.published_at.desc()))

    @classmethod
    def published_by_slug(cls, slug: str):
        return cls.query.filter_by(slug=(slug or '').strip().lower(),
                                   status='published').first()

    @classmethod
    def with_tag(cls, tag: str):
        # Portable tag filtering: tags are stored as a JSON array of strings.
        needle = json.dumps(tag)[1:-1]
        return (cls.published_query()
                .filter(sa_cast_tags().ilike(f'%"{needle}"%')))


def sa_cast_tags():
    import sqlalchemy as sa
    return sa.cast(Post.tags, sa.String)
