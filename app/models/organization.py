"""Organization: the tenant. Represents the website/community being operated."""

import re
from typing import ClassVar

from app.extensions import db
from app.platform.errors import ValidationError

from .base import BaseModel, transaction
from .types import JSONColumn, TZDateTime


class Organization(BaseModel):
    __tablename__ = 'organization'

    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(63), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    theme = db.Column(db.String(50), nullable=False, default='origin')
    brand_primary = db.Column(db.String(7), nullable=True)      # #RRGGBB
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    archived_at = db.Column(TZDateTime, nullable=True)
    settings = db.Column(JSONColumn, nullable=False, default=dict)

    memberships = db.relationship('Membership', back_populates='organization',
                                  cascade='all, delete-orphan', lazy='select')

    RESERVED_SLUGS: ClassVar[set[str]] = {
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
    def provision(cls, name: str, slug: str, owner,
                  seed_defaults: bool = True,
                  vertical: str | None = None) -> 'Organization':
        """Create an organization with its owner membership and starter
        content (homepage, About, navigation, first post, General space),
        atomically."""
        from .membership import Membership
        org = cls(name=name, slug=slug)
        org.validate()
        with transaction():
            db.session.add(org)
            db.session.flush()                       # need org.id
            db.session.add(Membership(user_id=owner.id, org_id=org.id, role='owner'))
            if seed_defaults:
                from app.platform.defaults import seed_default_content
                seed_default_content(db.session, org, owner_id=owner.id,
                                     vertical=vertical)
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

    def teases_gated_content(self) -> bool:
        """Tease-don't-hide policy (Manage → Settings → Privacy): when on
        (the default), members-only items appear in public lists as locked
        titles and direct hits land on the gate page. When off, gated
        content is invisible to non-members — hidden from lists, and direct
        URLs behave as before the gate existed (login redirect / 404)."""
        return bool(self.setting('gated_teasers', True))

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
