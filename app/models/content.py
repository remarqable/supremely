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
from app.platform.authz import VISIBILITY_LEVELS
from app.platform.errors import ValidationError
from app.platform.theming import PAGE_TEMPLATE_RE

from .base import (
    LIKE_ESCAPE,
    AuditMixin,
    BaseModel,
    MarkdownBody,
    OrgScoped,
    escape_like,
    reject_control_characters,
    scoped_to_own_org,
    utcnow,
)
from .types import BigIntFK, JSONColumn, TZDateTime

# An article body is longer than a forum post, but still bounded: it is
# re-rendered through Markdown and the sanitiser on every view.
BODY_MAX = 500_000

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
        existing = scoped_to_own_org(
            Category.query.filter_by(slug=self.slug), self).first()
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


class Content(OrgScoped, AuditMixin, MarkdownBody, BaseModel):
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
    # Where a standalone page presents: 'site' renders through the theme
    # (marketing header/footer), 'community' inside the app-owned shell.
    # Presentation, never authorization — visibility gates either way.
    presentation = db.Column(db.String(10), nullable=False, default='site')
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
    VISIBILITIES = VISIBILITY_LEVELS   # single source: app.platform.authz
    PRESENTATIONS = ('site', 'community')

    def validate(self):
        from app.platform.content_types import CONTENT_TYPES
        self.title = (self.title or '').strip()
        self.slug = (self.slug or '').strip().lower()
        self.type = self.type or 'article'
        self.status = self.status or 'draft'
        self.visibility = self.visibility or 'public'

        if not self.title:
            raise ValidationError('Title is required')
        if len(self.title) > 200:
            raise ValidationError('Title too long (max 200 chars)')
        # This is the newsletter subject line (app/platform/newsletter.py).
        reject_control_characters(self.title, 'Title')
        # Columns declare these widths, but SQLite does not enforce them,
        # so an over-long value stores in development and raises in
        # production. The check has to live here.
        for field, label, limit in (('excerpt', 'Excerpt', 500),
                                    ('seo_title', 'SEO title', 200),
                                    ('seo_description', 'SEO description', 300),
                                    ('body', 'Body', BODY_MAX)):
            if len(getattr(self, field) or '') > limit:
                raise ValidationError(f'{label} too long (max {limit} chars)')
        if not re.fullmatch(r'[a-z0-9]([a-z0-9-]{0,198})?', self.slug):
            raise ValidationError('Slug must be lowercase letters, numbers, hyphens')
        if self.type not in CONTENT_TYPES:
            raise ValidationError(f'Unknown content type: {self.type}')
        if self.status not in self.STATUSES:
            raise ValidationError('Invalid status')
        if self.visibility not in self.VISIBILITIES:
            raise ValidationError('Invalid visibility')
        self.presentation = self.presentation or 'site'
        if self.presentation not in self.PRESENTATIONS:
            raise ValidationError('Invalid presentation')
        if self.tags is not None and not isinstance(self.tags, list):
            raise ValidationError('Tags must be a list')

        # `template` names one of the theme's page templates. It reaches
        # render_site()'s candidate list, which also searches app-owned
        # directories, so an unchecked value renders an application
        # template on a public URL. This is the structural half: a name,
        # never a path. Whether the theme actually provides that name is
        # checked where the value is accepted, in manage._content_from_form,
        # so a value stranded by a later theme switch does not block
        # unrelated edits to the same page.
        if self.template is not None:
            self.template = self.template.strip()
            if not self.template:
                self.template = None
            elif not PAGE_TEMPLATE_RE.fullmatch(self.template):
                raise ValidationError(
                    'Template must be lowercase letters, numbers and '
                    'hyphens: a template name, not a path')

        # Page slugs live at /<slug>, so they cannot shadow app routes or a
        # feed type's base segment (e.g. "blog", "events").
        if self.content_type.is_page:
            # Every archive base, not just the tenant's active ones: a page
            # must not take a slug that a later plugin install would shadow.
            bases = {ct.base.strip('/') for ct in CONTENT_TYPES.values()
                     if ct.has_archive}
            if self.slug in RESERVED_PAGE_SLUGS or self.slug in bases:
                raise ValidationError('That slug is reserved')

        # Unique per (org, type, slug): a page "about" and an article "about"
        # can coexist because they live at different URLs.
        existing = scoped_to_own_org(
            Content.query.filter_by(type=self.type, slug=self.slug),
            self).first()
        if existing and existing.id != self.id:
            raise ValidationError(
                f'A {self.content_type.singular.lower()} with that slug already exists')

    @property
    def content_type(self):
        from app.platform.content_types import get_content_type
        return get_content_type(self.type)

    @property
    def is_published(self) -> bool:
        return self.status == 'published'

    @property
    def permalink(self) -> str:
        ct = self.content_type
        if ct.is_page:
            return f'/{self.slug}'
        return f'{ct.base}/{self.slug}'

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

    @classmethod
    def section_visibility(cls, type_slug: str) -> str:
        """Org-wide lock for a whole content section (Manage → Content
        types): org.settings['section_visibility'] maps type slug ->
        'members'. Absent means public — items then decide individually."""
        from flask import g
        org = getattr(g, 'org', None)
        if org is None:
            return 'public'
        return (org.setting('section_visibility') or {}).get(type_slug,
                                                             'public')

    @classmethod
    def section_readable_by_current_visitor(cls, type_slug: str) -> bool:
        from app.platform.authz import is_member_or_platform_admin
        if is_member_or_platform_admin():
            return True
        return cls.section_visibility(type_slug) == 'public'

    def visible_to_current_visitor(self) -> bool:
        # A locked section gates every item in it, item settings
        # notwithstanding (mirrors the discussions area switch).
        if not Content.section_readable_by_current_visitor(self.type):
            return False
        if self.visibility == 'public':
            return True
        from app.platform.authz import is_member_or_platform_admin
        return is_member_or_platform_admin()

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
    def visible_query(cls, type_slug: str | None = None):
        """Published content the current visitor may see listed.

        The tease-don't-hide switch decides what "listed" means: on (the
        default) a gated item stays in the list as a locked title, and the
        template draws the padlock from can_view; off, it is filtered out
        here. One path, so a theme's grid and an archive page agree.
        """
        from flask import g

        from app.platform.authz import is_member_or_platform_admin
        query = cls.published_query(type_slug)
        org = getattr(g, 'org', None)
        if org and org.teases_gated_content():
            return query
        if is_member_or_platform_admin():
            return query
        return query.filter_by(visibility='public')

    # A theme asks for "recent articles" without saying how many; this is how
    # many it gets, and the ceiling on how many it can ask for. A front page
    # renders a grid, not an archive.
    FEED_LIMIT = 24

    @classmethod
    def feed(cls, type_slug: str, limit: int | None = None) -> list['Content']:
        """Published items of one type, newest first, for a theme template.

        Empty is normal: an unregistered type, a locked section or a site
        with nothing published all return [], never an error. The featured
        image and author are loaded with the rows, so iterating the result
        cannot turn into an N+1.
        """
        from sqlalchemy.orm import joinedload
        if not cls.section_readable_by_current_visitor(type_slug):
            return []
        if limit is None:
            limit = cls.FEED_LIMIT
        else:
            try:
                limit = min(int(limit), cls.FEED_LIMIT)
            except (TypeError, ValueError):
                limit = cls.FEED_LIMIT      # a theme's typo is not an error
        if limit < 1:
            return []
        return (cls.visible_query(type_slug)
                .options(joinedload(cls.featured_upload),
                         joinedload(cls.created_by))
                .limit(limit).all())

    @classmethod
    def feed_count(cls, type_slug: str) -> int:
        """How many items `feed()` is drawing from, for "view all" links."""
        if not cls.section_readable_by_current_visitor(type_slug):
            return 0
        return cls.visible_query(type_slug).count()

    @classmethod
    def upcoming_event(cls, public_only=False):
        """The next published event dated today or later. Event dates live in
        the structured `fields` JSON, so the (few) events are filtered in
        Python."""
        from datetime import date
        today = date.today().isoformat()
        if public_only and cls.section_visibility('event') != 'public':
            return None
        query = cls.published_query('event')
        if public_only:
            query = query.filter_by(visibility='public')
        events = [(event.fields.get('starts_on'), event)
                  for event in query.limit(50).all()
                  if (event.fields or {}).get('starts_on', '') >= today]
        return min(events, default=(None, None))[1]

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
        return (cls.visible_query(type_slug)
                .filter(sa.cast(cls.tags, sa.String)
                        .like(f'%"{escape_like(needle)}"%',
                              escape=LIKE_ESCAPE)))

    @classmethod
    def visible_in_category(cls, type_slug: str, category):
        return (cls.visible_query(type_slug)
                .filter(cls.categories.contains(category)))
