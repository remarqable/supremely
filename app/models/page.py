"""Pages: durable website content, distinct from Posts."""

import re

from app.extensions import db
from app.platform.errors import ValidationError
from .base import AuditMixin, BaseModel, OrgScoped, utcnow


class Page(OrgScoped, AuditMixin, BaseModel):
    __tablename__ = 'page'

    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False, default='')
    status = db.Column(db.String(10), nullable=False, default='draft')
    # public | members
    visibility = db.Column(db.String(10), nullable=False, default='public')
    published_at = db.Column(db.DateTime(timezone=True), nullable=True)
    template = db.Column(db.String(50), nullable=False, default='page')
    seo_title = db.Column(db.String(200), nullable=True)
    seo_description = db.Column(db.String(300), nullable=True)

    __table_args__ = (
        db.UniqueConstraint('org_id', 'slug', name='uq_page_org_slug'),
        db.Index('ix_page_org_status', 'org_id', 'status'),
    )

    # Path segments the org site claims for itself.
    RESERVED_SLUGS = {
        'manage', 'dashboard', 'admin', 'auth', 'setup', 'static', 'files',
        'themes', 'launcher', 'health', 'posts', 'discussions', 'members',
        'newsletter', 'feed', 'sitemap',
    }

    STATUSES = ('draft', 'published')
    VISIBILITIES = ('public', 'members')

    def validate(self):
        self.title = (self.title or '').strip()
        self.slug = (self.slug or '').strip().lower()
        # Column defaults apply at flush; validate runs before it.
        self.status = self.status or 'draft'
        self.visibility = self.visibility or 'public'
        self.template = self.template or 'page'

        if not self.title:
            raise ValidationError('Title is required')
        if len(self.title) > 200:
            raise ValidationError('Title too long (max 200 chars)')
        if not re.fullmatch(r'[a-z0-9]([a-z0-9-]{0,198})?', self.slug):
            raise ValidationError('Slug must be lowercase letters, numbers, hyphens')
        if self.slug in self.RESERVED_SLUGS:
            raise ValidationError('That slug is reserved')
        if self.status not in self.STATUSES:
            raise ValidationError('Invalid status')
        if self.visibility not in self.VISIBILITIES:
            raise ValidationError('Invalid visibility')

        existing = Page.query.filter_by(slug=self.slug).first()
        if existing and existing.id != self.id:
            raise ValidationError('A page with that slug already exists')

    @property
    def html(self) -> str:
        from app.platform.content import render_markdown
        return render_markdown(self.body)

    @property
    def is_published(self) -> bool:
        return self.status == 'published'

    def publish(self):
        self.status = 'published'
        if self.published_at is None:
            self.published_at = utcnow()
        return self.save()

    def unpublish(self):
        self.status = 'draft'
        return self.save()

    def visible_to_current_visitor(self) -> bool:
        if self.visibility == 'public':
            return True
        from app.platform.authz import is_org_member
        from flask_login import current_user
        return is_org_member() or (
            current_user.is_authenticated and current_user.is_platform_admin)

    @classmethod
    def published_by_slug(cls, slug: str):
        return cls.query.filter_by(slug=(slug or '').strip().lower(),
                                   status='published').first()
