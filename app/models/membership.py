"""Membership links a User to an Organization with a role.

Deliberately NOT OrgScoped: memberships must be readable before the current
tenant is known. See blueprint/patterns/tenancy.md.
"""

from app.extensions import db
from app.platform.errors import ValidationError
from .base import BaseModel
from .types import BigIntFK

ROLES = ('owner', 'admin', 'member')


class Membership(BaseModel):
    __tablename__ = 'membership'

    user_id = db.Column(BigIntFK, db.ForeignKey('user.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    org_id = db.Column(BigIntFK, db.ForeignKey('organization.id', ondelete='CASCADE'),
                       nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False, default='member')

    user = db.relationship('User', back_populates='memberships')
    organization = db.relationship('Organization', back_populates='memberships')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'org_id', name='uq_membership_user_org'),
    )

    def validate(self):
        if self.role not in ROLES:
            raise ValidationError(f'Role must be one of: {", ".join(ROLES)}')

    @classmethod
    def get(cls, user_id: int, org_id: int):
        return cls.query.filter_by(user_id=user_id, org_id=org_id).first()

    @classmethod
    def add(cls, user_id: int, org_id: int, role: str = 'member') -> 'Membership':
        """Add a user to an organization. Re-adding is a no-op."""
        existing = cls.get(user_id, org_id)
        if existing:
            return existing
        return cls(user_id=user_id, org_id=org_id, role=role).save()

    def change_role(self, role: str) -> 'Membership':
        if self.role == 'owner' and role != 'owner':
            self.organization_must_keep_an_owner()
        self.role = role
        return self.save()

    def organization_must_keep_an_owner(self):
        """An organization must always have at least one owner."""
        owners = Membership.query.filter_by(org_id=self.org_id, role='owner').count()
        if owners <= 1:
            raise ValidationError('An organization must keep at least one owner')

    def remove(self):
        if self.role == 'owner':
            self.organization_must_keep_an_owner()
        self.delete()
