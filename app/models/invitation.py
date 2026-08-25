"""Invitations. Work without outbound email: the admin generates a URL and
sends it through any channel. Only a hash of the token is stored -- the
database must never be a list of workspace-entry credentials."""

import hashlib
import secrets
from datetime import timedelta

from app.extensions import db
from app.platform.errors import ValidationError
from .base import AuditMixin, BaseModel, OrgScoped, utcnow
from .membership import ROLES
from .types import TZDateTime


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Invitation(OrgScoped, AuditMixin, BaseModel):
    __tablename__ = 'invitation'

    email = db.Column(db.String(255), nullable=True)    # informational
    role = db.Column(db.String(20), nullable=False, default='member')
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    expires_at = db.Column(TZDateTime, nullable=False)
    accepted_at = db.Column(TZDateTime, nullable=True)
    accepted_by_id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'),
                               db.ForeignKey('user.id', ondelete='SET NULL'),
                               nullable=True)

    EXPIRY_DAYS = 7

    def validate(self):
        if self.role not in ROLES:
            raise ValidationError('Invalid role')

    @classmethod
    def create(cls, org_id: int, role: str = 'member',
               email: str | None = None) -> tuple['Invitation', str]:
        """Returns (invitation, token). The token is shown once, never stored."""
        token = secrets.token_urlsafe(32)
        invitation = cls(
            org_id=org_id, role=role,
            email=(email or '').strip().lower() or None,
            token_hash=_hash_token(token),
            expires_at=utcnow() + timedelta(days=cls.EXPIRY_DAYS),
        )
        invitation.stamp_audit()
        invitation.save()
        return invitation, token

    @classmethod
    def find_valid(cls, token: str) -> 'Invitation | None':
        invitation = cls.query.filter_by(token_hash=_hash_token(token)).first()
        if invitation is None:
            return None
        if invitation.accepted_at is not None:
            return None
        if utcnow() > invitation.expires_at:
            return None
        return invitation

    @property
    def is_open(self) -> bool:
        return self.accepted_at is None and utcnow() <= self.expires_at

    def accept(self, user) -> 'Invitation':
        """Create the membership. Re-inviting an existing member is a no-op
        on the membership but still consumes the invitation."""
        from .membership import Membership
        Membership.add(user.id, self.org_id, role=self.role)
        self.accepted_at = utcnow()
        self.accepted_by_id = user.id
        return self.save()

    def url(self, token: str) -> str:
        from app.platform.tenant import org_url
        from .organization import Organization
        org = Organization.get_by_id(self.org_id)
        return org_url(org, f'/invite/{token}')
