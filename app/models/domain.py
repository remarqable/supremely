"""Custom domains mapping to organizations.

Not OrgScoped: consulted during tenant resolution, before the tenant is
known. Beyond the blueprint's tenancy pattern (spec Phase 8): a domain -> org
lookup, manual verification by a Platform Admin in MVP, on-demand TLS at the
reverse proxy in hosted operations.
"""

import re

from app.extensions import db

from .base import BaseModel, utcnow
from .types import BigIntFK, TZDateTime

DOMAIN_RE = re.compile(
    r'^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$')


class OrgDomain(BaseModel):
    __tablename__ = 'org_domain'

    org_id = db.Column(BigIntFK,
                       db.ForeignKey('organization.id', ondelete='CASCADE'),
                       nullable=False, index=True)
    domain = db.Column(db.String(253), unique=True, nullable=False, index=True)
    # pending: added by the org, awaiting Platform Admin verification
    status = db.Column(db.String(10), nullable=False, default='pending')
    verified_at = db.Column(TZDateTime, nullable=True)

    organization = db.relationship('Organization', lazy='select')

    STATUSES = ('pending', 'active')

    def validate(self):
        from flask import current_app

        from app.platform.errors import ValidationError
        self.domain = (self.domain or '').strip().lower().rstrip('.')
        self.status = self.status or 'pending'
        if not DOMAIN_RE.match(self.domain):
            raise ValidationError('That does not look like a valid domain')
        base = current_app.config['BASE_DOMAIN'].split(':')[0]
        if self.domain == base or self.domain.endswith('.' + base):
            raise ValidationError(
                'Subdomains of the installation are automatic; add only '
                'external domains here')
        if self.status not in self.STATUSES:
            raise ValidationError('Invalid status')
        existing = OrgDomain.query.filter_by(domain=self.domain).first()
        if existing and existing.id != self.id:
            raise ValidationError('That domain is already claimed')

    @classmethod
    def resolve(cls, host: str):
        """Organization for an active custom domain, or None."""
        row = cls.query.filter_by(domain=(host or '').lower(),
                                  status='active').first()
        return row.organization if row else None

    def activate(self):
        self.status = 'active'
        self.verified_at = utcnow()
        return self.save()
