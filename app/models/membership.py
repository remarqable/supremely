"""Membership links a User to an Organization with a role.

Deliberately NOT OrgScoped: memberships must be readable before the current
tenant is known. See blueprint/patterns/tenancy.md.
"""

from typing import TYPE_CHECKING

from app.extensions import db
from app.platform.errors import ValidationError

from .base import BaseModel
from .types import BigIntFK

if TYPE_CHECKING:
    from .tier import Tier

ROLES = ('owner', 'admin', 'member')


class Membership(BaseModel):
    __tablename__ = 'membership'

    user_id = db.Column(BigIntFK, db.ForeignKey('user.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    org_id = db.Column(BigIntFK, db.ForeignKey('organization.id', ondelete='CASCADE'),
                       nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False, default='member')
    # What this member may read. Every membership sits on a tier, so the
    # access rule has no null branch and there is no second meaning for
    # "the bottom". Role and tier are independent: one is what you may do,
    # the other what you may see.
    tier_id = db.Column(BigIntFK, db.ForeignKey('tier.id', ondelete='RESTRICT'),
                        nullable=False, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    user = db.relationship('User', back_populates='memberships')
    organization = db.relationship('Organization', back_populates='memberships')
    tier = db.relationship('Tier', lazy='joined')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'org_id', name='uq_membership_user_org'),
    )

    def validate(self):
        if self.role not in ROLES:
            raise ValidationError(f'Role must be one of: {", ".join(ROLES)}')
        self.tier_must_belong_to_this_organization()

    def tier_must_belong_to_this_organization(self):
        """A tier belongs to one organization, and so does a membership.

        Membership is not OrgScoped, deliberately, so the tenant stamp
        never sees this write and nothing else would refuse it. Pointed at
        another organization's tier the row commits happily and then reads
        back with no tier at all, because the loader filter hides it: the
        member silently loses every tier-gated thing, and the invariant
        this column exists to hold quietly stops being true.
        """
        if self.tier_id is None:
            return
        from app.platform.tenant import unscoped

        from .tier import Tier
        with unscoped():
            tier = Tier.query.filter_by(id=self.tier_id).first()
        if tier is None or tier.org_id != self.org_id:
            raise ValidationError('That tier belongs to another organization')

    @classmethod
    def get(cls, user_id: int, org_id: int):
        return cls.query.filter_by(user_id=user_id, org_id=org_id).first()

    @classmethod
    def add(cls, user_id: int, org_id: int, role: str = 'member',
            tier_id: int | None = None) -> 'Membership':
        """Add a user to an organization. Re-adding is a no-op.

        Without a tier the new member lands on the organization's bottom
        one, which is where somebody who has done nothing but join belongs.
        """
        existing = cls.get(user_id, org_id)
        if existing:
            return existing
        from .tier import Tier
        if tier_id is None:
            tier_id = Tier.ensure_default(org_id).id
        return cls(user_id=user_id, org_id=org_id, role=role,
                   tier_id=tier_id).save()

    def set_tier(self, tier: 'Tier') -> 'Membership':
        """Move this member to another tier. An administrator may do this
        to anyone at any time, their own membership included; a tier is not
        a permission and moving one grants nothing but reading."""
        self.tier_id = tier.id
        return self.save()

    @classmethod
    def active_count(cls, org_id: int) -> int:
        return cls.query.filter_by(org_id=org_id, is_active=True).count()

    @classmethod
    def recent_users(cls, org_id: int, limit: int = 5):
        """The newest active members' users, newest first (the community
        right rail's avatar stack)."""
        from .user import User
        return (User.query.join(cls, cls.user_id == User.id)
                .filter(cls.org_id == org_id, cls.is_active.is_(True))
                .order_by(cls.created_at.desc()).limit(limit).all())

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

    def suspend(self):
        if self.role == 'owner':
            self.organization_must_keep_an_owner()
        self.is_active = False
        return self.save()

    def unsuspend(self):
        self.is_active = True
        return self.save()

    def transfer_ownership_to(self, new_owner_membership: 'Membership'):
        """Owner hands the org to another member; the old owner becomes admin."""
        if self.role != 'owner':
            raise ValidationError('Only an owner can transfer ownership')
        if new_owner_membership.org_id != self.org_id:
            raise ValidationError('Membership belongs to a different organization')
        if new_owner_membership.id == self.id:
            # Both writes below land on the same row: owner, then admin.
            # The organization would be left with no owner and no way back.
            raise ValidationError('Ownership is already yours')
        new_owner_membership.role = 'owner'
        new_owner_membership.is_active = True
        self.role = 'admin'
        db.session.commit()
        return self
