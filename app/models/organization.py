"""Organization: the tenant. Represents the website/community being operated."""

import re

from app.extensions import db
from app.platform.errors import ValidationError
from .base import BaseModel, transaction
from .types import JSONColumn


class Organization(BaseModel):
    __tablename__ = 'organization'

    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(63), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    theme = db.Column(db.String(50), nullable=False, default='default')
    brand_primary = db.Column(db.String(7), nullable=True)      # #RRGGBB
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    archived_at = db.Column(db.DateTime(timezone=True), nullable=True)
    settings = db.Column(JSONColumn, nullable=False, default=dict)

    memberships = db.relationship('Membership', back_populates='organization',
                                  cascade='all, delete-orphan', lazy='select')

    RESERVED_SLUGS = {
        'www', 'api', 'admin', 'app', 'static', 'mail', 'smtp', 'status',
        'setup', 'auth', 'login', 'logout', 'launcher', 'health', 'files',
        'themes', 'assets', 'blog', 'docs', 'help', 'support',
    }

    def validate(self):
        self.name = (self.name or '').strip()
        self.slug = (self.slug or '').strip().lower()

        if not self.name:
            raise ValidationError('Organization name is required')
        if len(self.name) > 100:
            raise ValidationError('Organization name too long (max 100 chars)')
        if not re.fullmatch(r'[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?', self.slug):
            raise ValidationError('Slug must be 3-63 chars: a-z, 0-9 and hyphens')
        if self.slug in self.RESERVED_SLUGS:
            raise ValidationError('That slug is reserved')
        # Unvalidated tenant input inside a <style> block is CSS injection;
        # Jinja's HTML autoescaping does not protect inside <style>.
        if self.brand_primary and not re.fullmatch(r'#[0-9a-fA-F]{6}',
                                                   self.brand_primary):
            raise ValidationError('Brand colour must be #RRGGBB')

        existing = Organization.query.filter_by(slug=self.slug).first()
        if existing and existing.id != self.id:
            raise ValidationError('That slug is already taken')

    @classmethod
    def get_by_slug(cls, slug: str):
        return cls.query.filter_by(slug=(slug or '').strip().lower()).first()

    @classmethod
    def provision(cls, name: str, slug: str, owner) -> 'Organization':
        """Create an organization with its owner membership, atomically."""
        from .membership import Membership
        org = cls(name=name, slug=slug)
        org.validate()
        with transaction():
            db.session.add(org)
            db.session.flush()                       # need org.id
            db.session.add(Membership(user_id=owner.id, org_id=org.id, role='owner'))
        return org

    def suspend(self):
        self.is_active = False
        return self.save()

    def reactivate(self):
        self.is_active = True
        self.archived_at = None
        return self.save()

    def archive(self):
        from .base import utcnow
        self.is_active = False
        self.archived_at = utcnow()
        return self.save()

    def member_count(self) -> int:
        from .membership import Membership
        return Membership.query.filter_by(org_id=self.id).count()

    def setting(self, key: str, default=None):
        return (self.settings or {}).get(key, default)

    def update_settings(self, **updates) -> 'Organization':
        self.settings = {**(self.settings or {}), **updates}
        return self.save()

    def logo(self):
        from .upload import Upload
        upload_id = self.setting('logo_upload_id')
        return Upload.get_by_id(upload_id) if upload_id else None

    def favicon(self):
        from .upload import Upload
        upload_id = self.setting('favicon_upload_id')
        return Upload.get_by_id(upload_id) if upload_id else None

    def homepage(self):
        from .page import Page
        page_id = self.setting('homepage_page_id')
        if not page_id:
            return None
        page = Page.get_by_id(page_id)
        return page if page and page.is_published else None
