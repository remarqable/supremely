"""Content: the universal publishing model. Every published thing an
organization has -- pages, blog articles, events, and vertical types -- is a
Content row with a `type`. The type (see app/platform/content_types.py)
decides labels, URL base, template, and fields.

("Content" is deliberately distinct from a discussion Post; see
app/models/discussion.py.)
"""

import json
import re

from app.extensions import db
from app.platform.errors import ValidationError

from .base import AuditMixin, BaseModel, OrgScoped, utcnow
from .types import BigIntFK, JSONColumn, TZDateTime

content_category = db.Table(
    'content_category',
    db.Column('content_id', BigIntFK,
              db.ForeignKey('content.id', ondelete='CASCADE'), primary_key=True),
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


# Slugs a page-type content cannot use, because a page is served at /<slug>
# and must not shadow app routes. Feed-type bases are added dynamically.
RESERVED_PAGE_SLUGS = {
    'manage', 'dashboard', 'admin', 'auth', 'setup', 'static', 'files',
    'themes', 'launcher', 'health', 'discussions', 'members', 'newsletter',
    'feed', 'sitemap', 'profile', 'invite', 'avatars', 'subscribe',
    'unsubscribe',
}


class Content(OrgScoped, AuditMixin, BaseModel):
    __tablename__ = 'content'

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
    template = db.Column(db.String(50), nullable=True)   # page-type override
    seo_title = db.Column(db.String(200), nullable=True)
    seo_description = db.Column(db.String(300), nullable=True)

    categories = db.relationship('Category', secondary=content_category,
                                 lazy='select')
    featured_upload = db.relationship('Upload', lazy='select')

    __table_args__ = (
        db.UniqueConstraint('org_id', 'type', 'slug',
                            name='uq_content_org_type_slug'),
        db.Index('ix_content_org_type_status',
                 'org_id', 'type', 'status'),
    )

    STATUSES = ('draft', 'published', 'archived')
    VISIBILITIES = ('public', 'members')

    def validate(self):
        from app.platform.content_types import CONTENT_TYPES, feed_types
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
        if self.type not in CONTENT_TYPES:
            raise ValidationError(f'Unknown content type: {self.type}')
        if self.status not in self.STATUSES:
            raise ValidationError('Invalid status')
        if self.visibility not in self.VISIBILITIES:
            raise ValidationError('Invalid visibility')
        if self.tags is not None and not isinstance(self.tags, list):
            raise ValidationError('Tags must be a list')

        # Page slugs live at /<slug>, so they cannot shadow app routes or a
        # feed type's base segment (e.g. "blog", "events").
        if self.content_type.is_page:
            bases = {ct.base.strip('/') for ct in feed_types()}
            if self.slug in RESERVED_PAGE_SLUGS or self.slug in bases:
                raise ValidationError('That slug is reserved')

        # Unique per (org, type, slug): a page "about" and an article "about"
        # can coexist because they live at different URLs.
        existing = Content.query.filter_by(type=self.type,
                                           slug=self.slug).first()
        if existing and existing.id != self.id:
            raise ValidationError(
                f'A {self.content_type.singular.lower()} with that slug already exists')

    @property
    def content_type(self):
        from app.platform.content_types import get_content_type
        return get_content_type(self.type)

    @property
    def html(self) -> str:
        from app.platform.content import render_markdown
        return render_markdown(self.body)

    @property
    def is_published(self) -> bool:
        return self.status == 'published'

    @property
    def permalink(self) -> str:
        ct = self.content_type
        if ct.is_page:
            return f'/{self.slug}'
        return f'{ct.base}/{self.slug}'

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
        self.fields = self.content_type.clean_fields(data)
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
        from flask_login import current_user

        from app.platform.authz import is_org_member
        return is_org_member() or (
            current_user.is_authenticated and current_user.is_platform_admin)

    # --- queries -----------------------------------------------------------

    @classmethod
    def of_type(cls, type_slug: str):
        return cls.query.filter_by(type=type_slug)

    @classmethod
    def count_by_type(cls):
        """(type_slug, count) pairs for this org's content, tenant-scoped."""
        import sqlalchemy as sa

        from app.extensions import db
        return (db.session.query(cls.type, sa.func.count(cls.id))
                .group_by(cls.type).all())

    @classmethod
    def published_query(cls, type_slug: str | None = None):
        q = cls.query.filter_by(status='published')
        if type_slug:
            q = q.filter_by(type=type_slug)
        return q.order_by(cls.published_at.desc())

    @classmethod
    def published_by_slug(cls, type_slug: str, slug: str):
        return cls.query.filter_by(type=type_slug, status='published',
                                   slug=(slug or '').strip().lower()).first()

    @classmethod
    def published_page(cls, slug: str):
        return cls.published_by_slug('page', slug)

    @classmethod
    def with_tag(cls, type_slug: str, tag: str):
        import sqlalchemy as sa
        needle = json.dumps(tag)[1:-1]
        return (cls.published_query(type_slug)
                .filter(sa.cast(cls.tags, sa.String).like(f'%"{needle}"%')))
