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
